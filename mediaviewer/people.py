"""Люди: группировка лиц по отпечаткам SFace, имена, склейка и исправление групп.

Отпечатки лиц считает оценка кадра (quality.py) — там уже читается оригинал снимка с лицами.
faces.person — решение человека: NULL — группа решает сама, id — подтверждено (дали имя группе или склеили),
0 — «это не тот человек» (лицо больше ни в какую группу не попадает).
faces.cluster — группа после последней группировки, faces.who — итог: подтверждённый человек или тот,
кого больше всего среди подтверждённых лиц его группы.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

# Группировка в два шага. Сначала склеиваем почти одинаковые лица (серии, соседние кадры): так групп становится
# в разы меньше лиц. Потом средняя связь: две группы — один человек, если в среднем их лица похожи хотя бы на
# GROUP_MIN. На снимках из поездки так двое главных людей разошлись чисто (276 и 203 лица), а при простом
# «сцеплении» соседей их склеивало в одного уже при пороге 0.5. Лица с одного снимка — всегда разные люди.
TIGHT = 0.7
GROUP_MIN = 0.35
MIN_PHOTOS = 3  # группы без имени меньше этого — случайные прохожие, их не показываем
REC_MIN = 48  # лица меньше (в пикселях снимка, на котором искали) не узнаём: отпечаток выходит случайным


def recluster(lib) -> None:
    with lib.connect() as c:
        rows = c.execute("SELECT id, media_id, emb, person FROM faces WHERE person IS NOT 0").fetchall()
        if rows:
            emb = np.frombuffer(b"".join(r["emb"] for r in rows), dtype=np.float16).reshape(len(rows), -1)
            lab = _cluster(emb.astype(np.float32), np.array([r["media_id"] for r in rows]),
                           np.array([r["person"] or 0 for r in rows]))
            ids = [r["id"] for r in rows]
            # номер группы — наименьший id лица в ней: от группировки к группировке он почти не меняется
            first = {}
            for fid, g in zip(ids, lab):
                first[g] = min(first.get(g, fid), fid)
            c.execute("UPDATE faces SET cluster = NULL")
            c.executemany("UPDATE faces SET cluster = ? WHERE id = ?", [(first[g], fid) for fid, g in zip(ids, lab)])
        resolve(c)


def _cluster(emb: np.ndarray, photo: np.ndarray, person: np.ndarray) -> np.ndarray:
    n = len(emb)
    parent = np.arange(n)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # 1. почти одинаковые лица и лица одного подтверждённого человека
    for i in range(0, n, 2048):
        s = emb[i:i + 2048] @ emb.T
        for r, j in zip(*np.nonzero(s >= TIGHT)):
            a = i + r
            if a < j and photo[a] != photo[j] and (person[a] == person[j] or not person[a] or not person[j]):
                parent[find(a)] = find(j)
    by_person: dict[int, int] = {}
    for i in np.nonzero(person > 0)[0]:
        if person[i] in by_person:
            parent[find(i)] = find(by_person[person[i]])
        else:
            by_person[person[i]] = i
    roots = np.array([find(i) for i in range(n)])
    groups, inv = np.unique(roots, return_inverse=True)
    g = len(groups)
    sums = np.zeros((g, emb.shape[1]), dtype=np.float32)
    np.add.at(sums, inv, emb)
    cnt = np.bincount(inv, minlength=g).astype(np.float32)
    # 2. запреты: общий снимок или разные подтверждённые люди
    ban = np.zeros((g, g), dtype=bool)
    for members in _members(inv, photo).values():
        if len(members) > 1:
            ban[np.ix_(members, members)] = True
    named = {}
    for i in np.nonzero(person > 0)[0]:
        named[inv[i]] = person[i]
    keys = list(named)
    for a in keys:
        for b in keys:
            if named[a] != named[b]:
                ban[a, b] = True
    np.fill_diagonal(ban, True)
    alive = np.ones(g, dtype=bool)
    sim = (sums @ sums.T) / np.outer(cnt, cnt)
    sim[ban] = -np.inf
    best = sim.argmax(1)
    best_val = sim[np.arange(g), best]
    while g > 1:
        a = int(best_val.argmax())
        if best_val[a] < GROUP_MIN:
            break
        b = int(best[a])
        sums[a] += sums[b]
        cnt[a] += cnt[b]
        inv[inv == b] = a
        alive[b] = False
        ban[a] |= ban[b]
        ban[:, a] = ban[a]
        row = (sums @ sums[a]) / (cnt * cnt[a])
        row[ban[a] | ~alive] = -np.inf
        sim[a], sim[:, a] = row, row
        sim[b], sim[:, b] = -np.inf, -np.inf
        best_val[b] = -np.inf
        best[a] = int(row.argmax())
        best_val[a] = row[best[a]]
        for k in np.nonzero(alive & ((best == a) | (best == b)))[0]:  # у них лучшая пара изменилась
            best[k] = int(sim[k].argmax())
            best_val[k] = sim[k, best[k]]
        better = alive & (row > best_val)
        best[better] = a
        best_val[better] = row[better]
    return inv


def _members(inv: np.ndarray, photo: np.ndarray) -> dict[int, list[int]]:
    out: dict[int, set[int]] = {}
    for g, p in zip(inv, photo):
        out.setdefault(int(p), set()).add(int(g))
    return {p: sorted(s) for p, s in out.items()}


def resolve(c) -> None:
    """Пересчитать faces.who после группировки или правок."""
    major: dict[int, int] = {}
    counts: dict[int, dict[int, int]] = {}
    for cl, person in c.execute("SELECT cluster, person FROM faces WHERE person > 0 AND cluster IS NOT NULL"):
        counts.setdefault(cl, {})[person] = counts.setdefault(cl, {}).get(person, 0) + 1
    for cl, d in counts.items():
        major[cl] = max(d, key=d.get)
    c.execute("UPDATE faces SET who = CASE WHEN person > 0 THEN person ELSE NULL END")
    c.executemany("UPDATE faces SET who = ? WHERE cluster = ? AND person IS NULL", [(p, cl) for cl, p in major.items()])
    c.commit()


# ---------------------------------------------------------------- для страницы «Люди»

def groups(lib) -> dict:
    """Люди с именами и группы без имени: ключ, имя, снимки (id), лицо для обложки."""
    with lib.connect() as c:
        names = {r["id"]: r for r in c.execute("SELECT id, name, hidden FROM persons")}
        rows = c.execute("""SELECT f.id, f.media_id, f.who, f.cluster, f.w * f.h AS area FROM faces f
                            JOIN media m ON m.id = f.media_id WHERE f.person IS NOT 0 ORDER BY m.ts DESC""").fetchall()
        total = c.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
    out: dict[str, dict] = {}
    for r in rows:
        if r["who"]:
            p = names.get(r["who"])
            if p is None or p["hidden"]:
                continue
            key = f"p{r['who']}"
            g = out.setdefault(key, {"key": key, "person": r["who"], "name": p["name"], "ids": [], "faces": 0,
                                     "cover": r["id"], "area": 0})
        elif r["cluster"] is not None:
            key = f"c{r['cluster']}"
            g = out.setdefault(key, {"key": key, "person": None, "name": None, "ids": [], "faces": 0,
                                     "cover": r["id"], "area": 0})
        else:
            continue
        g["faces"] += 1
        if not g["ids"] or g["ids"][-1] != r["media_id"]:
            g["ids"].append(r["media_id"])
        if r["area"] > g["area"] * 1.5:  # обложка — одно из крупных лиц, по возможности недавнее
            g["cover"], g["area"] = r["id"], r["area"]
    res = []
    for g in out.values():
        g["ids"] = list(dict.fromkeys(g["ids"]))
        g.pop("area")
        if g["name"] or len(g["ids"]) >= MIN_PHOTOS:
            res.append(g)
    res.sort(key=lambda g: (g["name"] is None, -len(g["ids"])))
    return {"groups": res, "faces": total, "hidden": sum(1 for p in names.values() if p["hidden"])}


def _faces_of(c, key: str) -> list[int]:
    if key.startswith("p"):
        return [r[0] for r in c.execute("SELECT id FROM faces WHERE who = ?", (int(key[1:]),))]
    return [r[0] for r in c.execute("SELECT id FROM faces WHERE cluster = ? AND who IS NULL AND person IS NULL",
                                    (int(key[1:]),))]


def face_list(lib, key: str) -> list[list[int]]:
    """Все лица группы: [[id лица, id снимка], ...] — чтобы убрать чужие."""
    with lib.connect() as c:
        ids = _faces_of(c, key)
        q = ",".join("?" * len(ids)) or "NULL"
        return [list(r) for r in c.execute(f"""SELECT f.id, f.media_id FROM faces f JOIN media m ON m.id = f.media_id
                                               WHERE f.id IN ({q}) ORDER BY m.ts DESC""", ids)]


def set_name(lib, key: str, name: str) -> None:
    """Дать имя группе или переименовать человека. Имя, которое уже есть, — это склейка с тем человеком."""
    name = " ".join(name.split())
    with lib.connect() as c:
        # регистр сравниваем в Python: lower() в SQLite понимает только латиницу
        same = next(((pid,) for pid, n in c.execute("SELECT id, name FROM persons WHERE name IS NOT NULL AND hidden = 0")
                     if n.casefold() == name.casefold()), None)
        if key.startswith("p"):
            pid = int(key[1:])
            if same and same[0] != pid:
                c.execute("UPDATE faces SET person = ? WHERE person = ?", (same[0], pid))
                c.execute("DELETE FROM persons WHERE id = ?", (pid,))
                pid = same[0]
            else:
                c.execute("UPDATE persons SET name = ? WHERE id = ?", (name, pid))
            # лица, которые попали к человеку через группу, теперь подтверждены
            c.execute("UPDATE faces SET person = ? WHERE who = ? AND person IS NULL", (pid, int(key[1:])))
        else:
            pid = same[0] if same else c.execute("INSERT INTO persons(name) VALUES(?)", (name,)).lastrowid
            ids = _faces_of(c, key)
            c.executemany("UPDATE faces SET person = ? WHERE id = ?", [(pid, f) for f in ids])
        c.commit()
    recluster(lib)  # подтверждённые лица тянут за собой похожие группы


def hide(lib, key: str) -> None:
    """Не показывать группу (незнакомые люди, статуи): её лица запоминаются как скрытый человек."""
    with lib.connect() as c:
        if key.startswith("p"):
            c.execute("UPDATE persons SET hidden = 1 WHERE id = ?", (int(key[1:]),))
        else:
            pid = c.execute("INSERT INTO persons(name, hidden) VALUES(NULL, 1)").lastrowid
            c.executemany("UPDATE faces SET person = ? WHERE id = ?", [(pid, f) for f in _faces_of(c, key)])
        c.commit()
    recluster(lib)


def unhide_all(lib) -> None:
    with lib.connect() as c:
        c.execute("UPDATE faces SET person = NULL WHERE person IN (SELECT id FROM persons WHERE hidden = 1 AND name IS NULL)")
        c.execute("DELETE FROM persons WHERE hidden = 1 AND name IS NULL")
        c.execute("UPDATE persons SET hidden = 0")
        c.commit()
    recluster(lib)


def exclude(lib, face_ids: list[int]) -> None:
    """«Это не он»: лица больше не попадают ни в одну группу."""
    with lib.connect() as c:
        c.executemany("UPDATE faces SET person = 0 WHERE id = ?", [(f,) for f in face_ids])
        c.commit()
    recluster(lib)


def face_crop(lib, fid: int, size: int = 160) -> bytes | None:
    """Квадрат с лицом и небольшим запасом — из миниатюры снимка."""
    with lib.connect() as c:
        r = c.execute("SELECT f.x, f.y, f.w, f.h, t.data FROM faces f JOIN thumbs t ON t.id = f.media_id WHERE f.id = ?",
                      (fid,)).fetchone()
    if r is None:
        return None
    img = Image.open(io.BytesIO(r["data"])).convert("RGB")
    w, h = img.size
    cx, cy = (r["x"] + r["w"] / 2) * w, (r["y"] + r["h"] / 2) * h
    side = max(r["w"] * w, r["h"] * h) * 1.5
    img = img.crop((round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2)))
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()

"""Поиск серий (кадры подряд и похожие) и дубликатов (точные копии и почти одинаковые).

Если папка проанализирована на странице «AI», учитывается и смысл снимка: в серию попадают кадры одной сцены,
снятые с другой точки или через минуту, а в дубликаты — копии с другой обрезкой (см. copies.py).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

NEAR_DUP_PHASH = 6
NEAR_DUP_DHASH = 10
AES_FLOOR = 0.2  # см. _series_best


@dataclass
class Settings:
    series_gap: float = 10.0  # макс. пауза между соседними кадрами серии, сек
    series_dist: int = 20  # макс. расстояние pHash (из 64 бит) между соседними кадрами
    series_keep: str = "auto"  # сколько кадров рекомендовать оставить: auto | 1 | 2 | 3


@dataclass
class Result:
    series_id: dict[int, int] = field(default_factory=dict)
    series_best: set[int] = field(default_factory=set)
    dup_id: dict[int, int] = field(default_factory=dict)
    dup_best: set[int] = field(default_factory=set)
    dup_exact: set[int] = field(default_factory=set)
    dup_crop: set[int] = field(default_factory=set)  # копия с другой обрезкой или размером (найдена через copies.py)


def _u64(v: int) -> int:
    return v & 0xFFFFFFFFFFFFFFFF


def hamming(a: int, b: int) -> int:
    return (_u64(a) ^ _u64(b)).bit_count()


class _UF:
    def __init__(self):
        self.p: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _keep_count(n: int, setting: str) -> int:
    if setting == "auto":
        return 1 if n <= 3 else 2
    try:
        return max(1, min(n, int(setting)))
    except ValueError:
        return 1


_COPY_MARK = re.compile(r"\(\d+\)|[ _-]copy\b|копия|[ _-]\d\.\w+$", re.I)


def _quality_key(r: dict):
    name = r["path"].rsplit("/", 1)[-1]
    return ((r["width"] or 0) * (r["height"] or 0), bool(r["make"] or r["model"]), r["size"] or 0,
            not _COPY_MARK.search(name), "dcim/" in r["path"].lower(), -len(r["path"]))


def _series_best(run: list[dict], k: int) -> list[dict]:
    """Какие кадры серии оставить. Без оценки нейросетью — самые резкие.

    С оценкой (страница «AI»): сначала кадры, где ни у кого не закрыты глаза, затем по сумме резкости,
    открытости глаз и красоты кадра — каждое относительно лучшего кадра этой же серии.
    Если лица есть на всех кадрах, важна резкость лиц, а не всего кадра.
    """
    rated = any(r.get("q_faces") is not None or r.get("q_aes") is not None for r in run)
    if not rated:
        return sorted(run, key=lambda r: (r["sharpness"] or 0, _quality_key(r)), reverse=True)[:k]
    sharp_key = "q_face_sharp" if all(r.get("q_face_sharp") for r in run) else "sharpness"
    top_sharp = max(r[sharp_key] or 0 for r in run) or 1
    # Не меньше AES_FLOOR: у неудачных кадров красота 0.01–0.05, и разница 0.011 против 0.013 — шум, а не причина
    top_aes = max(AES_FLOOR, *(r.get("q_aes") or 0 for r in run))

    def key(r):
        eyes = r.get("q_eyes")
        score = (0.45 * (r[sharp_key] or 0) / top_sharp + 0.25 * (r.get("q_aes") or 0) / top_aes
                 + 0.30 * (1.0 if eyes is None else eyes))
        return -(r.get("q_closed") or 0), score, _quality_key(r)

    return sorted(run, key=key, reverse=True)[:k]


def compute(rows: list[dict], s: Settings, emb: dict | None = None, sem: tuple = (),
            copies: list[tuple[int, int]] = ()) -> Result:
    """emb — нормированные векторы смысла снимков (id → вектор), sem — ((пауза, сек, мин. сходство), ...) для серий,
    copies — пары, проверенные попиксельно как одна картинка."""
    res = Result()
    emb = emb or {}

    def same_scene(prev: list[dict], r: dict, gap: float) -> bool:
        b = emb.get(r["id"])
        need = next((sim for limit, sim in sem if gap <= limit), None)
        if b is None or need is None:
            return False
        return any((a := emb.get(x["id"])) is not None and float(a @ b) >= need for x in prev)

    # ---------- серии: идём по времени, соседние кадры с маленькой паузой и похожей картинкой.
    # Точные копии файла в серию не берём (это дубликаты, а не кадры).
    seen_hash: set[str] = set()
    imgs = []
    for r in sorted(rows, key=lambda r: (r["ts"] or 0, len(r["path"]), r["path"])):
        if r["kind"] != "image" or r["category"] == "screenshot" or r["ts"] is None or r["phash"] is None:
            continue
        if r["sha1"]:
            if r["sha1"] in seen_hash:
                continue
            seen_hash.add(r["sha1"])
        imgs.append(r)
    runs: list[list[dict]] = []
    cur: list[dict] = []
    for r in imgs:
        if cur:
            prev = cur[-1]
            gap = r["ts"] - prev["ts"]
            same_cam = not (prev["model"] and r["model"] and prev["model"] != r["model"])
            limit = s.series_dist + (4 if gap <= 2 else 0)  # настоящая серийная съёмка — порог мягче
            similar = gap <= s.series_gap and any(hamming(x["phash"], r["phash"]) <= limit for x in cur[-3:])
            if same_cam and (similar or same_scene(cur[-3:], r, gap)):
                cur.append(r)
                continue
            if len(cur) > 1:
                runs.append(cur)
        cur = [r]
    if len(cur) > 1:
        runs.append(cur)

    for sid, run in enumerate(runs, 1):
        for r in run:
            res.series_id[r["id"]] = sid
        k = _keep_count(len(run), s.series_keep)
        res.series_best.update(r["id"] for r in _series_best(run, k))

    # ---------- дубликаты
    uf = _UF()
    by_hash: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["sha1"]:
            by_hash[r["sha1"]].append(r)
    for group in by_hash.values():
        for r in group[1:]:
            uf.union(group[0]["id"], r["id"])

    known = {r["id"] for r in rows}
    cand = [r for r in rows if r["kind"] == "image" and r["phash"] is not None and r["dhash"] is not None]
    if len(cand) > 1:
        ph = np.array([r["phash"] for r in cand], dtype=np.int64).view(np.uint64)
        dh = np.array([r["dhash"] for r in cand], dtype=np.int64).view(np.uint64)
        for i in range(len(cand) - 1):
            dp = np.bitwise_count(ph[i + 1:] ^ ph[i])
            dd = np.bitwise_count(dh[i + 1:] ^ dh[i])
            sid = res.series_id.get(cand[i]["id"])
            for j in np.nonzero((dp <= NEAR_DUP_PHASH) & (dd <= NEAR_DUP_DHASH))[0]:
                other = cand[i + 1 + j]["id"]
                if sid is not None and res.series_id.get(other) == sid:
                    continue  # похожие кадры одной серии разбираются в режиме серий
                uf.union(cand[i]["id"], other)
    for a, b in copies:  # та же картинка с другой обрезкой или размером
        if a in known and b in known:
            sid = res.series_id.get(a)
            if sid is None or res.series_id.get(b) != sid:
                uf.union(a, b)
                res.dup_crop.update((a, b))

    by_id = {r["id"]: r for r in rows}
    groups: dict[int, list[dict]] = defaultdict(list)
    for x in list(uf.p):
        groups[uf.find(x)].append(by_id[x])
    dup_groups = sorted((g for g in groups.values() if len(g) > 1),
                        key=lambda g: min((r["ts"] or 0) for r in g))
    for gid, g in enumerate(dup_groups, 1):
        exact = len({r["sha1"] for r in g}) == 1 and g[0]["sha1"] is not None
        for r in g:
            res.dup_id[r["id"]] = gid
            if exact:
                res.dup_exact.add(r["id"])
        res.dup_best.add(max(g, key=_quality_key)["id"])
    return res

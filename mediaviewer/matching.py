"""Сверка файлов телефона с облаком (папками на компьютере) и с журналом удалений.

Имена не сравниваются: Яндекс Диск при автозагрузке переименовывает снимки («2024-07-14 12-00-00.jpg»),
а iPhone может отдать HEIC, сконвертированный в JPEG. Поэтому порядок такой:
  1. одинаковое содержимое (SHA-1) — точное совпадение;
  2. та же дата съёмки (EXIF, с долями секунды) + та же камера + похожая картинка (pHash);
  3. для файлов без даты (скриншоты) — тот же размер кадра + почти одинаковая картинка.
"""
from __future__ import annotations

import bisect
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .grouping import hamming

TIME_TOL = 1.0  # сек: разные программы по-разному округляют доли секунды
VIDEO_TIME_TOL = 2.0
PHASH_SAME_SHOT = 14  # один и тот же кадр после конвертации HEIC->JPEG / пересжатия
PHASH_NO_DATE = 5
DHASH_NO_DATE = 8


@dataclass
class Ref:
    group: str  # "cloud" — файл есть в облаке, "deleted" — был удалён (журнал)
    label: str  # где найден: путь в облаке
    kind: str
    ts: float | None
    ts_source: str | None
    model: str | None
    width: int | None
    height: int | None
    duration: float | None
    size: int | None
    sha1: str | None
    phash: int | None
    dhash: int | None
    category: str | None = None
    src: tuple = ()  # откуда взять миниатюру: ("lib", путь к index.db, id) или ("journal", id)


def library_roots(data_dir: Path) -> list[dict]:
    """Все папки, которые открывались в программе (кандидаты в «облако»)."""
    out = []
    base = data_dir / "libraries"
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        db, root_txt = d / "index.db", d / "root.txt"
        if not db.exists() or not root_txt.exists():
            continue
        root = root_txt.read_text(encoding="utf-8").strip()
        try:
            with sqlite3.connect(db) as c:
                n = c.execute("SELECT COUNT(*) FROM media WHERE parent_id IS NULL").fetchone()[0]
        except sqlite3.Error:
            continue
        out.append({"root": root, "db": str(db), "count": n, "exists": Path(root).exists()})
    return out


def load_refs(data_dir: Path, roots: set[str] | None, journal_rows: list[dict]) -> list[Ref]:
    refs: list[Ref] = []
    for lib in library_roots(data_dir):
        if roots is not None and lib["root"] not in roots:
            continue
        with sqlite3.connect(lib["db"]) as c:
            c.row_factory = sqlite3.Row
            for r in c.execute(
                    """SELECT id, path, kind, ts, ts_source, model, width, height, duration, size, sha1, phash, dhash,
                       category FROM media WHERE parent_id IS NULL AND analyzed=1"""):
                refs.append(Ref("cloud", f"{lib['root']}\\{r['path'].replace('/', chr(92))}", r["kind"], r["ts"],
                                r["ts_source"], r["model"], r["width"], r["height"], r["duration"], r["size"],
                                r["sha1"], r["phash"], r["dhash"], r["category"], ("lib", lib["db"], r["id"])))
    for r in journal_rows:
        refs.append(Ref("deleted", f"{r['source']}\\{(r['path'] or '').replace('/', chr(92))}", r["kind"], r["ts"],
                        r["ts_source"], r["model"], r["width"], r["height"], r["duration"], r["size"], r["sha1"],
                        r["phash"], r["dhash"], None, ("journal", r["id"])))
    return refs


def _same_shot(item: dict, ref: Ref) -> bool:
    if item["kind"] != ref.kind:
        return False
    if item["model"] and ref.model and item["model"].strip().lower() != ref.model.strip().lower():
        return False
    if item["kind"] == "video":
        if item["duration"] and ref.duration and abs(item["duration"] - ref.duration) > 1.0:
            return False
        if item["phash"] is not None and ref.phash is not None and hamming(item["phash"], ref.phash) > PHASH_SAME_SHOT + 6:
            return False
        return True
    if item["phash"] is None or ref.phash is None:
        return bool(item["size"] and item["size"] == ref.size)
    return hamming(item["phash"], ref.phash) <= PHASH_SAME_SHOT


def match(items: list[dict], refs: list[Ref]) -> dict[int, tuple[str, str, bool]]:
    """items: строки индекса телефона. Возвращает {id: (группа, где найден, надёжно)}; «cloud» важнее «deleted».

    Ненадёжное совпадение (только похожая картинка того же размера, без даты) показывается, но не помечается
    к удалению автоматически."""
    by_sha: dict[str, list[Ref]] = {}
    by_size: dict[int, list[Ref]] = {}
    by_dims: dict[tuple[int, int], list[Ref]] = {}
    timed = sorted((r for r in refs if r.ts is not None and r.ts_source != "mtime"), key=lambda r: r.ts)
    times = [r.ts for r in timed]
    for r in refs:
        if r.sha1:
            by_sha.setdefault(r.sha1, []).append(r)
        if r.size:
            by_size.setdefault(r.size, []).append(r)
        if r.width and r.height and r.phash is not None:
            by_dims.setdefault((r.width, r.height), []).append(r)

    def pick(cands: list[Ref], strong: bool) -> tuple[str, str, bool] | None:
        if not cands:
            return None
        best = next((c for c in cands if c.group == "cloud"), cands[0])
        return best.group, best.label, strong

    out: dict[int, tuple[str, str, bool]] = {}
    for it in items:
        found: list[Ref] = []
        strong = True
        if it.get("sha1") and it["sha1"] in by_sha:
            found = by_sha[it["sha1"]]
        if not found and it["ts"] is not None and it.get("ts_source") != "mtime":
            tol = VIDEO_TIME_TOL if it["kind"] == "video" else TIME_TOL
            lo, hi = bisect.bisect_left(times, it["ts"] - tol), bisect.bisect_right(times, it["ts"] + tol)
            found = [r for r in timed[lo:hi] if _same_shot(it, r)]
        if not found and it["size"]:
            # тот же размер файла + та же картинка (на случай пустого EXIF)
            found = [r for r in by_size.get(it["size"], []) if r.kind == it["kind"] and
                     (it["phash"] is None or r.phash is None or hamming(it["phash"], r.phash) <= PHASH_NO_DATE)]
        if not found and it["phash"] is not None and it["width"] and it["height"]:
            strong = False
            dims = by_dims.get((it["width"], it["height"]), []) + by_dims.get((it["height"], it["width"]), [])
            found = [r for r in dims if r.kind == it["kind"] and hamming(it["phash"], r.phash) <= PHASH_NO_DATE
                     and (it["dhash"] is None or r.dhash is None or hamming(it["dhash"], r.dhash) <= DHASH_NO_DATE)]
        res = pick(found, strong)
        if res:
            out[it["id"]] = res
    return out


def dates_compatible(a: float | None, b: float | None) -> bool:
    """Одна и та же съёмка, если время совпадает с точностью до сдвига часового пояса (кратного 15 мин)."""
    if a is None or b is None:
        return True
    d = abs(a - b)
    return d <= 26 * 3600 and min(d % 900, 900 - d % 900) <= 2


def quick_match(size: int, taken: float | None, refs_by_size: dict[int, list[Ref]]) -> Ref | None:
    """Сверка без копирования файла: по размеру из списка файлов телефона и дате съёмки.

    Размер многомегабайтного файла сам по себе почти уникален; дата нужна для подстраховки.
    Часовой пояс у даты из телефона может отличаться от EXIF, поэтому допускается сдвиг на целое число
    четвертей часа (так устроены все часовые пояса), а минуты/секунды должны совпасть.
    """
    cands = refs_by_size.get(size) or []
    if not cands or size < 20_000:
        return None
    best = None
    for r in cands:
        if r.ts_source != "mtime" and not dates_compatible(taken, r.ts):
            continue
        if best is None or (r.group == "cloud" and best.group != "cloud"):
            best = r
    return best


def ref_thumb(ref: Ref, journal) -> bytes | None:
    if not ref.src:
        return None
    if ref.src[0] == "journal":
        return journal.thumb(ref.src[1])
    with sqlite3.connect(ref.src[1]) as c:
        r = c.execute("SELECT data FROM thumbs WHERE id=?", (ref.src[2],)).fetchone()
        return r[0] if r else None

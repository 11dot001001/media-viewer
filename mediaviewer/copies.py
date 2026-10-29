"""Копии одной картинки с другой обрезкой, размером или фильтром: версия из мессенджера, обрезка в редакторе.

Отпечаток pHash такие копии не узнаёт: после обрезки картинка «сдвигается». Поэтому кандидаты берутся по смыслу
(эмбеддинги страницы «AI»), а затем проверяются попиксельно: меньший снимок ищется внутри большего на разных
масштабах, и в найденном месте сравниваются мелкие детали. У копии они совпадают, а у второго кадра той же сцены
(даже снятого через секунду со штатива) расходятся: шевельнулись люди, листья, волны, сдвинулась камера.
"""
from __future__ import annotations

import io
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from PIL import Image

COARSE = 96  # грубый поиск положения: миниатюра до 96 px
SCALES = np.geomspace(1.0, 0.4, 12)  # меньший снимок может быть обрезкой до 40% ширины большего
# Совпадение мелких деталей: у копий ≥ 0.6, у разных кадров одной сцены обычно < 0.4.
# Если у одного снимка нет модели камеры (пересохранён из мессенджера или редактора), хватает 0.4:
# сильное сжатие стирает часть деталей, а среди таких пар при этом пороге попадались только копии.
STRONG = 0.6
WEAK = 0.4
BLOCK = 1024
CHUNK = 32  # пар на одну задачу процесса


def candidates(ids: np.ndarray, mat: np.ndarray, meta: dict, min_sim: float) -> list[tuple[int, int]]:
    """Пары снимков, очень похожих по смыслу, но разного размера (или у одного нет камеры — пересохранён).

    Одинаковый размер и камера у обоих — не наш случай: такие копии (и пересжатые, и с фильтром) находит pHash,
    а здесь они дают ложные пары вроде двух снимков одного экрана, у которых совпадает вся графика интерфейса.
    meta: id → (ширина, высота, камера, группа дубликатов).
    """
    out = []
    for s in range(0, len(ids), BLOCK):
        sims = mat[s:s + BLOCK] @ mat[s:].T
        for i, j in zip(*np.nonzero(sims >= min_sim)):
            a, b = int(ids[s + i]), int(ids[s + j])
            if j <= i or a not in meta or b not in meta:
                continue
            (wa, ha, ca, da), (wb, hb, cb, db) = meta[a], meta[b]
            if da is not None and da == db:
                continue  # уже дубликаты
            if {wa, ha} == {wb, hb} and ca and cb:
                continue
            out.append((a, b))
    return out


def run(lib, ids: np.ndarray, mat: np.ndarray, min_sim: float, status: dict, cancel) -> int:
    """Проверяет новых кандидатов попиксельно и запоминает результат (в том числе «не копия»).

    Возвращает, сколько найдено новых копий. status — прогресс задачи, в которой идёт поиск (сканирование или анализ).
    """
    with lib.connect() as c:
        meta = {r[0]: (r[1], r[2], r[3] or "", r[4]) for r in c.execute(
            """SELECT id, width, height, model, dup_id FROM media
               WHERE parent_id IS NULL AND kind = 'image' AND width IS NOT NULL""")}
        mtime = dict(c.execute("SELECT id, mtime FROM media WHERE parent_id IS NULL").fetchall())
        done = {(r[0], r[1]) for r in c.execute("SELECT a, b, ma, mb FROM lookalike")
                if mtime.get(r[0]) == r[2] and mtime.get(r[1]) == r[3]}
    todo = [p for p in candidates(ids, mat, meta, min_sim) if p not in done]
    if not todo:
        return 0
    status.update(phase="copies", total=len(todo), done=0, message=f"Поиск копий с другой обрезкой: 0 из {len(todo)}")

    # БПФ в numpy не отпускает GIL, поэтому параллельно — процессами, а не потоками
    workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    step = CHUNK * workers
    found = 0
    with ProcessPoolExecutor(workers) as pool, lib.connect() as c:
        for k in range(0, len(todo), step):
            if cancel.is_set():
                break
            part = todo[k:k + step]
            ids_ = {i for pair in part for i in pair}
            q = ",".join("?" * len(ids_))
            data = dict(c.execute(f"SELECT id, data FROM thumbs WHERE id IN ({q})", list(ids_)).fetchall())
            jobs = [[(data.get(a), data.get(b)) for a, b in part[i:i + CHUNK]] for i in range(0, len(part), CHUNK)]
            scores = [sc for res in pool.map(_score_many, jobs) for sc in res]
            c.executemany("INSERT OR REPLACE INTO lookalike(a, b, ma, mb, score) VALUES(?, ?, ?, ?, ?)",
                          [(a, b, mtime.get(a), mtime.get(b), sc) for (a, b), sc in zip(part, scores)])
            c.commit()
            found += sum(sc >= WEAK for sc in scores)
            n = k + len(part)
            status.update(done=n, message=f"Поиск копий с другой обрезкой: {n} из {len(todo)}")
    return found


def verified(c) -> list[tuple[int, int]]:
    """Пары, признанные одной картинкой (для группировки дубликатов)."""
    return [(r[0], r[1]) for r in c.execute(
        """SELECT l.a, l.b FROM lookalike l JOIN media x ON x.id = l.a AND x.mtime IS l.ma
           JOIN media y ON y.id = l.b AND y.mtime IS l.mb
           WHERE l.score >= ? OR (l.score >= ? AND (COALESCE(x.model, '') = '' OR COALESCE(y.model, '') = ''))""",
        (STRONG, WEAK))]


# ---------------------------------------------------------------- попиксельная проверка

def _score_many(pairs: list[tuple[bytes | None, bytes | None]]) -> list[float]:
    out = []
    for a, b in pairs:
        try:
            out.append(score(a, b) if a and b else -1.0)
        except Exception:  # noqa: BLE001 — битая миниатюра: считаем, что не копия
            out.append(-1.0)
    return out


def score(a_bytes: bytes, b_bytes: bytes) -> float:
    """Насколько совпадают мелкие детали, если меньший снимок наложить на нужное место большего (−1…1)."""
    a = Image.open(io.BytesIO(a_bytes)).convert("L")
    b = Image.open(io.BytesIO(b_bytes)).convert("L")
    sa, sb = a.copy(), b.copy()
    sa.thumbnail((COARSE, COARSE))
    sb.thumbnail((COARSE, COARSE))
    ca = _place(np.asarray(sa, np.float32), sb)
    cb = _place(np.asarray(sb, np.float32), sa)
    big, small, place = (a, b, ca) if ca[0] >= cb[0] else (b, a, cb)
    if place[0] < 0.8:
        return -1.0  # даже в целом картинки не совпадают
    return _details(big, small, place)


def _place(big: np.ndarray, small: Image.Image) -> tuple[float, float, float, float]:
    """Где меньший снимок лучше всего совпадает с большим: (корреляция, масштаб, x, y) в долях большего."""
    H, W = big.shape
    frame = _Frame(big)  # спектр большего один на все масштабы
    best = (-1.0, 0.0, 0.0, 0.0)
    for s in SCALES:
        wh = _fit(W, H, small.size, s)
        if wh is None or min(wh) < 20:
            continue
        m = frame.ncc(np.asarray(small.resize(wh, Image.Resampling.BILINEAR), np.float32))
        k = int(m.argmax())
        if m.flat[k] > best[0]:
            y, x = divmod(k, m.shape[1])
            best = (float(m.flat[k]), wh[0] / W, x / W, y / H)
    return best


def _details(big_img: Image.Image, small_img: Image.Image, place) -> float:
    """Корреляция мелких деталей на полной миниатюре около найденного места (масштаб уточняется ±4%)."""
    _, s0, x0, y0 = place
    big = _high_pass(np.asarray(big_img, np.float32))
    H, W = big.shape
    best, r = -1.0, 8
    for s in s0 * np.array([0.96, 0.98, 1.0, 1.02, 1.04]):
        wh = _fit(W, H, small_img.size, s)
        if wh is None or min(wh) < 32:
            continue
        w, h = wh
        cx, cy = round(x0 * W), round(y0 * H)
        xa, ya, xb, yb = max(0, cx - r), max(0, cy - r), min(W, cx + w + r), min(H, cy + h + r)
        if xb - xa < w or yb - ya < h:
            continue
        small = _high_pass(np.asarray(small_img.resize(wh, Image.Resampling.BILINEAR), np.float32))
        best = max(best, float(_Frame(big[ya:yb, xa:xb]).ncc(small).max()))
    return best


def _fit(W: int, H: int, size: tuple[int, int], s: float) -> tuple[int, int] | None:
    sw, sh = size
    w = round(W * s)
    h = round(w * sh / sw)
    if h > H:
        h = round(H * s)
        w = round(h * sw / sh)
    return (w, h) if w <= W and h <= H else None


def _box(a: np.ndarray, r: int) -> np.ndarray:
    k = 2 * r + 1
    p = np.pad(a, r + 1, mode="edge")[:a.shape[0] + k, :a.shape[1] + k]
    s = p.cumsum(0).cumsum(1)
    return (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) / (k * k)


def _high_pass(a: np.ndarray) -> np.ndarray:
    """Картинка минус размытая: остаются мелкие детали, а цвет, яркость и фильтры уходят."""
    return a - _box(_box(a, 2), 2)


def _fast_len(n: int) -> int:
    """Ближайшая сверху длина из множителей 2, 3, 5: на таких БПФ в разы быстрее."""
    while True:
        m = n
        for f in (2, 3, 5):
            while m % f == 0:
                m //= f
        if m == 1:
            return n
        n += 1


class _Frame:
    """Больший снимок, подготовленный для поиска в нём меньших (спектр и суммы считаются один раз)."""

    def __init__(self, big: np.ndarray):
        self.big = big
        H, W = big.shape
        # Корреляция по БПФ круговая, но для сдвигов, при которых меньший снимок целиком внутри большего,
        # «заворота» нет: хватает размера самого большего снимка, без дополнения нулями.
        self.shape = (_fast_len(H), _fast_len(W))
        self.spec = np.fft.rfft2(big, self.shape)
        self.i1 = np.pad(big, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        self.i2 = np.pad(big * big, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def ncc(self, small: np.ndarray) -> np.ndarray:
        """Нормированная корреляция small со всеми положениями внутри большего снимка."""
        H, W = self.big.shape
        h, w = small.shape
        t = small - small.mean()
        tn = float(np.sqrt((t * t).sum()))
        if tn < 1e-3:
            return np.full((1, 1), -1.0)  # однотонный снимок: сравнивать нечего
        num = np.fft.irfft2(self.spec * np.conj(np.fft.rfft2(t, self.shape)), self.shape)[:H - h + 1, :W - w + 1]

        def box(s):
            return s[h:, w:] - s[:-h, w:] - s[h:, :-w] + s[:-h, :-w]

        var = box(self.i2) - box(self.i1) ** 2 / (h * w)
        return num / (np.sqrt(np.maximum(var, 1e-3)) * tn)

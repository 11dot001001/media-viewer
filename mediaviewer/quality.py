"""Оценка кадра: закрытые глаза и резкость лиц. Вместе с красотой кадра помогает выбрать ★ в серии.
Заодно для каждого лица считается отпечаток (SFace) — по ним страница «Люди» группирует лица (people.py).

Лица ищутся на миниатюре. Только если они есть, открывается оригинал (до 1600 px): на миниатюре глаз не разглядеть.
Не скачанные из облака файлы и снимки с телефона оцениваются по миниатюре.
"""
from __future__ import annotations

import io
import os
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import numpy as np
from PIL import Image

from . import media, people
from .faces import MIN_FACE

VERSION = 3  # при изменении способа оценки — поднять, и снимки оценятся заново (3 — отпечатки лиц)
# Отношение высоты глаза к ширине (EAR) у более открытого глаза. Подобрано по снимкам из поездки:
# открытые глаза ≈ 0.22–0.30, прищур и взгляд вниз ≈ 0.10–0.15, закрытые — меньше 0.10.
CLOSED_EAR = 0.11
OPEN_EAR = 0.20
ORIGINAL_SIDE = 1600
MAX_FACES = 8
MAIN_FACE = 0.4  # лица меньше этой доли от самого крупного — прохожие на фоне, их глаза не важны
# Почти всё время уходит на распаковку оригиналов HEIC (~150 мс), а не на модели (~40 мс). libheif отпускает GIL,
# поэтому потоков много: на 32 ядрах 16 потоков распаковывают в 1,7 раза быстрее, чем 6, дальше прироста нет.
WORKERS = max(4, min(16, os.cpu_count() or 4))
BATCH = 48


class Rater:
    """Фоновая оценка всех фото открытой папки."""

    def __init__(self, lib, manager):
        self.lib = lib
        self.manager = manager
        self.status = {"phase": "idle", "done": 0, "total": 0, "message": "", "started": 0}
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run_safe, daemon=True, name="quality")
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run_safe(self) -> None:
        self.status = {"phase": "load", "done": 0, "total": 0, "message": "Загрузка модели…", "started": time.time()}
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.status.update(phase="error", message=f"Ошибка оценки: {e}"[:400])
            return
        self.status.update(phase="stopped" if self._cancel.is_set() else "done",
                           message="Остановлено" if self._cancel.is_set() else "Готово")
        self.lib.regroup()  # ★ в сериях выбираются с учётом глаз
        if self.manager.engine().rec is not None:
            self.status["message"] = "Группировка лиц…"
            people.recluster(self.lib)
            self.status["message"] = "Остановлено" if self._cancel.is_set() else "Готово"
            self.lib.status["version"] += 1

    def _run(self) -> None:
        fm = self.manager.engine()
        with self.lib.connect() as c:
            c.execute("DELETE FROM quality WHERE id NOT IN (SELECT id FROM media WHERE parent_id IS NULL)")
            c.execute("DELETE FROM faces WHERE media_id NOT IN (SELECT id FROM media WHERE parent_id IS NULL)")
            # Сначала кадры серий: там оценка нужнее всего
            todo = [tuple(r) for r in c.execute(
                """SELECT m.id, m.path, m.ext, m.mtime, m.cloud FROM media m JOIN thumbs t ON t.id = m.id
                   LEFT JOIN quality q ON q.id = m.id
                   WHERE m.parent_id IS NULL AND m.kind = 'image'
                     AND (q.id IS NULL OR q.version != ? OR q.mtime IS NOT m.mtime)
                   ORDER BY m.series_id IS NULL, m.ts DESC""", (VERSION,))]
        self.status.update(phase="analyze", total=len(todo), done=0, message="Оценка кадров")
        pool = ThreadPoolExecutor(WORKERS)
        pending: set = set()
        try:
            with self.lib.connect() as c:
                for i in range(0, len(todo), BATCH):
                    part = todo[i:i + BATCH]
                    q = ",".join("?" * len(part))
                    thumbs = dict(c.execute(f"SELECT id, data FROM thumbs WHERE id IN ({q})", [t[0] for t in part]))
                    pending |= {pool.submit(self._rate, fm, t, thumbs.get(t[0])) for t in part}
                    # Не ждём всю пачку (иначе потоки простаивают из-за самого долгого снимка): пока в работе
                    # больше пачки, сохраняем то, что уже готово
                    while len(pending) > BATCH:
                        finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                        self._save(c, finished)
                    if self._cancel.is_set():
                        return
                self._save(c, wait(pending)[0])
                pending = set()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _save(self, c, finished) -> None:
        done = [(t, r) for t, r in (f.result() for f in finished) if r is not None]
        c.executemany("""INSERT OR REPLACE INTO quality(id, version, mtime, faces, closed, eyes, face_sharp)
                         VALUES(?, ?, ?, ?, ?, ?, ?)""", [(t[0], VERSION, t[3], *r[0]) for t, r in done])
        for t, (_, found) in done:
            if found is not None:
                _save_faces(c, t[0], found)
        c.commit()
        self.status["done"] += len(finished)

    def _rate(self, fm, item: tuple, thumb: bytes | None) -> tuple[tuple, tuple | None]:
        return item, self._rate_one(fm, item, thumb)

    def _rate_one(self, fm, item: tuple, thumb: bytes | None) -> tuple[tuple, list | None] | None:
        """(faces, closed, eyes, face_sharp) и лица для «Людей»: [(x, y, w, h в долях кадра, отпечаток)]."""
        mid, rel, ext, _, cloud = item
        if not thumb:
            return None
        try:
            img = Image.open(io.BytesIO(thumb)).convert("RGB")
            found = fm.detect(img)
            if found and self.lib.kind == "folder" and not cloud:
                path = self.lib.local_path(rel)
                if path.exists():
                    img, _, _ = media.open_oriented(path, ext, ORIGINAL_SIDE, fast=True)
                    found = fm.detect(img)
        except Exception:  # noqa: BLE001 — файл не читается: оценим в следующий раз
            traceback.print_exc()
            return None
        min_face = MIN_FACE * max(img.size) / ORIGINAL_SIDE  # на миниатюре порог меньше
        found = sorted((f for f in found if max(f["box"][2:]) >= min_face), key=lambda f: -max(f["box"][2:]))
        found = [f for f in found if max(f["box"][2:]) >= MAIN_FACE * max(found[0]["box"][2:])]
        checked = [(f, fm.eyes(img, f)) for f in found[:MAX_FACES]]
        checked = [(f, e) for f, e in checked if e is not None]  # Face Mesh не нашёл лица — ложное срабатывание
        known = None
        if fm.rec is not None:
            w, h = img.size
            known = [(f["box"][0] / w, f["box"][1] / h, f["box"][2] / w, f["box"][3] / h, fm.embed(img, f))
                     for f, _ in checked if max(f["box"][2:]) >= people.REC_MIN]
        if not checked:
            return (0, 0, None, None), known
        opened = [max(e["ear"]) for _, e in checked]
        return ((len(checked), sum(o < CLOSED_EAR for o in opened), float(np.mean([min(1.0, o / OPEN_EAR) for o in opened])),
                 float(np.mean([e["sharp"] for _, e in checked]))), known)


def _save_faces(c, mid: int, found: list) -> None:
    """Заменить лица снимка. Решения человека («это Маша», «это не она») переходят к тем же лицам на новом месте."""
    old = c.execute("SELECT x, y, w, h, person FROM faces WHERE media_id = ? AND person IS NOT NULL", (mid,)).fetchall()
    c.execute("DELETE FROM faces WHERE media_id = ?", (mid,))
    for x, y, w, h, emb in found:
        person = next((o[4] for o in old if _iou((x, y, w, h), o[:4]) > 0.5), None)
        c.execute("INSERT INTO faces(media_id, x, y, w, h, emb, person) VALUES(?, ?, ?, ?, ?, ?, ?)",
                  (mid, x, y, w, h, emb.astype(np.float16).tobytes(), person))


def _iou(a, b) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / max(a[2] * a[3] + b[2] * b[3] - inter, 1e-9)


def counts(lib) -> dict:
    with lib.connect() as c:
        total = c.execute("""SELECT COUNT(*) FROM media m JOIN thumbs t ON t.id = m.id
                             WHERE m.parent_id IS NULL AND m.kind = 'image'""").fetchone()[0]
        done, faces, closed = c.execute(
            """SELECT COUNT(*), SUM(q.faces > 0), SUM(q.closed > 0) FROM quality q JOIN media m ON m.id = q.id
               WHERE q.version = ? AND q.mtime IS m.mtime""", (VERSION,)).fetchone()
    return {"total": total, "done": done, "with_faces": faces or 0, "closed": closed or 0}

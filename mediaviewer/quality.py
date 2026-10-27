"""Оценка кадра: закрытые глаза и резкость лиц. Вместе с красотой кадра (SigLIP) помогает выбрать ★ в серии.

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

from . import media
from .faces import MIN_FACE

VERSION = 2  # при изменении способа оценки — поднять, и снимки оценятся заново
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

    def _run(self) -> None:
        fm = self.manager.engine()
        with self.lib.connect() as c:
            c.execute("DELETE FROM quality WHERE id NOT IN (SELECT id FROM media WHERE parent_id IS NULL)")
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
        rows = [(t[0], VERSION, t[3], *r) for t, r in (f.result() for f in finished) if r is not None]
        c.executemany("""INSERT OR REPLACE INTO quality(id, version, mtime, faces, closed, eyes, face_sharp)
                         VALUES(?, ?, ?, ?, ?, ?, ?)""", rows)
        c.commit()
        self.status["done"] += len(finished)

    def _rate(self, fm, item: tuple, thumb: bytes | None) -> tuple[tuple, tuple | None]:
        return item, self._rate_one(fm, item, thumb)

    def _rate_one(self, fm, item: tuple, thumb: bytes | None) -> tuple | None:
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
        eyes = [e for e in (fm.eyes(img, f) for f in found[:MAX_FACES]) if e is not None]
        if not eyes:
            return 0, 0, None, None
        opened = [max(e["ear"]) for e in eyes]
        return (len(eyes), sum(o < CLOSED_EAR for o in opened),
                float(np.mean([min(1.0, o / OPEN_EAR) for o in opened])), float(np.mean([e["sharp"] for e in eyes])))


def counts(lib) -> dict:
    with lib.connect() as c:
        total = c.execute("""SELECT COUNT(*) FROM media m JOIN thumbs t ON t.id = m.id
                             WHERE m.parent_id IS NULL AND m.kind = 'image'""").fetchone()[0]
        done, faces, closed = c.execute(
            """SELECT COUNT(*), SUM(q.faces > 0), SUM(q.closed > 0) FROM quality q JOIN media m ON m.id = q.id
               WHERE q.version = ? AND q.mtime IS m.mtime""", (VERSION,)).fetchone()
    return {"total": total, "done": done, "with_faces": faces or 0, "closed": closed or 0}

"""Текст на снимках: встроенное распознавание Windows (Windows.Media.Ocr) и поиск по нему.

Модели качать не нужно — Windows распознаёт языки, для которых установлен языковой пакет (русский движок читает
и латиницу). Нужны только маленькие пакеты winrt для Python, они ставятся кнопкой со страницы «AI».
Текст читается с оригинала (до 2000 px): на миниатюре мелкие буквы не разобрать. Не скачанные из облака файлы
и снимки с телефона пропускаются.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import re
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from PIL import Image

from . import ai, media

VERSION = 1
SIDE = 2000  # скриншот телефона 1170×2532 уменьшается не сильнее чем в 1,3 раза — буквы ещё читаются
# Распознавание идёт ~60 мс на снимок, у каждого потока свой движок (один движок не читает два снимка разом).
# Почти всё остальное время — распаковка HEIC, она тоже идёт в потоках.
WORKERS = 12
BATCH = 48
PACKAGES = ("winrt-runtime", "winrt-Windows.Media.Ocr", "winrt-Windows.Graphics.Imaging", "winrt-Windows.Foundation",
            "winrt-Windows.Foundation.Collections", "winrt-Windows.Globalization", "winrt-Windows.Storage.Streams")
LANGS = ("ru", "en-US", "en")  # каким движком читать, если их несколько


class OcrManager(ai.Manager):
    """Только пакеты winrt: модель встроена в Windows."""

    def __init__(self, data_dir):
        super().__init__(data_dir, "ocr", {}, 2 * 1024 ** 2, lambda d: Engine())

    def packages_ok(self) -> bool:
        return sys.platform == "win32" and importlib.util.find_spec("winrt.windows.media.ocr") is not None

    def model_ok(self) -> bool:
        return True  # модель — часть Windows

    def _download(self) -> None:
        pass

    def _install_packages(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Распознавание текста есть только в Windows")
        self.job = {"phase": "packages", "done": 0, "total": 0, "message": "Установка пакетов winrt…"}
        ai.pip(sys.executable, *PACKAGES)
        importlib.invalidate_caches()
        if not self.packages_ok():
            raise RuntimeError("pip не смог установить пакеты winrt")

    def info(self) -> dict:
        res = super().info()
        res["supported"] = sys.platform == "win32"
        res["languages"] = languages() if self.packages_ok() else []
        return res


def languages() -> list[str]:
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
    except Exception:  # noqa: BLE001
        return []


class Engine:
    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        have = languages()
        self.lang = next((lang for lang in LANGS if lang in have), have[0] if have else None)
        if self.lang is None:
            raise RuntimeError("В Windows не установлено ни одного языка для распознавания текста")
        self._make = lambda: OcrEngine.try_create_from_language(Language(self.lang))
        self._local = threading.local()
        self.device = f"Windows OCR ({self.lang})"

    def read(self, img: Image.Image) -> str:
        """Текст снимка: строки через перевод строки (пусто, если текста нет)."""
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap

        eng = getattr(self._local, "eng", None)
        if eng is None:
            eng = self._local.eng = self._make()
        b, g, r = img.convert("RGB").split()
        data = Image.merge("RGBA", (b, g, r, Image.new("L", img.size, 255))).tobytes()
        bmp = SoftwareBitmap.create_copy_from_buffer(data, BitmapPixelFormat.BGRA8, img.width, img.height,
                                                     BitmapAlphaMode.PREMULTIPLIED)

        async def run():
            return await eng.recognize_async(bmp)

        return "\n".join(line.text for line in asyncio.run(run()).lines)


class Reader:
    """Фоновое чтение текста со всех фото открытой папки."""

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
        self._thread = threading.Thread(target=self._run_safe, daemon=True, name="ocr")
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run_safe(self) -> None:
        self.status = {"phase": "load", "done": 0, "total": 0, "message": "Подготовка…", "started": time.time()}
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.status.update(phase="error", message=f"Ошибка распознавания: {e}"[:400])
            return
        self.status.update(phase="stopped" if self._cancel.is_set() else "done",
                           message="Остановлено" if self._cancel.is_set() else "Готово")

    def _run(self) -> None:
        eng = self.manager.engine()
        with self.lib.connect() as c:
            c.execute("DELETE FROM ocr WHERE id NOT IN (SELECT id FROM media WHERE parent_id IS NULL)")
            # Сначала то, где текст вероятнее всего: скриншоты, затем документы, экраны и мемы по оценке AI
            todo = [tuple(r) for r in c.execute(
                """SELECT m.id, m.path, m.ext, m.mtime FROM media m LEFT JOIN ocr o ON o.id = m.id
                   LEFT JOIN ai a ON a.id = m.id AND a.model = ?
                   WHERE m.parent_id IS NULL AND m.kind = 'image' AND m.cloud = 0
                     AND (o.id IS NULL OR o.version != ? OR o.mtime IS NOT m.mtime)
                   ORDER BY m.category != 'screenshot', COALESCE(a.label IN ('docs', 'screens', 'memes'), 0) DESC,
                            m.ts DESC""", (self.lib.ai_model, VERSION))]
        if self.lib.kind != "folder":
            todo = []  # снимки с телефона: оригиналы читаются по проводу слишком долго
        self.status.update(phase="analyze", total=len(todo), done=0, message=f"Распознавание: {eng.device}")
        pool = ThreadPoolExecutor(WORKERS)
        pending: set = set()
        try:
            with self.lib.connect() as c:
                for item in todo:
                    pending.add(pool.submit(self._read, eng, item))
                    while len(pending) > BATCH:  # сохраняем готовое, не дожидаясь самого долгого снимка
                        finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                        self._save(c, finished)
                    if self._cancel.is_set():
                        return
                self._save(c, wait(pending)[0])
                pending = set()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _save(self, c, finished) -> None:
        rows = [(item[0], VERSION, item[3], text) for item, text in (f.result() for f in finished) if text is not None]
        c.executemany("INSERT OR REPLACE INTO ocr(id, version, mtime, text) VALUES(?, ?, ?, ?)", rows)
        c.commit()
        self.status["done"] += len(finished)
        self.lib.ocr_version += 1

    def _read(self, eng, item: tuple) -> tuple[tuple, str | None]:
        mid, rel, ext, _ = item
        try:
            path = self.lib.local_path(rel)
            if not path.exists():
                return item, None
            img, _, _ = media.open_oriented(path, ext, SIDE, fast=True)
            return item, eng.read(img)
        except Exception:  # noqa: BLE001 — файл не читается: считаем, что текста нет, пока файл не изменится
            return item, ""


def counts(lib) -> dict:
    with lib.connect() as c:
        total = c.execute("""SELECT COUNT(*) FROM media WHERE parent_id IS NULL AND kind = 'image' AND cloud = 0""").fetchone()[0]
        done, with_text = c.execute(
            """SELECT COUNT(*), SUM(o.text != '') FROM ocr o JOIN media m ON m.id = o.id
               WHERE o.version = ? AND o.mtime IS m.mtime""", (VERSION,)).fetchone()
    return {"total": total, "done": done, "with_text": with_text or 0}


# ---------------------------------------------------------------- поиск

# Русский движок читает и латиницу, но путает похожие буквы: «Маг.» вместо «Mar.», «по.» вместо «no.».
# Поэтому и текст, и запрос сводятся к «скелету», где похожие кириллические и латинские буквы — одна буква.
_SKELETON = str.maketrans("аеёкмнопрстухгив", "aeekmhonpctyxrub")


def _norm(s: str) -> str:
    return s.casefold().translate(_SKELETON)


def _texts(lib) -> list[tuple[int, str]]:
    cache = getattr(lib, "_ocr_cache", None)
    if cache is None or cache[0] != lib.ocr_version:
        with lib.connect() as c:
            rows = [(r[0], _norm(r[1])) for r in c.execute("SELECT id, text FROM ocr WHERE text != ''")]
        cache = lib._ocr_cache = (lib.ocr_version, rows)
    return cache[1]


def search(lib, q: str, limit: int = 2000) -> list[int]:
    """Снимки, на которых есть все слова запроса (без учёта регистра и ё/е), лучшие — где слова идут подряд.

    Пробелы в цифрах не важны: «76 1693» найдёт и «761693» — распознавание то вставляет, то теряет пробелы.
    """
    words = [w for w in re.split(r"\s+", _norm(q)) if w]
    if not words:
        return []
    phrase = " ".join(words)
    digits = re.sub(r"\D", "", phrase) if re.fullmatch(r"[\d\s.-]+", phrase) else ""
    hits = []
    for mid, text in _texts(lib):
        if digits and len(digits) >= 4:
            if digits in re.sub(r"\D", "", text):
                hits.append((0, mid))
        elif all(w in text for w in words):
            hits.append((0 if phrase in text else 1, mid))
    hits.sort()
    return [mid for _, mid in hits[:limit]]


def text_of(lib, mid: int) -> str | None:
    with lib.connect() as c:
        r = c.execute("SELECT text FROM ocr WHERE id = ?", (mid,)).fetchone()
    return r[0] if r else None

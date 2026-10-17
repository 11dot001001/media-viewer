"""Поиск файлов из «Корзины отбора» (папка облака) на подключённом телефоне.

1. Кандидаты — файлы телефона с тем же размером и совместимой датой съёмки.
2. Каждый кандидат сверяется ПОБАЙТНО: файл на телефоне читается целиком, SHA-1 == SHA-1 файла в облаке
   (Яндекс Диск хранит снимки байт в байт, меняя только имя). Найденным считается только совпавший.
3. Для наглядной проверки с телефона берётся его собственная миниатюра и показывается рядом с облачной.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import threading
import traceback

import numpy as np
from PIL import Image

from . import matching
from .library import STATUS_REJECT
from .phone import WpdDevice, companions, list_with_cache, phone_db


class PhoneFinder:
    def __init__(self, lib, data_dir, device_path: str | None = None, everywhere: bool = False):
        self.lib = lib
        self.everywhere = everywhere  # False — только папки месяцев, в которые попадают даты снимков
        self.data_dir = data_dir
        self.device_path = device_path
        self.device: WpdDevice | None = None
        self.status = {"phase": "idle", "done": 0, "total": 0, "message": "", "found": 0, "checked": 0, "device": None,
                       "scope": None, "months": [], "mismatch": 0}
        self.found: dict[int, str | None] = {}  # id в папке -> путь на телефоне (None — не найден)
        self.mismatch: set[int] = set()  # размер и дата совпали, а содержимое — нет
        self.unreadable: set[int] = set()  # не удалось прочитать с телефона (обычно — он заблокирован)
        self.thumbs: dict[int, bytes] = {}  # id -> миниатюра, полученная с телефона
        self.extra: dict[int, list[str]] = {}  # id -> Live-видео/правки на телефоне
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="phone-find")
        self._thread.start()

    def _set(self, **kw):
        self.status.update(kw)

    def _run(self):
        try:
            self._set(phase="connect", done=0, total=0, message="Подключение к телефону…")
            devs = WpdDevice.list_devices()
            if self.device_path:
                devs = [d for d in devs if d["path"] == self.device_path] or devs
            if not devs:
                self._set(phase="error", message="Телефон не найден. Подключите iPhone кабелем, разблокируйте "
                                                 "и нажмите «Доверять этому компьютеру».")
                return
            d = devs[0]
            self.device = WpdDevice(d["path"], d["name"], d.get("serial"))
            self._set(device=self.device.name)
            months = None if self.everywhere else self._months()
            ffilter = None
            if months is not None:
                def ffilter(rel):
                    # iPhone раскладывает снимки по папкам «ГГГГММ_x»; остальные папки (DCIM у Android) — смотрим все
                    m = re.match(r"(\d{6})_", rel.rsplit("/", 1)[-1])
                    return m is None or m.group(1) in months
            self._set(scope="all" if months is None else "months", months=sorted(months or []))
            lst = list_with_cache(self.device, phone_db(self.data_dir, self.device),
                                  lambda n, total, first: self._set(
                                      phase="list", done=n, total=total,
                                      message="Чтение списка с телефона"),
                                  ffilter)
            self._set(phase="match", message="Сверка…")
            self._match(lst)
            self._set(phase="done", message="Готово")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._set(phase="error", message=f"Ошибка: {e}")
        finally:
            self.lib.status["version"] += 1

    def _months(self) -> set[str]:
        """Месяцы съёмки файлов из корзины (± сутки на часовые пояса и границы месяца)."""
        with self.lib.connect() as c:
            rows = c.execute("SELECT ts, ts_source FROM media WHERE parent_id IS NULL AND status=?",
                             (STATUS_REJECT,)).fetchall()
        months = set()
        for r in rows:
            if r["ts"] is None or r["ts_source"] == "mtime":
                continue  # дата ненадёжна — такие снимки найдутся только при поиске по всему телефону
            d = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=r["ts"])
            for shift in (-1, 0, 1):
                months.add((d + dt.timedelta(days=shift)).strftime("%Y%m"))
        return months

    def _match(self, lst):
        by_size: dict[int, list[tuple]] = {}
        for f in lst:
            if f[1]:
                by_size.setdefault(f[1], []).append(f)
        rels = [f[0] for f in lst]
        with self.lib.connect() as c:
            items = c.execute("SELECT id, path, size, ts, ts_source, sha1 FROM media WHERE parent_id IS NULL AND status=?",
                              (STATUS_REJECT,)).fetchall()
        conn = self.device._conn()
        broken = False
        found, extra, mismatch, thumbs, unreadable = {}, {}, set(), {}, set()
        self._set(phase="verify", done=0, total=len(items), message="Сверка содержимого с телефоном")
        for n, it in enumerate(items, 1):
            self._set(done=n)
            found[it["id"]] = None
            if broken:
                unreadable.add(it["id"])
                continue
            if not it["size"] or it["size"] < 20_000:
                continue  # крошечные файлы по размеру не сверяем
            cands = [f for f in by_size.get(it["size"], [])
                     if it["ts_source"] == "mtime" or matching.dates_compatible(f[3], it["ts"])]
            if not cands:
                continue
            try:
                cloud_sha = it["sha1"] or self.lib._sha1(self.lib.root / it["path"])
            except OSError:
                continue
            cands.sort(key=lambda f: abs((f[3] or 0) - (it["ts"] or 0)) % 900)
            for f in cands:
                oid = self.device._ids.get(f[0], (None,))[0]
                if not oid:
                    continue
                try:
                    same = conn.sha1(oid, f[1]) == cloud_sha
                except Exception:  # noqa: BLE001 — заблокированный iPhone не отдаёт содержимое файлов
                    unreadable.add(it["id"])
                    broken = True  # после сбоя передачи дальше не читаем — связь может быть нарушена
                    break
                if not same:
                    mismatch.add(it["id"])
                    continue
                mismatch.discard(it["id"])
                unreadable.discard(it["id"])
                found[it["id"]] = f[0]
                extra[it["id"]] = companions(f[0], rels)
                try:
                    thumbs[it["id"]] = self._oriented_thumb(conn.read_thumb(oid), self.lib.thumb(it["id"]))
                except Exception:  # noqa: BLE001 — без миниатюры удаление всё равно защищено сверкой содержимого
                    pass
                break
        mismatch -= unreadable
        self.found, self.extra, self.mismatch, self.thumbs = found, extra, mismatch, thumbs
        self.unreadable = unreadable
        self._set(checked=len(items), found=sum(1 for v in found.values() if v), mismatch=len(mismatch),
                  unreadable=len(unreadable))

    @staticmethod
    def _oriented_thumb(phone_jpeg: bytes, cloud_jpeg: bytes | None) -> bytes:
        """Миниатюры с iPhone не повёрнуты по EXIF — берём поворот, при котором она больше похожа на облачную."""
        img = Image.open(io.BytesIO(phone_jpeg)).convert("RGB")
        if cloud_jpeg:
            ref = np.asarray(Image.open(io.BytesIO(cloud_jpeg)).convert("L").resize((32, 32)), dtype=np.float32)

            def diff(im):
                a = np.asarray(im.convert("L").resize((32, 32)), dtype=np.float32)
                return float(((a - ref) ** 2).mean())

            img = min((img, img.transpose(Image.Transpose.ROTATE_270), img.transpose(Image.Transpose.ROTATE_180),
                       img.transpose(Image.Transpose.ROTATE_90)), key=diff)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        return buf.getvalue()

    def phone_paths(self, ids) -> list[str]:
        out = []
        for i in ids:
            if self.found.get(i):
                out.append(self.found[i])
                out += self.extra.get(i, [])
        return out

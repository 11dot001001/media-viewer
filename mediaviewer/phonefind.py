"""Поиск файлов из «Корзины отбора» (папка облака) на подключённом телефоне.

С телефона ничего не копируется: сверка по размеру файла и дате съёмки из списка файлов телефона
(Яндекс Диск хранит снимки байт в байт, меняя только имя).
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import traceback

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
                       "scope": None, "months": []}
        self.found: dict[int, str | None] = {}  # id в папке -> путь на телефоне (None — не найден)
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
            items = c.execute("SELECT id, size, ts, ts_source FROM media WHERE parent_id IS NULL AND status=?",
                              (STATUS_REJECT,)).fetchall()
        found, extra = {}, {}
        for it in items:
            cands = [f for f in by_size.get(it["size"], [])
                     if it["ts_source"] == "mtime" or matching.dates_compatible(f[3], it["ts"])]
            if it["size"] and it["size"] < 20_000:
                cands = []  # крошечные файлы по размеру не сверяем
            if cands:
                best = min(cands, key=lambda f: abs((f[3] or 0) - (it["ts"] or 0)) % 900)
                found[it["id"]] = best[0]
                extra[it["id"]] = companions(best[0], rels)
            else:
                found[it["id"]] = None
        self.found, self.extra = found, extra
        self._set(checked=len(items), found=sum(1 for v in found.values() if v))

    def phone_paths(self, ids) -> list[str]:
        out = []
        for i in ids:
            if self.found.get(i):
                out.append(self.found[i])
                out += self.extra.get(i, [])
        return out

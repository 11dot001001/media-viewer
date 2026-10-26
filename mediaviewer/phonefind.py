"""Поиск файлов из «Корзины отбора» (папка облака) на подключённом телефоне.

1. Кандидаты — файлы телефона с тем же размером и совместимой датой съёмки.
2. Каждый кандидат сверяется ПОБАЙТНО: файл на телефоне читается целиком, SHA-1 == SHA-1 файла в облаке
   (Яндекс Диск хранит снимки байт в байт, меняя только имя). Найденным считается только совпавший.
3. Для наглядной проверки с телефона берётся его собственная миниатюра и показывается рядом с облачной.

Свойства каждого файла iPhone отдаёт ~50 мс и строго по одному, поэтому читать все папки нужных месяцев
долго (25 тыс. файлов — 20+ минут). Внутри месячной папки файлы идут по времени съёмки, поэтому
снимок ищется бинарным поиском по дате: читаются десяток файлов на пути и соседи с той же секундой.
По кнопке «Искать шире» не найденные ищутся вторым проходом во всём окне ±26 ч (сдвиг часового пояса,
нарушенный порядок). Автоматически он не запускается: обычно снимков просто нет на телефоне (их удалили
там, а в облаке оставили), и для каждого такого он читал бы с телефона сотни файлов.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import threading
import traceback
from bisect import bisect_left, bisect_right

import numpy as np
from PIL import Image

from . import matching
from .library import STATUS_REJECT
from .phone import LIST_EXT, WpdDevice, companions, list_with_cache, phone_db

MONTH_DIR = re.compile(r"(\d{6})_")
NEAR = 5  # с, окно первого прохода: дата на телефоне обычно совпадает с EXIF до секунды
WIDE = 26 * 3600 + NEAR  # окно второго прохода — как в matching.dates_compatible
SLACK = 2  # лишние позиции по краям окна — на случай мелких нарушений порядка
COMPANION_SPAN = 8  # Live-видео и правки лежат рядом со снимком


class _SortedFolder:
    """Месячная папка iPhone. Файлы в ней упорядочены по времени съёмки (от новых к старым),
    поэтому свойства читаются только для тех позиций, что понадобились поиску."""

    def __init__(self, conn, fid: str, prefix: str):
        self.c, self.fid, self.prefix = conn, fid, prefix
        self.kids = conn.children(fid)
        self.rows: dict[int, dict | None] = {}  # позиция -> свойства (None — не прочитались)
        self.reads = 0
        n = len(self.kids)
        self.desc = n < 2 or (self._ts(0) or 0) >= (self._ts(n - 1) or 0)

    def load(self, positions) -> None:
        need = [p for p in positions if 0 <= p < len(self.kids) and p not in self.rows]
        if not need:
            return
        props = self.c.properties([self.kids[p] for p in need])
        self.reads += len(need)
        for p in need:
            r = props.get(self.kids[p])
            self.rows[p] = r if r and not r["folder"] else None

    def _ts(self, p: int) -> float | None:
        self.load([p])
        r = self.rows[p]
        return (r["created"] or r["modified"]) if r else None

    def _key(self, p: int) -> float:
        # ключ, растущий с позицией; файл без даты берёт ключ ближайшего соседа справа
        for q in range(p, min(p + 4, len(self.kids))):
            t = self._ts(q)
            if t is not None:
                return -t if self.desc else t
        return float("inf")

    def window(self, lo: float, hi: float) -> list[int]:
        """Позиции файлов с датой съёмки в [lo, hi] (плюс SLACK позиций по краям)."""
        class Keys:  # «список» ключей для bisect: читает с телефона только то, на что смотрит поиск
            def __len__(_):
                return len(self.kids)

            def __getitem__(_, p):
                return self._key(p)

        k_lo, k_hi = (-hi, -lo) if self.desc else (lo, hi)
        a = max(0, bisect_left(Keys(), k_lo) - SLACK)
        b = min(len(self.kids), bisect_right(Keys(), k_hi) + SLACK)
        self.load(range(a, b))
        return list(range(a, b))

    def file(self, p: int) -> tuple[str, int, float, float | None] | None:
        r = self.rows.get(p)
        if not r or not r["name"].lower().endswith(tuple(LIST_EXT)):
            return None
        return f"{self.prefix}/{r['name']}", r["size"], r["modified"] or 0.0, r["created"]

    def loaded_ids(self) -> dict[str, tuple[str, int, str]]:
        return {f[0]: (self.kids[p], f[1], self.fid) for p in self.rows if (f := self.file(p))}


class PhoneFinder:
    def __init__(self, lib, data_dir, device_path: str | None = None, everywhere: bool = False, wide: bool = False):
        self.lib = lib
        self.everywhere = everywhere  # False — только папки месяцев, в которые попадают даты снимков
        self.wide = wide  # второй проход поиска по дате: окно ±26 ч для не найденных
        self.data_dir = data_dir
        self.device_path = device_path
        self.device: WpdDevice | None = None
        self.status = {"phase": "idle", "done": 0, "total": 0, "message": "", "found": 0, "checked": 0, "device": None,
                       "scope": None, "months": [], "mismatch": 0, "wide": wide, "reads": 0}
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
            self._set(scope="all" if months is None else "months", months=sorted(months or []))
            if months is None or not self._search_by_date():
                self._search_full(months)
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

    def _items(self):
        with self.lib.connect() as c:
            return c.execute("SELECT id, path, size, ts, ts_source, sha1 FROM media WHERE parent_id IS NULL AND status=?",
                             (STATUS_REJECT,)).fetchall()

    def _search_full(self, months: set[str] | None) -> None:
        """Прочитать все файлы в папках нужных месяцев (или на всём телефоне) и сверить по размеру и дате."""
        ffilter = None
        if months is not None:
            def ffilter(rel):
                # iPhone раскладывает снимки по папкам «ГГГГММ_x»; остальные папки (DCIM у Android) — смотрим все
                m = MONTH_DIR.match(rel.rsplit("/", 1)[-1])
                return m is None or m.group(1) in months
        lst = list_with_cache(self.device, phone_db(self.data_dir, self.device),
                              lambda n, total, first: self._set(
                                  phase="list", done=n, total=total,
                                  message="Чтение списка с телефона"),
                              ffilter)
        by_size: dict[int, list[tuple]] = {}
        for f in lst:
            if f[1]:
                by_size.setdefault(f[1], []).append(f)
        rels = [f[0] for f in lst]
        items = self._items()
        acc = self._acc()
        self._verify(items, lambda it: by_size.get(it["size"], []), lambda rel: companions(rel, rels), acc)
        self._publish(items, acc)

    def _search_by_date(self) -> bool:
        """Бинарный поиск по дате в месячных папках iPhone. False — раскладка не как у iPhone, нужен полный поиск."""
        dev = self.device
        items = self._items()
        with dev._lock:
            conn = dev._conn()
            folders: dict[str, list[tuple[str, str]]] = {}  # ГГГГММ -> [(id папки, путь)]
            for sid, s in conn.properties(conn.children("DEVICE")).items():
                for fid, r in conn.properties(conn.children(sid)).items():
                    m = MONTH_DIR.match(r["name"])
                    if not r["folder"]:
                        continue
                    if m is None:
                        return False  # не iPhone (у Android снимки в DCIM/…) — ищем как раньше
                    folders.setdefault(m.group(1), []).append((fid, f"{s['name']}/{r['name']}"))
            opened: dict[str, _SortedFolder] = {}

            def folder(fid, prefix):
                if fid not in opened:
                    opened[fid] = _SortedFolder(conn, fid, prefix)
                return opened[fid]

            def month_folders(ts, days):
                d = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=ts)
                keys = {(d + dt.timedelta(days=k)).strftime("%Y%m") for k in range(-days, days + 1)}
                return [folder(*f) for k in sorted(keys) for f in folders.get(k, [])]

            def candidates(it, span, days):
                if it["ts"] is None or it["ts_source"] == "mtime":
                    return []  # без надёжной даты бинарный поиск невозможен — найдётся «по всему телефону»
                out = []
                for sf in month_folders(it["ts"], days):
                    for p in sf.window(it["ts"] - span, it["ts"] + span):
                        f = sf.file(p)
                        if f and f[1] == it["size"]:
                            out.append(f)
                            dev._ids[f[0]] = (sf.kids[p], f[1], sf.fid)  # для сверки и удаления
                return out

            def nearby(rel):
                # соседи найденного снимка: среди них его Live-видео и правки
                for sf in opened.values():
                    for p, r in list(sf.rows.items()):
                        if r and f"{sf.prefix}/{r['name']}" == rel:
                            sf.load(range(p - COMPANION_SPAN, p + COMPANION_SPAN + 1))
                            dev._ids.update(sf.loaded_ids())
                            rels = [f[0] for q in range(p - COMPANION_SPAN, p + COMPANION_SPAN + 1)
                                    if (f := sf.file(q))]
                            return companions(rel, rels)
                return []

            reads = lambda: sum(sf.reads for sf in opened.values())  # noqa: E731
            acc = self._acc()
            passes = [("Поиск по дате съёмки", NEAR, 1)]
            if self.wide:
                passes.append(("Поиск со сдвигом часового пояса", WIDE, 2))
            for label, span, days in passes:
                todo = [it for it in items if not acc["found"].get(it["id"]) and it["id"] not in acc["unreadable"]
                        and (it["size"] or 0) >= 20_000]
                if not todo:
                    break
                for n, it in enumerate(todo, 1):
                    self._set(phase="list", done=n, total=len(todo), message=f"{label} (прочитано с телефона {reads()})")
                    cands = candidates(it, span, days)
                    self._verify([it], lambda _, c=cands: c, nearby, acc)
                    if acc["broken"]:
                        break
            self._set(reads=reads())
        self._publish(items, acc)
        return True

    @staticmethod
    def _acc() -> dict:
        return {"found": {}, "extra": {}, "mismatch": set(), "thumbs": {}, "unreadable": set(), "broken": False}

    def _verify(self, items, cands_of, companions_of, acc) -> None:
        """Сверить кандидатов побайтно (SHA-1 всего файла на телефоне) и записать результат в acc."""
        conn = self.device._conn()
        found, mismatch, unreadable = acc["found"], acc["mismatch"], acc["unreadable"]
        progress = len(items) > 1  # поиск по дате сверяет по одному снимку и показывает свой прогресс
        for n, it in enumerate(items, 1):
            if progress:
                self._set(phase="verify", done=n, total=len(items), message="Сверка содержимого с телефоном")
            found.setdefault(it["id"], None)
            if acc["broken"]:
                unreadable.add(it["id"])
                continue
            if not it["size"] or it["size"] < 20_000:
                continue  # крошечные файлы по размеру не сверяем
            cands = [f for f in cands_of(it)
                     if f[1] == it["size"] and (it["ts_source"] == "mtime" or matching.dates_compatible(f[3], it["ts"]))]
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
                    acc["broken"] = True  # после сбоя передачи дальше не читаем — связь может быть нарушена
                    break
                if not same:
                    mismatch.add(it["id"])
                    continue
                mismatch.discard(it["id"])
                unreadable.discard(it["id"])
                found[it["id"]] = f[0]
                acc["extra"][it["id"]] = companions_of(f[0])
                try:
                    acc["thumbs"][it["id"]] = self._oriented_thumb(conn.read_thumb(oid), self.lib.thumb(it["id"]))
                except Exception:  # noqa: BLE001 — без миниатюры удаление всё равно защищено сверкой содержимого
                    pass
                break

    def _publish(self, items, acc) -> None:
        for it in items:
            acc["found"].setdefault(it["id"], None)  # проверен, но не найден (крошечные, без даты — тоже)
        mismatch = acc["mismatch"] - acc["unreadable"]
        self.found, self.extra, self.mismatch, self.thumbs = acc["found"], acc["extra"], mismatch, acc["thumbs"]
        self.unreadable = acc["unreadable"]
        self._set(checked=len(items), found=sum(1 for v in self.found.values() if v), mismatch=len(mismatch),
                  unreadable=len(self.unreadable))

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

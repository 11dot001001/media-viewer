"""Библиотека = одна папка с фото. Индекс в SQLite, фоновое сканирование, группировка, корзина."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import grouping, media, trash
from .classify import classify
from .journal import Journal

SKIP_DIRS = {trash.REJECTED_DIR, "$RECYCLE.BIN", "System Volume Information", ".thumbnails", ".trashed", "@eaDir"}

# Файл облака (Яндекс.Диск, OneDrive), не скачанный на компьютер: RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE.
# Чтение такого файла запускает скачивание и висит десятки секунд, а при сбое кончается ошибкой.
CLOUD_ONLY_ATTRS = 0x400000 | 0x40000 | 0x1000


def cloud_only(p: Path) -> bool:
    try:
        return bool(getattr(p.stat(), "st_file_attributes", 0) & CLOUD_ONLY_ATTRS)
    except OSError:
        return False

SCHEMA = """
CREATE TABLE IF NOT EXISTS media(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  parent_id INTEGER,
  kind TEXT, ext TEXT, size INTEGER, mtime REAL,
  width INTEGER, height INTEGER, ts REAL, ts_source TEXT,
  make TEXT, model TEXT, software TEXT,
  lat REAL, lon REAL, duration REAL,
  phash INTEGER, dhash INTEGER, sharpness REAL,
  sha1 TEXT, category TEXT,
  status INTEGER NOT NULL DEFAULT 0,
  series_id INTEGER, series_best INTEGER NOT NULL DEFAULT 0,
  dup_id INTEGER, dup_best INTEGER NOT NULL DEFAULT 0, dup_exact INTEGER NOT NULL DEFAULT 0,
  analyzed INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  match TEXT, match_ref TEXT, match_strong INTEGER,
  cloud INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_parent ON media(parent_id);
CREATE INDEX IF NOT EXISTS ix_size ON media(size);
CREATE TABLE IF NOT EXISTS thumbs(id INTEGER PRIMARY KEY, data BLOB);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

STATUS_NONE, STATUS_KEEP, STATUS_REJECT = 0, 1, 2


def _primary_order(ext: str) -> int:
    """Кто главный в паре с одинаковым именем: JPG/HEIC > RAW > видео (Live Photo) > .AAE (правки iOS)."""
    if ext in media.IMAGE_EXT:
        return 0
    if ext in media.RAW_EXT:
        return 1
    if ext in media.VIDEO_EXT:
        return 2
    return 3


def _pair_stem(rel: str) -> str:
    """IMG_O1234.AAE / IMG_E1234.AAE относятся к IMG_1234.*"""
    stem = Path(rel).stem.lower()
    if rel.lower().endswith(".aae") and re.match(r"img_[oe]\d", stem):
        stem = "img_" + stem[5:]
    return stem


class Library:
    kind = "folder"

    def __init__(self, root: Path, data_dir: Path, lib_dir: Path | None = None):
        self.root = root.resolve()
        self.data_dir = data_dir
        self.journal = Journal(data_dir / "journal.db")
        if lib_dir is None:
            key = hashlib.sha1(str(self.root).lower().encode("utf-8")).hexdigest()[:12]
            lib_dir = data_dir / "libraries" / key
            lib_dir.mkdir(parents=True, exist_ok=True)
            (lib_dir / "root.txt").write_text(str(self.root), encoding="utf-8")
        self.dir = lib_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "index.db"
        self.preview_dir = self.dir / "previews"
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._group_lock = threading.Lock()
        self.finder = None  # PhoneFinder: поиск файлов корзины на телефоне
        self._offline: set[str] = set()  # файлы, которые лежат только в облаке (см. CLOUD_ONLY_ATTRS)
        self.status = {"phase": "idle", "total": 0, "done": 0, "message": "", "errors": 0, "started": 0, "version": 0,
                       "folder_size": None, "folder_files": None, "cloud": 0}
        with self.connect() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(media)")}
            for col, typ in (("match", "TEXT"), ("match_ref", "TEXT"), ("match_strong", "INTEGER"),
                             ("cloud", "INTEGER NOT NULL DEFAULT 0")):
                if col not in cols:  # индекс от прошлой версии
                    c.execute(f"ALTER TABLE media ADD COLUMN {col} {typ}")
            for row in c.execute("SELECT key, value FROM settings WHERE key IN ('folder_size', 'folder_files')"):
                self.status[row["key"]] = int(row["value"])

    # ------------------------------------------------------------ БД

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def settings(self) -> grouping.Settings:
        s = grouping.Settings()
        with self.connect() as c:
            for row in c.execute("SELECT key, value FROM settings"):
                if row["key"] == "series_gap":
                    s.series_gap = float(row["value"])
                elif row["key"] == "series_dist":
                    s.series_dist = int(row["value"])
                elif row["key"] == "series_keep":
                    s.series_keep = row["value"]
        return s

    def save_settings(self, values: dict) -> None:
        allowed = {"series_gap", "series_dist", "series_keep", "folder_size", "folder_files"}
        with self.connect() as c:
            for k, v in values.items():
                if k in allowed and v is not None:
                    c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (k, str(v)))

    @property
    def title(self) -> str:
        return str(self.root)

    def local_path(self, rel: str) -> Path:
        """Где лежит файл на диске (у телефона — локальная копия, при необходимости скачивается)."""
        return self.root / rel

    def _prepare(self, todo: list[tuple]) -> None:
        """Перед анализом: у телефона здесь файлы копируются на компьютер."""

    def after_scan(self) -> None:
        """После сканирования: у телефона здесь сверка с облаком."""

    # ------------------------------------------------------------ сканирование

    @property
    def scanning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_scan(self) -> None:
        if self.scanning:
            return
        self._cancel.clear()
        self._thread = threading.Thread(target=self._scan_safe, daemon=True, name="scan")
        self._thread.start()

    def stop(self) -> None:
        self._cancel.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _set(self, **kw) -> None:
        self.status.update(kw)

    def _scan_safe(self) -> None:
        self._set(phase="walk", total=0, done=0, errors=0, message="Поиск файлов…", started=time.time())
        try:
            self._scan()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._set(phase="error", message=f"Ошибка сканирования: {e}")
            return
        if self.status["phase"] == "confirm":
            pass  # ждём решения пользователя (телефон: копировать ли много файлов)
        elif self._cancel.is_set():
            self._set(phase="idle", message="Остановлено")
        else:
            self._set(phase="done", message="Готово")
        self.status["version"] += 1

    def _walk(self) -> list[tuple[str, Path, int, float, str]]:
        """Обход папки. Попутно считает размер всей папки (на Windows stat из scandir бесплатный)."""
        files = []
        self._offline = set()
        total_size = total_files = 0
        stack = [self.root]
        while stack:
            if self._cancel.is_set():
                return files
            d = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not e.name.startswith(".") and e.name not in SKIP_DIRS:
                            stack.append(Path(e.path))
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                total_size += st.st_size
                total_files += 1
                ext = os.path.splitext(e.name)[1].lower()
                if ext not in media.ALL_EXT or e.name.startswith("._"):
                    continue
                p = Path(e.path)
                rel = p.relative_to(self.root).as_posix()
                if getattr(st, "st_file_attributes", 0) & CLOUD_ONLY_ATTRS:
                    self._offline.add(rel)
                files.append((rel, p, st.st_size, st.st_mtime, ext))
            self._set(message=f"Найдено файлов: {len(files)}")
        self._set(folder_size=total_size, folder_files=total_files)
        self.save_settings({"folder_size": total_size, "folder_files": total_files})
        return files

    def _scan(self) -> None:
        files = self._walk()
        if self._cancel.is_set():
            return

        # Пары с одинаковым именем в одной папке: RAW+JPG, HEIC+MOV (Live Photo)
        by_stem: dict[tuple[str, str], list] = defaultdict(list)
        for f in files:
            rel = f[0]
            by_stem[(rel.rsplit("/", 1)[0] if "/" in rel else "", _pair_stem(rel))].append(f)
        parent_of: dict[str, str] = {}
        for group in by_stem.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda f: _primary_order(f[4]))
            head = group[0]
            if _primary_order(head[4]) >= 2:
                continue  # одни видео/правки — не пара
            for f in group[1:]:
                if _primary_order(f[4]) > _primary_order(head[4]):
                    parent_of[f[0]] = head[0]

        # .AAE без снимка не нужны в списке
        files = [f for f in files if f[4] in media.ALL_EXT or f[0] in parent_of]
        on_disk = {f[0] for f in files}
        with self.connect() as c:
            existing = {r["path"]: r for r in c.execute(
                "SELECT id, path, size, mtime, analyzed, parent_id, cloud, error IS NOT NULL AS failed FROM media")}
            gone = [r["id"] for p, r in existing.items() if p not in on_disk]
            for i in range(0, len(gone), 500):
                chunk = gone[i:i + 500]
                q = ",".join("?" * len(chunk))
                c.execute(f"DELETE FROM media WHERE id IN ({q})", chunk)
                c.execute(f"DELETE FROM thumbs WHERE id IN ({q})", chunk)

            todo: list[tuple[int, str, Path, str, str]] = []
            in_cloud: list[tuple[int, str, Path, str, str]] = []
            primaries = [f for f in files if f[0] not in parent_of]
            children = [f for f in files if f[0] in parent_of]
            ids: dict[str, int] = {}
            for rel, p, size, mtime, ext in primaries:
                kind = media.media_kind(ext)
                old = existing.get(rel)
                changed = old is None or old["size"] != size or abs((old["mtime"] or 0) - mtime) > 1 or old["parent_id"] is not None
                if changed:
                    c.execute(
                        """INSERT INTO media(path, kind, ext, size, mtime, parent_id, analyzed, sha1)
                           VALUES(?, ?, ?, ?, ?, NULL, 0, NULL)
                           ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, ext=excluded.ext, size=excluded.size,
                           mtime=excluded.mtime, parent_id=NULL, analyzed=0, sha1=NULL""",
                        (rel, kind, ext, size, mtime))
                ids[rel] = c.execute("SELECT id FROM media WHERE path=?", (rel,)).fetchone()[0]
                # failed: прошлые версии пытались читать такие файлы и записывали ошибку
                if changed or not old["analyzed"] or old["cloud"] or (old["failed"] and rel in self._offline):
                    # Не скачанный из облака файл не читаем; уже разобранный и выгруженный обратно в облако — оставляем как есть
                    (in_cloud if rel in self._offline else todo).append((ids[rel], rel, p, ext, kind))
            for t in in_cloud:
                self._store(c, t[0], self._analyze(*t, cloud=True))
            self.status["cloud"] = len(in_cloud)
            for rel, p, size, mtime, ext in children:
                c.execute(
                    """INSERT INTO media(path, kind, ext, size, mtime, parent_id, analyzed)
                       VALUES(?, ?, ?, ?, ?, ?, 1)
                       ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,
                       parent_id=excluded.parent_id, analyzed=1, series_id=NULL, dup_id=NULL""",
                    (rel, media.media_kind(ext), ext, size, mtime, ids[parent_of[rel]]))
                c.execute("DELETE FROM thumbs WHERE id=(SELECT id FROM media WHERE path=?)", (rel,))

        self._prepare(todo)
        if self._cancel.is_set():
            return

        # Анализ новых/изменённых файлов в пуле потоков (Pillow и ffmpeg отпускают GIL)
        self._set(phase="analyze", total=len(todo), done=0, message="Анализ файлов…")
        if todo:
            # Больше 16 потоков не ускоряет: упирается в GIL на мелких операциях numpy/Pillow
            workers = max(2, min(16, (os.cpu_count() or 4)))
            with ThreadPoolExecutor(workers) as ex, self.connect() as c:
                futs = {ex.submit(self._analyze, *t): t for t in todo}
                n = 0
                for fut in as_completed(futs):
                    if self._cancel.is_set():
                        ex.shutdown(wait=False, cancel_futures=True)
                        return
                    mid = futs[fut][0]
                    try:
                        info = fut.result()
                        self._store(c, mid, info)
                    except Exception as e:  # noqa: BLE001
                        self.status["errors"] += 1
                        c.execute("UPDATE media SET analyzed=1, error=?, category=COALESCE(category,'other') WHERE id=?",
                                  (str(e)[:300], mid))
                    n += 1
                    self.status["done"] = n
                    if n % 40 == 0:
                        c.commit()
                        self.status["version"] += 1

        # Точные дубликаты: SHA-1 только для файлов, у которых совпадает размер
        self._set(phase="hash", message="Поиск точных копий…")
        with self.connect() as c:
            rows = [r for r in c.execute(
                """SELECT id, path FROM media WHERE parent_id IS NULL AND sha1 IS NULL AND size IN
                   (SELECT size FROM media WHERE parent_id IS NULL GROUP BY size HAVING COUNT(*) > 1)""")
                    if r["path"] not in self._offline]
        self._set(total=len(rows), done=0)
        if rows:
            with ThreadPoolExecutor(4) as ex, self.connect() as c:
                futs = {ex.submit(lambda rel: self._sha1(self.local_path(rel)), r["path"]): r["id"] for r in rows}
                for fut in as_completed(futs):
                    if self._cancel.is_set():
                        ex.shutdown(wait=False, cancel_futures=True)
                        return
                    try:
                        c.execute("UPDATE media SET sha1=? WHERE id=?", (fut.result(), futs[fut]))
                    except OSError:
                        pass
                    self.status["done"] += 1

        self._set(phase="group", message="Поиск серий и дубликатов…")
        self.regroup()
        self.after_scan()

    @staticmethod
    def _sha1(p: Path) -> str:
        h = hashlib.sha1()
        with open(p, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        return h.hexdigest()

    def _analyze(self, mid: int, rel: str, p: Path, ext: str, kind: str, cloud: bool = False) -> dict:
        empty = ("width", "height", "ts", "make", "model", "software", "lat", "lon", "duration",
                 "phash", "dhash", "sharpness", "thumb")
        if cloud:
            # Файл только в облаке: дата — из имени или mtime, остальное — когда он скачается (cloud=1)
            info = dict.fromkeys(empty)
            info["error"] = None
        else:
            try:
                info = media.analyze_video(p) if kind == "video" else media.analyze_image(p, ext)
                info["error"] = None
            except Exception as e:  # noqa: BLE001 — файл не читается, но в списке он должен остаться
                msg = str(e)
                if msg.startswith(("b'", 'b"')):
                    msg = msg[2:-1]
                info = dict.fromkeys(empty)
                info["error"] = f"Не удалось прочитать: {msg}"[:300]
        info["cloud"] = int(cloud)
        if info["ts"] is not None:
            info["ts_source"] = "exif"
        else:
            info["ts"] = media.date_from_name(p.name)
            info["ts_source"] = "name" if info["ts"] is not None else "mtime"
            if info["ts"] is None:
                # mtime — в UTC, переводим в локальное «настенное» время, как EXIF
                lt = time.localtime(p.stat().st_mtime)
                info["ts"] = time.mktime(lt) + lt.tm_gmtoff
        info["category"] = classify(rel, kind, ext, info)
        return info

    def _store(self, c: sqlite3.Connection, mid: int, i: dict) -> None:
        c.execute(
            """UPDATE media SET width=?, height=?, ts=?, ts_source=?, make=?, model=?, software=?, lat=?, lon=?,
               duration=?, phash=?, dhash=?, sharpness=?, category=?, analyzed=1, error=?, cloud=? WHERE id=?""",
            (i["width"], i["height"], i["ts"], i["ts_source"], i["make"], i["model"], i["software"], i["lat"],
             i["lon"], i["duration"], i["phash"], i["dhash"], i["sharpness"], i["category"], i["error"],
             i.get("cloud", 0), mid))
        if i["error"]:
            self.status["errors"] += 1
        if i.get("thumb"):
            c.execute("INSERT OR REPLACE INTO thumbs(id, data) VALUES(?, ?)", (mid, i["thumb"]))
        elif i.get("cloud"):
            c.execute("DELETE FROM thumbs WHERE id=?", (mid,))

    # ------------------------------------------------------------ группировка

    def regroup(self) -> None:
        with self._group_lock:
            with self.connect() as c:
                rows = [dict(r) for r in c.execute(
                    """SELECT id, path, kind, category, ts, phash, dhash, sha1, size, width, height, sharpness, make, model
                       FROM media WHERE parent_id IS NULL AND analyzed=1""")]
                res = grouping.compute(rows, self.settings())
                c.execute("UPDATE media SET series_id=NULL, series_best=0, dup_id=NULL, dup_best=0, dup_exact=0")
                c.executemany("UPDATE media SET series_id=?, series_best=? WHERE id=?",
                              [(sid, int(mid in res.series_best), mid) for mid, sid in res.series_id.items()])
                c.executemany("UPDATE media SET dup_id=?, dup_best=?, dup_exact=? WHERE id=?",
                              [(gid, int(mid in res.dup_best), int(mid in res.dup_exact), mid)
                               for mid, gid in res.dup_id.items()])
            self.status["version"] += 1

    # ------------------------------------------------------------ запросы

    def list_items(self) -> list[dict]:
        with self.connect() as c:
            extra: dict[int, list] = defaultdict(list)
            for r in c.execute("SELECT parent_id, ext, size, kind FROM media WHERE parent_id IS NOT NULL"):
                extra[r["parent_id"]].append(r)
            out = []
            found = self.finder.found if self.finder is not None else None
            # Пока идёт сканирование, необработанные файлы не показываем: у них ещё нет ни даты, ни миниатюры
            for r in c.execute(
                    """SELECT id, path, kind, ext, size, mtime, width, height, ts, ts_source, model, lat, lon, duration,
                       sharpness, category, status, series_id, series_best, dup_id, dup_best, dup_exact, error,
                       match, match_ref, match_strong, cloud
                       FROM media WHERE parent_id IS NULL AND (analyzed=1 OR ?) ORDER BY ts, path""",
                    (not self.scanning,)):
                kids = extra.get(r["id"], [])
                cx = []
                for k in kids:
                    if k["ext"] in media.RAW_EXT:
                        cx.append("RAW")
                    elif k["kind"] == "video":
                        cx.append("LIVE")
                    elif k["ext"] != ".aae":
                        cx.append(k["ext"].lstrip(".").upper())
                out.append({
                    "id": r["id"], "p": r["path"], "k": "v" if r["kind"] == "video" else "i", "e": r["ext"],
                    "c": r["category"] or "other", "t": r["ts"], "tsrc": r["ts_source"],
                    "w": r["width"], "h": r["height"], "sz": (r["size"] or 0) + sum(k["size"] or 0 for k in kids),
                    "lat": r["lat"], "lon": r["lon"], "dur": r["duration"], "sh": r["sharpness"],
                    "cam": r["model"], "st": r["status"], "s": r["series_id"], "sb": r["series_best"],
                    "d": r["dup_id"], "db": r["dup_best"], "dx": r["dup_exact"], "cx": cx,
                    "v": int(r["mtime"] or 0), "err": r["error"], "cl": r["cloud"],
                    "m": r["match"], "mr": r["match_ref"], "ms": r["match_strong"],
                    "ph": (None if not found or r["id"] not in found else
                           (found[r["id"]] or (-1 if r["id"] in self.finder.mismatch else
                                               -2 if r["id"] in self.finder.unreadable else 0))),
                })
            return out

    def item(self, mid: int) -> dict | None:
        with self.connect() as c:
            r = c.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone()
            if not r:
                return None
            d = dict(r)
            d["abs_path"] = str(self.root / r["path"]) if self.kind == "folder" else f"{self.title}: {r['path']}"
            d["children"] = [dict(k) for k in c.execute("SELECT id, path, ext, size FROM media WHERE parent_id=?", (mid,))]
            d.pop("phash", None)
            d.pop("dhash", None)
            return d

    def thumb(self, mid: int) -> bytes | None:
        with self.connect() as c:
            r = c.execute("SELECT data FROM thumbs WHERE id=?", (mid,)).fetchone()
            return r[0] if r else None

    def file_for_view(self, mid: int) -> tuple[Path, bool] | None:
        """Путь для показа в браузере: оригинал или сгенерированный JPEG. Второе значение — это превью."""
        with self.connect() as c:
            r = c.execute("SELECT path, ext, kind, mtime FROM media WHERE id=?", (mid,)).fetchone()
        if not r:
            return None
        src = self.local_path(r["path"])
        if r["kind"] == "video" or r["ext"] in media.BROWSER_IMAGE_EXT:
            return src, False
        dest = self.preview_dir / f"{mid}_{int(r['mtime'] or 0)}.jpg"
        if not dest.exists():
            media.make_preview(src, r["ext"], dest)
        return dest, True

    def abs_path(self, mid: int) -> Path | None:
        with self.connect() as c:
            r = c.execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
        return self.local_path(r["path"]) if r else None

    def set_status(self, ids: list[int], status: int) -> None:
        with self.connect() as c:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ",".join("?" * len(chunk))
                c.execute(f"UPDATE media SET status=? WHERE id IN ({q}) OR parent_id IN ({q})", (status, *chunk, *chunk))

    def empty_trash(self, phone: bool = False) -> dict:
        if self.scanning:
            raise RuntimeError("Дождитесь окончания сканирования")
        if phone and (self.finder is None or self.finder.running or self.finder.device is None):
            raise RuntimeError("Сначала найдите файлы на телефоне")
        with self.connect() as c:
            rows = [dict(r) for r in c.execute(
                """SELECT id, path, parent_id, kind, size, sha1, ts, ts_source, model, width, height, duration,
                   phash, dhash, match, match_ref FROM media
                   WHERE status=? OR parent_id IN (SELECT id FROM media WHERE status=?)""",
                (STATUS_REJECT, STATUS_REJECT))]
        hashes: dict[int, str] = {}
        if phone:
            # SHA-1 каждого файла из облака: на телефоне удалим только файл с ТАКИМ ЖЕ содержимым
            for r in rows:
                if r["parent_id"] is None and self.finder.found.get(r["id"]):
                    p = self.root / r["path"]
                    if p.is_file():
                        hashes[r["id"]] = self._sha1(p)
        removed, errors, freed = self._remove(rows)
        phone_res = None
        if phone:
            # С телефона — только то, что уже ушло из облака в Корзину (копия остаётся восстановимой)
            result: dict[str, str | None] = {}
            phone_sizes = {rel: e[1] for rel, e in self.finder.device._ids.items()}
            for i in removed:
                main = self.finder.found.get(i)
                if not main:
                    continue
                if i not in hashes:
                    result[main] = "нет файла из облака для сверки — не тронут"
                    continue
                r1 = self.finder.device.delete([main], {main: hashes[i]})
                result.update(r1)
                if r1.get(main) is None and self.finder.extra.get(i):
                    # Live-видео и правки — только если сам снимок прошёл сверку и удалён
                    result.update(self.finder.device.delete(self.finder.extra[i]))
                self.finder.found.pop(i, None)
            done = [k for k, v in result.items() if v is None]
            phone_res = {"removed": len(done), "freed": sum(phone_sizes.get(k, 0) for k in done),
                         "errors": [f"{k}: {v}" for k, v in result.items() if v][:50]}
            self.journal.record_cleanup(self.title, "phone", phone_res["removed"], phone_res["freed"],
                                        self.finder.device.name)
        with self.connect() as c:
            for i in range(0, len(removed), 500):
                chunk = removed[i:i + 500]
                q = ",".join("?" * len(chunk))
                c.execute(f"DELETE FROM media WHERE id IN ({q})", chunk)
                c.execute(f"DELETE FROM thumbs WHERE id IN ({q})", chunk)
        removed_set = set(removed)
        for f in self.preview_dir.glob("*.jpg") if self.preview_dir.exists() else []:
            if int(f.stem.split("_")[0]) in removed_set:
                f.unlink(missing_ok=True)
        self.regroup()
        if self.status["folder_size"] is not None:
            self.status["folder_size"] = max(0, self.status["folder_size"] - freed)
            self.status["folder_files"] = max(0, self.status["folder_files"] - len(removed))
            self.save_settings({k: self.status[k] for k in ("folder_size", "folder_files")})
        self.journal.record_cleanup(self.title, "cloud" if self.kind == "folder" else "phone", len(removed), freed,
                                    None if self.kind == "folder" else self.title)
        return {"removed": len(removed), "errors": errors[:50], "freed": freed, "target": self.trash_target(),
                "phone": phone_res}

    def trash_target(self) -> str:
        return trash.describe_target(self.root)

    def _remove(self, rows: list[dict]) -> tuple[list[int], list[str], int]:
        """Удалить файлы с диска (Корзина Windows / _rejected) и записать их отпечатки в журнал."""
        removed, errors, freed, journal = [], [], 0, []
        ids = ",".join(str(r["id"]) for r in rows) or "0"
        with self.connect() as c:
            thumbs = {r["id"]: r["data"] for r in c.execute(f"SELECT id, data FROM thumbs WHERE id IN ({ids})")}
        for r in rows:
            p = self.root / r["path"]
            try:
                size = p.stat().st_size if p.exists() else 0
                fp = None
                if r["parent_id"] is None and size:
                    fp = {k: r[k] for k in ("path", "kind", "size", "ts", "ts_source", "model", "width", "height",
                                            "duration", "phash", "dhash")}
                    # не скачанный из облака файл не читаем ради хэша — иначе ждём скачивания
                    fp.update(name=p.name, source=str(self.root), thumb=thumbs.get(r["id"]),
                              sha1=r["sha1"] or (None if cloud_only(p) else self._sha1(p)))
                trash.remove(p, self.root)
                if fp:
                    journal.append(fp)
                removed.append(r["id"])
                freed += size
            except Exception as e:  # noqa: BLE001
                errors.append(f"{r['path']}: {e}")
        self.journal.record(journal)
        return removed, errors, freed

"""Телефон как источник: список файлов, копирование на компьютер, удаление, сверка с облаком.

iPhone (и Android) подключаются к Windows по MTP: у них нет буквы диска, поэтому работаем через
Windows Portable Devices (wpd.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
from pathlib import Path

from send2trash import send2trash

from . import matching, media
from .library import STATUS_REJECT, Library

COPY_LIMIT_FILES = 1500  # больше — спрашиваем, прежде чем копировать с телефона
COPY_LIMIT_BYTES = 10 * 1024 ** 3
MEDIA_DIRS = ("DCIM", "Pictures", "Movies")  # Android; новые iPhone показывают папки по месяцам в корне
LIST_EXT = media.ALL_EXT | {".aae"}  # .AAE — правки iOS, удаляются вместе со снимком
PHONE_IO_LOCK = threading.RLock()


# ============================================================ устройства

class FolderDevice:
    """Папка, которая ведёт себя как телефон (для тестов и для карт памяти)."""

    def __init__(self, root: Path, name: str | None = None):
        self.root = Path(root).resolve()
        self.name = name or self.root.name
        self.key = "dir-" + hashlib.sha1(str(self.root).lower().encode()).hexdigest()[:10]

    def list_files(self, progress=None, cache=None, folder_filter=None) -> list[tuple[str, int, float, float | None]]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in filenames:
                p = Path(dirpath, f)
                st = p.stat()
                out.append((p.relative_to(self.root).as_posix(), st.st_size, st.st_mtime, None))
        return out

    def fetch(self, rels: list[str], dest_root: Path, progress=None) -> None:
        for i, rel in enumerate(rels, 1):
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / rel, dest)
            if progress:
                progress(i)

    def delete(self, rels: list[str], hashes: dict[str, str] | None = None) -> dict[str, str | None]:
        res = {}
        for rel in rels:
            try:
                expected = (hashes or {}).get(rel)
                if expected:
                    h = hashlib.sha1((self.root / rel).read_bytes()).hexdigest()
                    if h != expected:
                        res[rel] = "содержимое не совпало с облаком — не тронут"
                        continue
                (self.root / rel).unlink()
                res[rel] = None
            except OSError as e:
                res[rel] = str(e)
        return res


class WpdDevice:
    """Телефон по MTP через Windows Portable Devices (см. wpd.py)."""

    def __init__(self, device_id: str, name: str | None = None, serial: str | None = None):
        self.device_id = device_id
        self._local = threading.local()
        self._lock = threading.Lock()
        if not name or not serial:
            info = self._conn().device_info()
            name = name or info.get("name")
            serial = serial or info.get("serial")
        self.name = name or "Телефон"
        self.serial = serial
        self.key = "wpd-" + hashlib.sha1((serial or device_id).lower().encode()).hexdigest()[:10]
        self._ids: dict[str, tuple[str, int, str]] = {}  # rel -> (id объекта, размер, id папки)
        self.broken = False  # чтение файла для сверки сорвалось: связь с телефоном ненадёжна, дальше не удаляем

    def _conn(self):
        from . import wpd

        if not hasattr(self._local, "c"):
            self._local.c = wpd.Connection(self.device_id)
        return self._local.c

    @staticmethod
    def list_devices() -> list[dict]:
        from . import wpd

        out = []
        for dev_id in wpd.list_device_ids():
            try:
                info = wpd.Connection(dev_id).device_info()
            except Exception:  # noqa: BLE001 — устройство заблокировано или не доверяет компьютеру
                info = {}
            out.append({"name": info.get("name") or "Телефон", "path": dev_id, "serial": info.get("serial")})
        return out

    def list_files(self, progress=None, cache: dict | None = None,
                   folder_filter=None) -> list[tuple[str, int, float, float | None]]:
        """Обход телефона. Свойства всегда читаются с устройства заново: ID объектов на iPhone
        меняются после удалений, поэтому никакого кэша между вызовами нет (параметр cache игнорируется).
        folder_filter(rel) -> bool: в какие папки заходить (None — во все)."""
        with self._lock:
            c = self._conn()
            out: list[tuple[str, int, float, float | None]] = []
            ids: dict[str, tuple[str, int, str]] = {}
            props: dict[str, dict] = {}
            storages = c.properties_strict(c.children("DEVICE"))
            frontier = [(sid, r["name"]) for sid, r in storages.items()]
            while frontier:
                kids = [(k, fid, prefix) for fid, prefix in frontier for k in c.children(fid)]
                total = len(kids)
                props.update(c.properties_strict([k for k, _, _ in kids], (lambda n: progress(n, total)) if progress else None))
                nxt = []
                for k, fid, prefix in kids:
                    r = props.get(k)
                    if not r:
                        continue
                    rel = f"{prefix}/{r['name']}"
                    if r["folder"]:
                        if not r["name"].startswith(".") and (folder_filter is None or folder_filter(rel)):
                            nxt.append((k, rel))
                    elif os.path.splitext(r["name"])[1].lower() in LIST_EXT:
                        out.append((rel, r["size"], r["modified"] or 0.0, r["created"]))
                        ids[rel] = (k, r["size"], fid)
                frontier = nxt
            if folder_filter is None:
                self._ids = ids
            else:
                self._ids.update(ids)
            return out

    def _ensure_ids(self, rels: list[str]) -> None:
        if any(r not in self._ids for r in rels):
            self.list_files()

    def _resolve(self, c, rel: str, size: int | None) -> str | None:
        """Найти объект заново по пути (имена папок и файла) — без запомненных ID."""
        parts = rel.split("/")
        parent = "DEVICE"
        for i, part in enumerate(parts):
            kids = c.properties(c.children(parent))
            last = i == len(parts) - 1
            match = [k for k, r in kids.items() if r["name"] == part and r["folder"] != last
                     and (not last or size is None or r["size"] == size)]
            if len(match) != 1:
                return None
            parent = match[0]
        return parent

    def _checked_id(self, c, rel: str) -> tuple[str | None, str | None]:
        """ID объекта, который ТОЧНО является этим файлом (имя и размер сверены с устройством прямо сейчас)."""
        entry = self._ids.get(rel)
        if not entry:
            return None, 'нет в списке найденных файлов'
        oid, live = self._verified_path(c, rel)
        if not oid or live['folder'] or live['size'] != entry[1]:
            return None, 'файл на телефоне не найден или изменился — не тронут'
        self._ids[rel] = (oid, entry[1], entry[2])
        return oid, None

    def fetch(self, rels: list[str], dest_root: Path, progress=None) -> None:
        self._ensure_ids(rels)
        with self._lock:
            c = self._conn()
            for i, rel in enumerate(rels, 1):
                oid, err = self._checked_id(c, rel)
                if err:
                    raise FileNotFoundError(f"{rel}: {err}")
                dest = dest_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                c.read_file(oid, self._ids[rel][1], dest)
                if progress:
                    progress(i)

    def _verified_path(self, c, rel: str) -> tuple[str | None, dict | None]:
        """Fresh, complete directory reads. Missing parents/ambiguous paths are errors."""
        parts = rel.split('/')
        parent = 'DEVICE'
        for part in parts[:-1]:
            rows = c.properties_strict(c.children(parent))
            matches = [oid for oid, row in rows.items() if row['name'] == part and row['folder']]
            if len(matches) != 1:
                raise RuntimeError(f'Папка {part} не найдена однозначно — проверка невозможна')
            parent = matches[0]
        rows = c.properties_strict(c.children(parent))
        # Verify the parent is still accessible after enumeration, even if it was empty.
        parent_row = c.one_strict(parent)
        if len(parts) < 2 or not parent_row['folder'] or parent_row['name'] != parts[-2]:
            raise RuntimeError('Папка изменилась во время проверки')
        matches = [(oid, row) for oid, row in rows.items() if row['name'] == parts[-1]]
        if len(matches) > 1:
            raise RuntimeError('Несколько файлов с одинаковым именем — проверка невозможна')
        return matches[0] if matches else (None, None)

    def _delete_one(self, c, rel, expected):
        if self.broken:
            return 'предыдущая операция не подтверждена — не тронут'
        if rel not in self._ids:
            self.broken = True
            return 'нет в списке найденных на телефоне — не тронут'
        if not expected:
            self.broken = True
            return 'нет проверенного отпечатка содержимого — не тронут'
        sent = False
        try:
            oid, row = self._verified_path(c, rel)
            if not oid or row['folder'] or row['size'] != self._ids[rel][1]:
                self.broken = True
                return 'файл не найден или изменился — не тронут'
            identity = c.identity(oid)
            if c.sha1(oid, row['size']) != expected:
                self.broken = True
                return 'содержимое не совпало — не тронут'
            # The object may have been renumbered during the read. Recheck immediately
            # before the destructive command, without reusing the old scan's identity.
            live = c.one_strict(oid)
            if (c.identity(oid) != identity or
                    (live['name'], live['size'], live['folder']) != (row['name'], row['size'], row['folder'])):
                self.broken = True
                return 'объект изменился после сверки — не тронут'
            # No cached object ID is used for the deletion or its confirmation.
            sent = True
            c.delete([oid])
            after, _ = self._verified_path(c, rel)
            if after is not None:
                self.broken = True
                return 'удаление не подтверждено: файл всё ещё находится на телефоне'
            self._ids.pop(rel, None)
            return None
        except Exception as e:
            self.broken = True
            return (('удаление не подтверждено' if sent else 'ошибка проверки — не тронут') + f': {e}')

    def delete(self, rels: list[str], hashes: dict[str, str] | None = None) -> dict[str, str | None]:
        """Success requires a complete fresh listing without the target; errors stop the batch."""
        from . import wpd
        res = {}
        with PHONE_IO_LOCK, self._lock:
            try:
                c = wpd.Connection(self.device_id) if not self.broken else None
                if c and (not self.serial or c.device_info().get('serial') != self.serial):
                    raise RuntimeError('Устройство изменилось или его серийный номер недоступен; повторите поиск')
            except Exception as e:
                c = None
                self.broken = True
                connection_error = str(e)
            else:
                connection_error = None
            for rel in rels:
                expected = (hashes or {}).get(rel)
                journal = getattr(self, 'journal', None)
                audit_id = journal.begin_phone_delete(self.name, rel, expected) if journal else None
                err = ('ошибка подключения — не тронут: ' + connection_error) if connection_error else self._delete_one(c, rel, expected)
                res[rel] = err
                if journal:
                    journal.finish_phone_delete(audit_id, err)
        return res


def phone_db(data_dir: Path, device) -> Path:
    d = data_dir / "phones" / device.key
    d.mkdir(parents=True, exist_ok=True)
    (d / "name.txt").write_text(device.name, encoding="utf-8")
    return d / "index.db"


def list_with_cache(device, db_path: Path, progress=None, folder_filter=None) -> list[tuple[str, int, float, float | None]]:
    """Список файлов телефона — всегда свежий. Раньше здесь был кэш «ID объекта → свойства», но на iPhone
    ID перенумеровываются после удалений, и кэш приводил к удалению не тех файлов. Старый кэш стираем."""
    with sqlite3.connect(db_path, timeout=30) as c:
        c.execute("DROP TABLE IF EXISTS objcache")
    first = True
    return device.list_files((lambda n, total: progress(n, total, first)) if progress else None, None, folder_filter)


def companions(rel: str, all_rels) -> list[str]:
    """Файлы на телефоне, которые удаляются вместе со снимком: Live-видео, правки (.AAE, IMG_E…)."""
    folder, name = rel.rsplit("/", 1)
    m = re.match(r"(?i)img_([eo]?)(\d+)\.", name)
    if not m:
        return []
    edited, num = bool(m.group(1)), m.group(2)  # в облако попала правленая версия — оригинал тоже лишний
    out = []
    for r in all_rels:
        if r == rel or not r.startswith(folder + "/"):
            continue
        n = r.rsplit("/", 1)[1]
        mm = re.match(r"(?i)img_([eo]?)(\d+)\.(\w+)$", n)
        if not mm or mm.group(2) != num:
            continue
        ext = mm.group(3).lower()
        if edited or ext in ("aae", "mov") or mm.group(1):  # Live-видео, файл правок, правленая копия
            out.append(r)
    return out


# ============================================================ библиотека телефона

class PhoneLibrary(Library):
    kind = "phone"

    def __init__(self, device, data_dir: Path):
        d = data_dir / "phones" / device.key
        super().__init__(d / "mirror", data_dir, lib_dir=d)
        (d / "name.txt").write_text(device.name, encoding="utf-8")
        self.device = device
        self.device.journal = self.journal
        self.allow_copy = False  # пользователь разрешил копировать много файлов
        self._sizes: dict[str, int] = {}
        self._taken: dict[str, float | None] = {}

    @property
    def title(self) -> str:
        return self.device.name

    def trash_target(self) -> str:
        return "phone"

    # ---- сканирование

    def _analyze(self, mid, rel, p, ext, kind, cloud=False):
        info = super()._analyze(mid, rel, p, ext, kind, cloud)
        if not cloud:
            info['sha1'] = self._sha1(p)
        return info

    def _store(self, c, mid, info):
        super()._store(c, mid, info)
        if info.get('sha1'):
            c.execute('UPDATE media SET sha1=? WHERE id=?', (info['sha1'], mid))
    def _walk(self):
        self._set(message="Чтение списка файлов с телефона…")
        lst = list_with_cache(self.device, self.db_path,
                              lambda n, total, first: self._set(
                                  phase="list", done=n, total=total,
                                  message="Чтение списка с телефона" + (" (первый раз — долго)" if first else "")))
        self._set(phase="walk", total=0, done=0)
        files = []
        for rel, size, mtime, _taken in lst:
            ext = os.path.splitext(rel)[1].lower()
            if ext in LIST_EXT:
                files.append((rel, self.root / rel, size, mtime, ext))
        self._sizes = {f[0]: f[1] for f in lst}
        self._taken = {f[0]: f[3] for f in lst}
        total = sum(f[1] for f in lst)
        self._set(folder_size=total, folder_files=len(lst))
        self.save_settings({"folder_size": total, "folder_files": len(lst)})
        return files

    def _prepare(self, todo: list[tuple]) -> None:
        self._quick_match(todo)
        need = [t[1] for t in todo if not (self.root / t[1]).exists()]
        if not need:
            return
        need_bytes = sum(self._sizes.get(r, 0) for r in need)
        if not self.allow_copy and (len(need) > COPY_LIMIT_FILES or need_bytes > COPY_LIMIT_BYTES):
            # Скорее всего, папка облака ещё не открыта в программе — не копируем весь телефон молча
            self._set(phase="confirm", total=len(need), done=0, copy_bytes=need_bytes,
                      message=f"С облаком не совпало {len(need)} файлов — нужно подтверждение")
            self._cancel.set()
            return
        self._set(phase="copy", total=len(need), done=0, message="Копирование с телефона…")
        for i in range(0, len(need), 100):
            if self._cancel.is_set():
                return
            base = i
            self.device.fetch(need[i:i + 100], self.root, lambda n: self._set(done=base + n))

    def _quick_match(self, todo: list[tuple]) -> None:
        """Снимки, чья копия уже есть в облаке (тот же размер и дата), не копируем с телефона:
        данные и миниатюру берём у копии из облака. todo изменяется на месте."""
        if not todo:
            return
        self._set(phase="match", message="Сверка списка файлов с облаком…")
        roots = self.ref_roots()
        refs = matching.load_refs(self.data_dir, set(roots) if roots is not None else None, self.journal.fingerprints())
        by_size: dict[int, list] = {}
        for r in refs:
            if r.size:
                by_size.setdefault(r.size, []).append(r)
        rest = []
        with self.connect() as c:
            for t in todo:
                mid, rel, _, ext, kind = t
                ref = matching.quick_match(self._sizes.get(rel, 0), self._taken.get(rel), by_size)
                if ref is None or ref.kind != kind:
                    rest.append(t)
                    continue
                c.execute(
                    """UPDATE media SET width=?, height=?, ts=?, ts_source=?, model=?, duration=?, phash=?, dhash=?,
                       sha1=?, category=?, analyzed=1, error=NULL WHERE id=?""",
                    (ref.width, ref.height, ref.ts, ref.ts_source, ref.model, ref.duration, ref.phash, ref.dhash,
                     ref.sha1, ref.category or ("video" if kind == "video" else "photo"), mid))
                thumb = matching.ref_thumb(ref, self.journal)
                if thumb:
                    c.execute("INSERT OR REPLACE INTO thumbs(id, data) VALUES(?, ?)", (mid, thumb))
        self._set(message=f"Уже есть в облаке: {len(todo) - len(rest)}")
        todo[:] = rest

    def local_path(self, rel: str) -> Path:
        p = self.root / rel
        if not p.exists():
            self.device.fetch([rel], self.root)
        return p

    def after_scan(self) -> None:
        # Older indexes did not retain hashes for every locally analysed photo.
        with self.connect() as c:
            missing = c.execute('SELECT id,path FROM media WHERE parent_id IS NULL AND sha1 IS NULL').fetchall()
            for row in missing:
                local = self.root / row['path']
                if local.is_file():
                    c.execute('UPDATE media SET sha1=? WHERE id=?', (self._sha1(local), row['id']))
        self._set(phase="match", message="Сверка с облаком…")
        self.rematch(premark=True)
        self._prune_mirror()

    # ---- сверка
    def ref_roots(self) -> list[str] | None:
        with self.connect() as c:
            r = c.execute("SELECT value FROM settings WHERE key='ref_roots'").fetchone()
        return json.loads(r[0]) if r else None

    def set_ref_roots(self, roots: list[str] | None) -> None:
        with self.connect() as c:
            if roots is None:
                c.execute("DELETE FROM settings WHERE key='ref_roots'")
            else:
                c.execute("INSERT INTO settings(key, value) VALUES('ref_roots', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(roots, ensure_ascii=False),))

    def rematch(self, premark: bool = True) -> dict:
        roots = self.ref_roots()
        refs = matching.load_refs(self.data_dir, set(roots) if roots is not None else None, self.journal.fingerprints())
        with self.connect() as c:
            rows = [dict(r) for r in c.execute(
                """SELECT id, kind, ts, ts_source, model, width, height, duration, size, sha1, phash, dhash, status, match
                   FROM media WHERE parent_id IS NULL AND analyzed=1""")]
            res = matching.match(rows, refs)
            marked = []
            for r in rows:
                m = res.get(r["id"])
                group, ref, strong = m if m else ("new", None, None)
                c.execute("UPDATE media SET match=?, match_ref=?, match_strong=? WHERE id=?",
                          (group, ref, int(bool(strong)) if m else None, r["id"]))
                # Удалённые в облаке — сразу к удалению (только при надёжном совпадении и если это новость)
                if premark and group == "deleted" and strong and r["status"] == 0 and r["match"] != "deleted":
                    marked.append(r["id"])
            for mid in marked:
                c.execute("UPDATE media SET status=? WHERE id=? OR parent_id=?", (STATUS_REJECT, mid, mid))
        self.status["version"] += 1
        counts = {"deleted": 0, "cloud": 0, "new": 0}
        for r in rows:
            counts[res[r["id"]][0] if r["id"] in res else "new"] += 1
        counts["premarked"] = len(marked)
        counts["refs"] = len(refs)
        return counts

    def _prune_mirror(self) -> None:
        """Копии снимков, которые уже есть в облаке или удалены там, на компьютере не нужны."""
        with self.connect() as c:
            rows = c.execute(
                """SELECT m.path FROM media m LEFT JOIN media p ON p.id = m.parent_id
                   WHERE COALESCE(p.match, m.match) IN ('cloud', 'deleted')""").fetchall()
        for r in rows:
            (self.root / r["path"]).unlink(missing_ok=True)

    # ---- удаление с телефона
    def _remove(self, rows: list[dict]) -> tuple[list[int], list[str], int]:
        groups = {r["id"]: r["match"] for r in rows if r["parent_id"] is None}
        errors: list[str] = []
        to_delete: list[dict] = []
        hashes = {}
        for r in rows:
            if r["parent_id"] is not None:
                errors.append(f"{r['path']}: сопутствующий файл не тронут; для удаления выберите его отдельно")
                continue
            if r.get("sha1"):
                hashes[r["path"]] = r["sha1"]
            else:
                errors.append(f"{r['path']}: нет отпечатка на момент отбора; пересканируйте телефон — не тронут")
                continue
            group = r["match"] if r["parent_id"] is None else groups.get(r["parent_id"])
            local = self.root / r["path"]
            if group not in ("cloud", "deleted"):
                # Этого снимка нет в облаке: перед удалением с телефона кладём копию в Корзину Windows
                try:
                    self.local_path(r["path"])
                    actual_hash = self._sha1(local)
                    if hashes.get(r["path"]) and hashes[r["path"]] != actual_hash:
                        raise RuntimeError("локальная копия изменилась после отбора")
                    hashes[r["path"]] = actual_hash
                    send2trash(str(local))
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{r['path']}: не удалось сохранить копию в Корзину, файл не тронут ({e})")
                    continue
            to_delete.append(r)
        self.device.broken = False
        result = self.device.delete([r["path"] for r in to_delete], hashes) if to_delete else {}
        removed, freed = [], 0
        for r in to_delete:
            err = result.get(r["path"], "результат удаления не получен")
            if err:
                errors.append(f"{r['path']}: {err}")
                continue
            removed.append(r["id"])
            freed += self._sizes.get(r["path"], r["size"] or 0)
            (self.root / r["path"]).unlink(missing_ok=True)
        return removed, errors, freed

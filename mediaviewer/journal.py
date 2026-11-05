"""Журнал удалений: «отпечатки» всех файлов, удалённых из папок (облака).

Сами файлы не хранятся — только то, по чему потом можно узнать их копию на телефоне:
дата съёмки, камера, разрешение, размер, SHA-1, перцептивные хэши и миниатюра для показа.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS deleted(
  id INTEGER PRIMARY KEY,
  name TEXT, path TEXT, source TEXT,
  kind TEXT, size INTEGER, sha1 TEXT,
  ts REAL, ts_source TEXT, model TEXT, width INTEGER, height INTEGER, duration REAL,
  phash INTEGER, dhash INTEGER,
  deleted_at REAL,
  thumb BLOB
);
CREATE INDEX IF NOT EXISTS ix_deleted_ts ON deleted(ts);
CREATE INDEX IF NOT EXISTS ix_deleted_sha1 ON deleted(sha1);
CREATE TABLE IF NOT EXISTS cleanups(
  id INTEGER PRIMARY KEY,
  at REAL,
  source TEXT,          -- папка (или телефон в режиме «Телефон»), из корзины которой удаляли
  what TEXT,            -- cloud — файлы папки, phone — файлы на телефоне
  device TEXT,          -- имя телефона для what=phone
  files INTEGER, bytes INTEGER,
  estimated INTEGER NOT NULL DEFAULT 0  -- восстановлено из журнала (без парных RAW/Live)
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS phone_deletions(
  id INTEGER PRIMARY KEY, at REAL, finished REAL, device TEXT, path TEXT,
  sha1 TEXT, status TEXT NOT NULL, error TEXT
);
"""

FIELDS = ("name", "path", "source", "kind", "size", "sha1", "ts", "ts_source", "model", "width", "height",
          "duration", "phash", "dhash", "thumb")


class Journal:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)
            self._backfill(c)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            with c:
                yield c
        finally:
            c.close()

    def record(self, rows: list[dict]) -> None:
        if not rows:
            return
        now = time.time()
        with self.connect() as c:
            c.executemany(
                f"INSERT INTO deleted({', '.join(FIELDS)}, deleted_at) VALUES({', '.join('?' * len(FIELDS))}, ?)",
                [tuple(r.get(k) for k in FIELDS) + (now,) for r in rows])

    def _backfill(self, c) -> None:
        """Однократно (при первом запуске с учётом): история очисток из журнала удалений."""
        if c.execute("SELECT 1 FROM meta WHERE key='backfilled'").fetchone():
            return
        c.execute("""INSERT INTO cleanups(at, source, what, files, bytes, estimated)
                     SELECT MIN(deleted_at), source, 'cloud', COUNT(*), SUM(size), 1 FROM deleted
                     GROUP BY source, CAST(deleted_at / 60 AS INTEGER)""")
        c.execute("INSERT INTO meta(key, value) VALUES('backfilled', '1')")

    def record_cleanup(self, source: str, what: str, files: int, size: int, device: str | None = None) -> None:
        if not files:
            return
        with self.connect() as c:
            c.execute("INSERT INTO cleanups(at, source, what, device, files, bytes) VALUES(?, ?, ?, ?, ?, ?)",
                      (time.time(), source, what, device, files, size))

    def stats(self, source: str | None) -> dict:
        with self.connect() as c:
            totals = [dict(r) for r in c.execute(
                """SELECT source, what, SUM(files) AS files, SUM(bytes) AS bytes, COUNT(*) AS sessions,
                          MIN(at) AS first, MAX(at) AS last, MAX(estimated) AS estimated
                   FROM cleanups GROUP BY source, what ORDER BY MAX(at) DESC""")]
            history = [dict(r) for r in c.execute(
                "SELECT at, what, device, files, bytes, estimated FROM cleanups WHERE source=? ORDER BY at DESC LIMIT 100",
                (source,))] if source else []
        return {"totals": totals, "history": history}

    def count(self) -> int:
        with self.connect() as c:
            return c.execute("SELECT COUNT(*) FROM deleted").fetchone()[0]

    def begin_phone_delete(self, device, path, sha1):
        with self.connect() as c:
            return c.execute("INSERT INTO phone_deletions(at,device,path,sha1,status) VALUES(?,?,?,?, 'pending')",
                             (time.time(), device, path, sha1)).lastrowid

    def finish_phone_delete(self, aid, error):
        with self.connect() as c:
            c.execute("UPDATE phone_deletions SET finished=?,status=?,error=? WHERE id=?",
                      (time.time(), 'unconfirmed' if error else 'confirmed', error, aid))

    def phone_history(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM phone_deletions ORDER BY id DESC LIMIT 500')]

    def fingerprints(self) -> list[dict]:
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                """SELECT id, name, path, source, kind, size, sha1, ts, ts_source, model, width, height, duration,
                   phash, dhash, deleted_at FROM deleted""")]

    def thumb(self, jid: int) -> bytes | None:
        with self.connect() as c:
            r = c.execute("SELECT thumb FROM deleted WHERE id=?", (jid,)).fetchone()
            return r[0] if r else None

"""Журнал удалений: «отпечатки» всех файлов, удалённых из папок (облака).

Сами файлы не хранятся — только то, по чему потом можно узнать их копию на телефоне:
дата съёмки, камера, разрешение, размер, SHA-1, перцептивные хэши и миниатюра для показа.
"""
from __future__ import annotations

import sqlite3
import time
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
"""

FIELDS = ("name", "path", "source", "kind", "size", "sha1", "ts", "ts_source", "model", "width", "height",
          "duration", "phash", "dhash", "thumb")


class Journal:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def record(self, rows: list[dict]) -> None:
        if not rows:
            return
        now = time.time()
        with self.connect() as c:
            c.executemany(
                f"INSERT INTO deleted({', '.join(FIELDS)}, deleted_at) VALUES({', '.join('?' * len(FIELDS))}, ?)",
                [tuple(r.get(k) for k in FIELDS) + (now,) for r in rows])

    def count(self) -> int:
        with self.connect() as c:
            return c.execute("SELECT COUNT(*) FROM deleted").fetchone()[0]

    def fingerprints(self) -> list[dict]:
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                """SELECT id, name, path, source, kind, size, sha1, ts, ts_source, model, width, height, duration,
                   phash, dhash, deleted_at FROM deleted""")]

    def thumb(self, jid: int) -> bytes | None:
        with self.connect() as c:
            r = c.execute("SELECT thumb FROM deleted WHERE id=?", (jid,)).fetchone()
            return r[0] if r else None

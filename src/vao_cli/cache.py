from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any


class PersistentCache:
    """Small SQLite cache for bounded Zenodo metadata and ZIP index ranges."""

    maximum_value = 16 * 1024 * 1024

    def __init__(self, path: Path, *, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        if not enabled:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS http_cache (
                       cache_key TEXT PRIMARY KEY,
                       kind TEXT NOT NULL,
                       value BLOB NOT NULL,
                       created_at REAL NOT NULL,
                       expires_at REAL NOT NULL,
                       hits INTEGER NOT NULL DEFAULT 0
                   )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS http_cache_expiry ON http_cache(expires_at)"
            )

    @staticmethod
    def key(kind: str, identity: str) -> str:
        return hashlib.sha256(f"{kind}\0{identity}".encode()).hexdigest()

    def get(self, kind: str, identity: str) -> bytes | None:
        if not self.enabled:
            return None
        key = self.key(kind, identity)
        now = time.time()
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT value, expires_at FROM http_cache WHERE cache_key=? AND kind=?",
                (key, kind),
            ).fetchone()
            if row is None:
                return None
            if float(row[1]) <= now:
                db.execute("DELETE FROM http_cache WHERE cache_key=?", (key,))
                return None
            db.execute("UPDATE http_cache SET hits=hits+1 WHERE cache_key=?", (key,))
            return bytes(row[0])

    def put(self, kind: str, identity: str, value: bytes, *, ttl: float) -> None:
        if not self.enabled or len(value) > self.maximum_value or ttl <= 0:
            return
        key = self.key(kind, identity)
        now = time.time()
        with sqlite3.connect(self.path) as db:
            db.execute(
                """INSERT INTO http_cache VALUES (?, ?, ?, ?, ?, 0)
                   ON CONFLICT(cache_key) DO UPDATE SET
                     kind=excluded.kind, value=excluded.value,
                     created_at=excluded.created_at, expires_at=excluded.expires_at""",
                (key, kind, value, now, now + ttl),
            )

    def stats(self) -> dict[str, Any]:
        if not self.enabled or not self.path.exists():
            return {
                "enabled": self.enabled,
                "path": str(self.path),
                "entries": 0,
                "bytes": 0,
            }
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(value)),0), COALESCE(SUM(hits),0), MIN(created_at), MAX(expires_at) FROM http_cache"
            ).fetchone()
        return {
            "enabled": self.enabled,
            "path": str(self.path),
            "entries": int(row[0]),
            "bytes": int(row[1]),
            "hits": int(row[2]),
            "oldestCreatedAt": row[3],
            "latestExpiry": row[4],
        }

    def prune(self) -> int:
        if not self.enabled or not self.path.exists():
            return 0
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "DELETE FROM http_cache WHERE expires_at <= ?", (time.time(),)
            ).rowcount

    def clear(self) -> int:
        if not self.enabled or not self.path.exists():
            return 0
        with sqlite3.connect(self.path) as db:
            return db.execute("DELETE FROM http_cache").rowcount

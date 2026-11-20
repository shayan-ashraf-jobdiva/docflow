from __future__ import annotations
"""SQLite persistence. Stands in for PostgreSQL (RDS)."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,
    upload_token TEXT,
    upload_expires REAL,
    file_path TEXT,
    size INTEGER,
    result TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_token ON jobs(upload_token);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL,
    receive_count INTEGER NOT NULL DEFAULT 0,
    visible_at REAL NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'ready'
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = str(path)
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def tx(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def query(self, sql, params=()):
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def execute(self, sql, params=()):
        with self.tx() as c:
            return c.execute(sql, params).rowcount


class Jobs:
    """Job repository. Every write invalidates the cache entry (cache-aside)."""

    def __init__(self, db: Database, cache):
        self.db = db
        self.cache = cache

    def create(self, user: str, filename: str, token: str, ttl: float) -> str:
        job_id = uuid.uuid4().hex
        now = time.time()
        self.db.execute(
            "INSERT INTO jobs (id,user,filename,status,upload_token,upload_expires,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (job_id, user, filename, "AWAITING_UPLOAD", token, now + ttl, now, now),
        )
        return job_id

    def row(self, job_id: str):
        rows = self.db.query("SELECT * FROM jobs WHERE id=?", (job_id,))
        return rows[0] if rows else None

    def by_token(self, token: str):
        rows = self.db.query("SELECT * FROM jobs WHERE upload_token=?", (token,))
        return rows[0] if rows else None

    def list_for(self, user: str):
        return self.db.query("SELECT * FROM jobs WHERE user=? ORDER BY created_at DESC LIMIT 100", (user,))

    def mark_uploaded(self, job_id: str, path: str, size: int):
        self._update(job_id, status="QUEUED", file_path=path, size=size, upload_token=None)

    def set_status(self, job_id: str, status: str, error: str | None = None):
        self._update(job_id, status=status, error=error)

    def complete(self, job_id: str, result: dict):
        self._update(job_id, status="DONE", result=json.dumps(result), error=None)

    def fail(self, job_id: str, error: str):
        self._update(job_id, status="FAILED", error=error)

    _UPDATABLE = {"status", "error", "result", "file_path", "size", "upload_token", "updated_at"}

    def _update(self, job_id: str, **fields):
        fields["updated_at"] = time.time()
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ValueError("unknown column(s): %s" % ", ".join(sorted(unknown)))
        cols = ", ".join(f"{k}=?" for k in fields)
        # column names are checked against the allowlist above; values are bound
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))  # nosec B608
        self.cache.delete(f"job:{job_id}")

    @staticmethod
    def public(row) -> dict:
        return {
            "id": row["id"],
            "user": row["user"],
            "filename": row["filename"],
            "status": row["status"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

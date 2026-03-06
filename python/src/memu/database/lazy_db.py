from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class LazyDatabase:
    def __init__(self, db_path: str, idle_timeout: int = 300) -> None:
        self.db_path = str(Path(db_path).expanduser())
        self.idle_timeout = idle_timeout
        self._conn: sqlite3.Connection | None = None
        self._last_access: float = 0.0
        self._lock = threading.RLock()
        self._ensure_parent_dir()

    def _ensure_parent_dir(self) -> None:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

    def _is_idle(self, now: float | None = None) -> bool:
        if self._conn is None:
            return False
        t = now if now is not None else time.time()
        return (t - self._last_access) > self.idle_timeout

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        logger.debug("Opened lazy sqlite connection: %s", self.db_path)
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
                    self._last_access = 0.0
                    logger.debug("Closed lazy sqlite connection: %s", self.db_path)

    def get_connection(self) -> sqlite3.Connection:
        with self._lock:
            now = time.time()
            if self._conn is None:
                self._conn = self._open()
            elif self._is_idle(now):
                self.close()
                self._conn = self._open()
            self._last_access = now
            return self._conn

    def ensure_open(self) -> None:
        self.get_connection()


class ConnectionPool:
    def __init__(self, max_size: int = 20, idle_timeout: int = 300) -> None:
        self.max_size = max_size
        self._open_soft_limit = max(1, max_size - 1)
        self.idle_timeout = idle_timeout
        self._entries: OrderedDict[str, LazyDatabase] = OrderedDict()
        self._lock = threading.RLock()
        self._gate = threading.BoundedSemaphore(max_size)
        self._waiting = 0

    def _prune_idle_locked(self) -> None:
        now = time.time()
        stale_keys = [key for key, db in self._entries.items() if db._is_idle(now)]
        for key in stale_keys:
            db = self._entries.pop(key)
            db.close()

    def _evict_if_needed_locked(self) -> None:
        self._prune_idle_locked()
        while len(self._entries) >= self._open_soft_limit:
            key, db = self._entries.popitem(last=False)
            db.close()
            logger.warning("Connection pool exhausted, evicting least-recent DB: %s", key)

    def get_db(self, key: str, db_path: str) -> LazyDatabase:
        with self._lock:
            self._prune_idle_locked()
            db = self._entries.get(key)
            if db is not None:
                self._entries.move_to_end(key)
                return db

            if len(self._entries) >= self._open_soft_limit:
                self._waiting += 1
                logger.warning("Connection pool exhausted, queueing request for key=%s", key)
                try:
                    self._evict_if_needed_locked()
                finally:
                    self._waiting -= 1

            db = LazyDatabase(db_path=db_path, idle_timeout=self.idle_timeout)
            self._entries[key] = db
            return db

    def touch(self, key: str) -> None:
        with self._lock:
            db = self._entries.get(key)
            if db is None:
                return
            self._entries.move_to_end(key)

    @contextmanager
    def checkout(self, key: str, db_path: str) -> Iterator[LazyDatabase]:
        acquired = self._gate.acquire(blocking=False)
        if not acquired:
            with self._lock:
                self._waiting += 1
            logger.warning("Connection pool exhausted, queueing request for key=%s", key)
            self._gate.acquire()
            with self._lock:
                self._waiting -= 1

        try:
            db = self.get_db(key, db_path)
            yield db
        finally:
            self._gate.release()

    def close_all(self) -> None:
        with self._lock:
            for db in self._entries.values():
                db.close()
            self._entries.clear()

    @property
    def open_connection_count(self) -> int:
        with self._lock:
            return sum(1 for db in self._entries.values() if db._conn is not None)

    @property
    def managed_database_count(self) -> int:
        with self._lock:
            return len(self._entries)


@contextmanager
def sqlite_cursor(db: LazyDatabase):
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        yield conn, cur
    finally:
        cur.close()


def execute_with_locked_retry(
    db: LazyDatabase,
    sql: str,
    params: tuple[object, ...] | None = None,
    *,
    max_retries: int = 3,
) -> None:
    backoffs = (0.1, 0.2, 0.4)
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with sqlite_cursor(db) as (conn, cur):
                cur.execute(sql, params or ())
                conn.commit()
                return
        except sqlite3.OperationalError as exc:
            last_exc = exc
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            if attempt >= max_retries:
                raise
            time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
    if last_exc:
        raise last_exc


def fetch_with_locked_retry(
    db: LazyDatabase,
    sql: str,
    params: tuple[object, ...] | None = None,
    *,
    max_retries: int = 3,
) -> list[tuple[object, ...]]:
    backoffs = (0.1, 0.2, 0.4)
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with sqlite_cursor(db) as (_conn, cur):
                cur.execute(sql, params or ())
                rows = cur.fetchall()
                return rows
        except sqlite3.OperationalError as exc:
            last_exc = exc
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            if attempt >= max_retries:
                raise
            time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
    if last_exc:
        raise last_exc
    return []


__all__ = [
    "ConnectionPool",
    "LazyDatabase",
    "execute_with_locked_retry",
    "fetch_with_locked_retry",
    "sqlite_cursor",
]

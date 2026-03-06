from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import sqlite3

import pytest

import docs_ingest
from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from tests.shared_user_model import SharedUserModel


class _FakeMemoryService:
    def __init__(self, *args, **kwargs) -> None:
        return None

    def _get_llm_client(self, _name: str) -> object:
        return object()


class _FakeManager:
    seed_docs: list[dict[str, object]] = []
    seed_chunks: list[dict[str, object]] = []
    latest: _FakeManager | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.docs = [dict(row) for row in self.__class__.seed_docs]
        self.chunks = [dict(row) for row in self.__class__.seed_chunks]
        self.ingest_calls: list[str] = []
        self.closed = False
        self.__class__.latest = self

    def get_shared_db(self):
        return self

    async def ingest_document(self, *, file_path: str, agent_id: str, user_id: str, embed_client: object):
        doc_id = f"new-doc-{len(self.ingest_calls) + 1}"
        self.ingest_calls.append(file_path)
        self.docs.append(
            {
                "id": doc_id,
                "filename": os.path.basename(file_path),
                "owner_agent_id": agent_id,
                "content_hash": None,
                "mtime": None,
                "created_at": len(self.docs) + 100,
            }
        )
        self.chunks.append({"id": f"chunk-{doc_id}", "document_id": doc_id})
        return SimpleNamespace(document_id=doc_id)

    def close(self) -> None:
        self.closed = True


class _FailingManager(_FakeManager):
    async def ingest_document(self, *, file_path: str, agent_id: str, user_id: str, embed_client: object):
        raise RuntimeError("ingest boom")


def _mock_fetch_with_locked_retry(db, sql: str, params=(), **kwargs):
    if "SELECT id, content_hash, mtime FROM documents" in sql:
        filename, owner_agent_id = params
        rows = [
            (row["id"], row.get("content_hash"), row.get("mtime"))
            for row in db.docs
            if row["filename"] == filename and row["owner_agent_id"] == owner_agent_id
        ]
        rows.sort(key=lambda x: next((d["created_at"] for d in db.docs if d["id"] == x[0]), 0), reverse=True)
        return rows
    raise AssertionError(f"Unexpected fetch SQL: {sql}")


def _mock_execute_with_locked_retry(db, sql: str, params=(), **kwargs):
    if sql.startswith("UPDATE documents SET content_hash = ?, mtime = ? WHERE id = ?"):
        content_hash, mtime, document_id = params
        for row in db.docs:
            if row["id"] == document_id:
                row["content_hash"] = content_hash
                row["mtime"] = mtime
        return
    if sql.startswith("DELETE FROM document_chunks WHERE document_id = ?"):
        document_id = params[0]
        db.chunks = [row for row in db.chunks if row["document_id"] != document_id]
        return
    if sql.startswith("DELETE FROM documents WHERE id = ?"):
        document_id = params[0]
        db.docs = [row for row in db.docs if row["id"] != document_id]
        return
    raise AssertionError(f"Unexpected execute SQL: {sql}")


def _run_docs_ingest_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, old_hash: str, old_mtime: float) -> _FakeManager:
    doc_path = tmp_path / "doc.md"
    doc_path.write_text("new document content", encoding="utf-8")
    new_hash = hashlib.sha256(doc_path.read_bytes()).hexdigest()
    new_mtime = float(os.path.getmtime(doc_path))

    _FakeManager.seed_docs = [
        {
            "id": "old-doc-1",
            "filename": "doc.md",
            "owner_agent_id": "main",
            "content_hash": old_hash,
            "mtime": old_mtime,
            "created_at": 1,
        }
    ]
    _FakeManager.seed_chunks = [{"id": "old-chunk-1", "document_id": "old-doc-1"}]
    _FakeManager.latest = None

    monkeypatch.setattr(docs_ingest, "MemoryService", _FakeMemoryService)
    monkeypatch.setattr(docs_ingest, "HybridDatabaseManager", _FakeManager)
    monkeypatch.setattr(docs_ingest, "fetch_with_locked_retry", _mock_fetch_with_locked_retry)
    monkeypatch.setattr(docs_ingest, "execute_with_locked_retry", _mock_execute_with_locked_retry)
    monkeypatch.setattr(docs_ingest, "migrate_legacy_single_db_to_agent_db", lambda default_agent="main": None)
    monkeypatch.setattr(docs_ingest, "_try_acquire_lock", lambda _path: object())
    monkeypatch.setattr(docs_ingest, "_release_lock", lambda _path, _fd: None)
    monkeypatch.setattr(docs_ingest, "_log", lambda _msg: None)

    monkeypatch.setenv("MEMU_EXTRA_PATHS", json.dumps([str(tmp_path)]))
    monkeypatch.setenv("MEMU_CHANGED_PATH", str(doc_path))
    monkeypatch.setenv("MEMU_DOC_OWNER_AGENT", "main")
    monkeypatch.setenv("MEMU_USER_ID", "u1")
    monkeypatch.setenv("MEMU_MEMORIZE_TIMEOUT_SECONDS", "30")

    asyncio.run(docs_ingest.main())
    manager = _FakeManager.latest
    assert manager is not None

    rows = [r for r in manager.docs if r["filename"] == "doc.md" and r["owner_agent_id"] == "main"]
    assert len(rows) == 1
    assert rows[0]["content_hash"] == new_hash
    assert abs(float(rows[0]["mtime"]) - new_mtime) < 1e-6
    return manager


def test_document_update_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = _run_docs_ingest_main(monkeypatch, tmp_path, old_hash="old-hash", old_mtime=1.0)
    assert manager.ingest_calls == [str(tmp_path / "doc.md")]
    assert all(row["id"] != "old-doc-1" for row in manager.docs)


def test_old_chunks_deleted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = _run_docs_ingest_main(monkeypatch, tmp_path, old_hash="old-hash", old_mtime=1.0)
    assert all(row["id"] != "old-chunk-1" for row in manager.chunks)


def test_new_chunks_indexed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = _run_docs_ingest_main(monkeypatch, tmp_path, old_hash="old-hash", old_mtime=1.0)
    assert any(str(row["id"]).startswith("chunk-new-doc-") for row in manager.chunks)


def test_content_hash_empty_file(tmp_path: Path) -> None:
    file_path = tmp_path / "empty.md"
    file_path.write_bytes(b"")
    expected = hashlib.sha256(b"").hexdigest()
    assert docs_ingest._calculate_content_hash(str(file_path)) == expected


def test_content_hash_large_file(tmp_path: Path) -> None:
    file_path = tmp_path / "large.md"
    payload = b"abc123" * (1024 * 512)
    file_path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert docs_ingest._calculate_content_hash(str(file_path)) == expected


def test_env_and_path_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEST_X", "  value ")
    assert docs_ingest._env("TEST_X", "d") == "  value "
    monkeypatch.setenv("TEST_X", "   ")
    assert docs_ingest._env("TEST_X", "d") == "d"

    inside = tmp_path / "a" / "b.md"
    inside.parent.mkdir(parents=True)
    inside.write_text("x", encoding="utf-8")
    assert docs_ingest._is_under_prefix(str(inside), str(tmp_path / "a")) is True
    assert docs_ingest._is_under_prefix(str(inside), str(tmp_path / "other")) is False


def test_collect_markdown_files_modes(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    p1 = root / "a.md"
    p2 = root / "b.txt"
    sub = root / "sub"
    sub.mkdir()
    p3 = sub / "c.md"
    p1.write_text("a", encoding="utf-8")
    p2.write_text("b", encoding="utf-8")
    p3.write_text("c", encoding="utf-8")

    all_files = docs_ingest._collect_markdown_files(extra_paths=[str(root)], changed_path=None)
    assert all_files == sorted([str(p1.resolve()), str(p3.resolve())])

    changed_file = docs_ingest._collect_markdown_files(extra_paths=[str(root)], changed_path=str(p1))
    assert changed_file == [str(p1.resolve())]

    changed_dir = docs_ingest._collect_markdown_files(extra_paths=[str(root)], changed_path=str(sub))
    assert changed_dir == [str(p3.resolve())]

    outside = docs_ingest._collect_markdown_files(extra_paths=[str(root)], changed_path=str(tmp_path / "outside.md"))
    assert outside == []


def test_get_extra_paths_and_markers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMU_EXTRA_PATHS", json.dumps(["a", 1, "b"]))
    assert docs_ingest.get_extra_paths() == ["a", "b"]

    monkeypatch.setenv("MEMU_EXTRA_PATHS", "not-json")
    assert docs_ingest.get_extra_paths() == []

    monkeypatch.delenv("MEMU_DATA_DIR", raising=False)
    assert docs_ingest._full_scan_marker_path() is None

    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path))
    assert docs_ingest._full_scan_marker_path() == str(tmp_path / "docs_full_scan.marker")


def test_language_config_and_db_dsn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg_default = docs_ingest._build_language_aware_memorize_config("xx")
    assert cfg_default.memory_types == ["profile", "event", "knowledge", "behavior", "skill", "tool"]

    cfg_zh = docs_ingest._build_language_aware_memorize_config("zh")
    assert cfg_zh.memory_type_prompts is not None
    assert "profile" in cfg_zh.memory_type_prompts

    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path))
    dsn = docs_ingest.get_db_dsn()
    assert dsn.endswith("/memu.db")
    assert tmp_path.exists()


def test_lock_and_mtime_helpers(tmp_path: Path) -> None:
    lock = tmp_path / "sync.lock"
    fd = docs_ingest._try_acquire_lock(str(lock))
    assert fd is not None
    assert docs_ingest._try_acquire_lock(str(lock)) is None
    docs_ingest._release_lock(str(lock), fd)
    assert not lock.exists()

    assert docs_ingest._is_same_mtime(1.0, 1.0 + 1e-7)
    assert not docs_ingest._is_same_mtime(None, 1.0)
    assert not docs_ingest._is_same_mtime("bad", 1.0)


def test_shared_document_exists_branching(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SimpleNamespace()

    monkeypatch.setattr(
        docs_ingest,
        "_get_shared_document_rows",
        lambda *_args, **_kwargs: [("d1", None, None)],
    )
    exists, legacy, stale = docs_ingest._shared_document_exists(
        manager, filename="a.md", owner_agent_id="main", content_hash="h", mtime=1.0
    )
    assert exists is True and legacy == ["d1"] and stale == []

    monkeypatch.setattr(
        docs_ingest,
        "_get_shared_document_rows",
        lambda *_args, **_kwargs: [("d2", "h", 1.0)],
    )
    exists, legacy, stale = docs_ingest._shared_document_exists(
        manager, filename="a.md", owner_agent_id="main", content_hash="h", mtime=1.0
    )
    assert exists is True and legacy == [] and stale == []

    monkeypatch.setattr(
        docs_ingest,
        "_get_shared_document_rows",
        lambda *_args, **_kwargs: [("d3", "old", 0.0)],
    )
    exists, legacy, stale = docs_ingest._shared_document_exists(
        manager, filename="a.md", owner_agent_id="main", content_hash="new", mtime=2.0
    )
    assert exists is False and legacy == [] and stale == ["d3"]


def test_main_short_circuits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    logs: list[str] = []
    monkeypatch.setattr(docs_ingest, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(docs_ingest, "_try_acquire_lock", lambda _path: None)
    asyncio.run(docs_ingest.main())
    assert any("already running" in m for m in logs)

    monkeypatch.setattr(docs_ingest, "_try_acquire_lock", lambda _path: object())
    monkeypatch.setattr(docs_ingest, "_release_lock", lambda _path, _fd: None)
    monkeypatch.setattr(docs_ingest, "MemoryService", _FakeMemoryService)
    monkeypatch.setattr(docs_ingest, "HybridDatabaseManager", _FakeManager)
    monkeypatch.setattr(docs_ingest, "migrate_legacy_single_db_to_agent_db", lambda default_agent="main": None)
    monkeypatch.setenv("MEMU_EXTRA_PATHS", json.dumps([str(tmp_path / "missing")]))
    monkeypatch.setenv("MEMU_CHANGED_PATH", str(tmp_path / "missing"))
    asyncio.run(docs_ingest.main())
    assert any("no markdown files" in m for m in logs)


def test_main_legacy_backfill_and_unchanged_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doc_path = tmp_path / "doc.md"
    doc_path.write_text("stable", encoding="utf-8")
    current_hash = hashlib.sha256(doc_path.read_bytes()).hexdigest()
    current_mtime = float(os.path.getmtime(doc_path))

    for seed in (
        [{"id": "legacy", "filename": "doc.md", "owner_agent_id": "main", "content_hash": None, "mtime": None, "created_at": 1}],
        [{"id": "same", "filename": "doc.md", "owner_agent_id": "main", "content_hash": current_hash, "mtime": current_mtime, "created_at": 1}],
    ):
        _FakeManager.seed_docs = [dict(row) for row in seed]
        _FakeManager.seed_chunks = [{"id": "c1", "document_id": seed[0]["id"]}]
        _FakeManager.latest = None

        monkeypatch.setattr(docs_ingest, "MemoryService", _FakeMemoryService)
        monkeypatch.setattr(docs_ingest, "HybridDatabaseManager", _FakeManager)
        monkeypatch.setattr(docs_ingest, "fetch_with_locked_retry", _mock_fetch_with_locked_retry)
        monkeypatch.setattr(docs_ingest, "execute_with_locked_retry", _mock_execute_with_locked_retry)
        monkeypatch.setattr(docs_ingest, "migrate_legacy_single_db_to_agent_db", lambda default_agent="main": None)
        monkeypatch.setattr(docs_ingest, "_try_acquire_lock", lambda _path: object())
        monkeypatch.setattr(docs_ingest, "_release_lock", lambda _path, _fd: None)
        monkeypatch.setattr(docs_ingest, "_log", lambda _msg: None)
        monkeypatch.setenv("MEMU_EXTRA_PATHS", json.dumps([str(tmp_path)]))
        monkeypatch.setenv("MEMU_CHANGED_PATH", str(doc_path))
        monkeypatch.setenv("MEMU_DOC_OWNER_AGENT", "main")

        asyncio.run(docs_ingest.main())
        manager = _FakeManager.latest
        assert manager is not None
        assert manager.ingest_calls == []
        rows = [r for r in manager.docs if r["filename"] == "doc.md" and r["owner_agent_id"] == "main"]
        assert len(rows) == 1
        assert rows[0]["content_hash"] == current_hash
        assert abs(float(rows[0]["mtime"]) - current_mtime) < 1e-6


def test_main_ingest_failure_and_full_scan_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    doc_path = docs_dir / "doc.md"
    doc_path.write_text("content", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    logs: list[str] = []

    _FailingManager.seed_docs = []
    _FailingManager.seed_chunks = []
    _FailingManager.latest = None

    monkeypatch.setattr(docs_ingest, "MemoryService", _FakeMemoryService)
    monkeypatch.setattr(docs_ingest, "HybridDatabaseManager", _FailingManager)
    monkeypatch.setattr(docs_ingest, "fetch_with_locked_retry", _mock_fetch_with_locked_retry)
    monkeypatch.setattr(docs_ingest, "execute_with_locked_retry", _mock_execute_with_locked_retry)
    monkeypatch.setattr(docs_ingest, "migrate_legacy_single_db_to_agent_db", lambda default_agent="main": None)
    monkeypatch.setattr(docs_ingest, "_try_acquire_lock", lambda _path: object())
    monkeypatch.setattr(docs_ingest, "_release_lock", lambda _path, _fd: None)
    monkeypatch.setattr(docs_ingest, "_log", lambda msg: logs.append(msg))
    monkeypatch.delenv("MEMU_CHANGED_PATH", raising=False)
    monkeypatch.setenv("MEMU_EXTRA_PATHS", json.dumps([str(docs_dir)]))
    monkeypatch.setenv("MEMU_DOC_OWNER_AGENT", "main")
    monkeypatch.setenv("MEMU_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MEMU_CHAT_PROVIDER", "openai")
    monkeypatch.setenv("MEMU_CHAT_BASE_URL", "http://x")
    monkeypatch.setenv("MEMU_CHAT_API_KEY", "k")
    monkeypatch.setenv("MEMU_CHAT_MODEL", "m")
    monkeypatch.setenv("MEMU_EMBED_PROVIDER", "openai")
    monkeypatch.setenv("MEMU_EMBED_BASE_URL", "http://y")
    monkeypatch.setenv("MEMU_EMBED_API_KEY", "k2")
    monkeypatch.setenv("MEMU_EMBED_MODEL", "e")

    asyncio.run(docs_ingest.main())
    assert any("failed:" in m for m in logs)
    assert (data_dir / "docs_full_scan.marker").exists()


def test_log_and_lock_edge_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path))
    docs_ingest._log("hello")
    sync_log = tmp_path / "sync.log"
    assert sync_log.exists()
    assert "hello" in sync_log.read_text(encoding="utf-8")

    real_open = open

    def _boom_open(*args, **kwargs):
        raise OSError("no write")

    monkeypatch.setattr("builtins.open", _boom_open)
    docs_ingest._log("ignore-error")
    monkeypatch.setattr("builtins.open", real_open)

    lock = tmp_path / "edge.lock"
    lock.write_text("123", encoding="utf-8")
    monkeypatch.setattr(docs_ingest.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    fd = docs_ingest._try_acquire_lock(str(lock))
    assert fd is not None
    docs_ingest._release_lock(str(lock), fd)

    lock.write_text("123", encoding="utf-8")
    monkeypatch.setattr(docs_ingest.os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError()))
    assert docs_ingest._try_acquire_lock(str(lock)) is None

    real_close = docs_ingest.os.close
    lock2 = tmp_path / "edge2.lock"
    fd2 = docs_ingest._try_acquire_lock(str(lock2))
    assert fd2 is not None
    monkeypatch.setattr(docs_ingest.os, "close", lambda _fd: (_ for _ in ()).throw(OSError("bad close")))
    docs_ingest._release_lock(str(lock2), fd2)
    monkeypatch.setattr(docs_ingest.os, "close", real_close)


def test_documents_schema_migration_backfills_hash_and_mtime(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    shared_dir = memory_root / "shared"
    shared_dir.mkdir(parents=True)
    db_path = shared_dir / "memu.db"

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT NOT NULL, owner_agent_id TEXT NOT NULL, created_at INTEGER)"
        )
        conn.execute(
            (
                "CREATE TABLE document_chunks ("
                "id TEXT PRIMARY KEY, document_id TEXT NOT NULL, chunk_index INTEGER, start_token INTEGER DEFAULT 0, "
                "end_token INTEGER DEFAULT 0, content TEXT, embedding BLOB, filename TEXT, owner_agent_id TEXT, created_at INTEGER DEFAULT 0)"
            )
        )
        conn.execute(
            "INSERT INTO documents (id, filename, owner_agent_id, created_at) VALUES (?, ?, ?, ?)",
            ("doc-1", "doc.md", "main", 123),
        )
        conn.execute(
            (
                "INSERT INTO document_chunks (id, document_id, chunk_index, start_token, end_token, content, embedding, filename, owner_agent_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            ("c1", "doc-1", 0, 0, 0, "hello", None, "doc.md", "main", 123),
        )
        conn.execute(
            (
                "INSERT INTO document_chunks (id, document_id, chunk_index, start_token, end_token, content, embedding, filename, owner_agent_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            ("c2", "doc-1", 1, 0, 0, "world", None, "doc.md", "main", 123),
        )
        conn.commit()
    finally:
        conn.close()

    manager = HybridDatabaseManager(
        config=MemUConfig(),
        db_config=DatabaseConfig(),
        user_model=SharedUserModel,
        memory_root=str(memory_root),
    )
    try:
        db = manager.get_shared_db()
        from memu.database.lazy_db import fetch_with_locked_retry

        columns = fetch_with_locked_retry(db, "PRAGMA table_info(documents)", ())
        names = {str(row[1]) for row in columns if len(row) > 1}
        assert "content_hash" in names
        assert "mtime" in names

        rows = fetch_with_locked_retry(db, "SELECT content_hash, mtime FROM documents WHERE id = ?", ("doc-1",))
        assert rows
        stored_hash, stored_mtime = rows[0]
        expected_hash = hashlib.sha256("helloworld".encode("utf-8")).hexdigest()
        assert stored_hash == expected_hash
        assert float(stored_mtime) == 123.0
    finally:
        manager.close()

from __future__ import annotations

import os
import tempfile
import sqlite3
from pathlib import Path

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from memu.database.lazy_db import execute_with_locked_retry
from tests.shared_user_model import SharedUserModel


def _new_manager(*, allow_cross_agent: bool) -> HybridDatabaseManager:
    tmp_root = tempfile.mkdtemp(prefix="memu_access_control_")
    os.environ["MEMU_MEMORY_ROOT"] = tmp_root
    return HybridDatabaseManager(
        config=MemUConfig(),
        db_config=DatabaseConfig(),
        user_model=SharedUserModel,
    )


def _new_manager_with_legacy_nullable_owner(*, allow_cross_agent: bool) -> HybridDatabaseManager:
    tmp_root = tempfile.mkdtemp(prefix="memu_access_control_legacy_")
    os.environ["MEMU_MEMORY_ROOT"] = tmp_root
    shared_db_path = Path(tmp_root) / "shared" / "memu.db"
    shared_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(shared_db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            "id TEXT PRIMARY KEY, "
            "filename TEXT NOT NULL, "
            "owner_agent_id TEXT, "
            "created_at INTEGER"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS document_chunks ("
            "id TEXT PRIMARY KEY, "
            "document_id TEXT NOT NULL, "
            "chunk_index INTEGER, "
            "content TEXT, "
            "embedding BLOB, "
            "FOREIGN KEY (document_id) REFERENCES documents(id)"
            ")"
        )
        conn.commit()
    finally:
        conn.close()

    return HybridDatabaseManager(
        config=MemUConfig(),
        db_config=DatabaseConfig(),
        user_model=SharedUserModel,
    )


def _insert_doc_chunk(
    manager: HybridDatabaseManager,
    *,
    doc_id: str,
    chunk_id: str,
    owner_agent_id: str | None,
    content: str,
) -> None:
    db_path = manager._shared_db_path()
    with manager.connection_pool.checkout("shared", str(db_path)) as db:
        execute_with_locked_retry(
            db,
            "INSERT INTO documents (id, filename, owner_agent_id, created_at) VALUES (?, ?, ?, ?)",
            (doc_id, f"{doc_id}.md", owner_agent_id, 0),
        )
        execute_with_locked_retry(
            db,
            "INSERT INTO document_chunks (id, document_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?, ?)",
            (chunk_id, doc_id, 0, content, None),
        )


def test_cross_agent_access_denied() -> None:
    manager = _new_manager(allow_cross_agent=False)
    try:
        _insert_doc_chunk(
            manager,
            doc_id="doc-agent-a",
            chunk_id="chunk-agent-a",
            owner_agent_id="agent-a",
            content="project roadmap",
        )

        results = manager.search_documents(
            query="roadmap",
            requesting_agent_id="agent-b",
            allow_cross_agent=False,
        )

        assert len(results) == 0
    finally:
        manager.close()


def test_cross_agent_access_allowed() -> None:
    manager = _new_manager(allow_cross_agent=True)
    try:
        _insert_doc_chunk(
            manager,
            doc_id="doc-agent-a",
            chunk_id="chunk-agent-a",
            owner_agent_id="agent-a",
            content="shared handbook",
        )

        results = manager.search_documents(
            query="handbook",
            requesting_agent_id="agent-b",
            allow_cross_agent=True,
        )

        assert len(results) == 1
        assert results[0]["owner_agent_id"] == "agent-a"
    finally:
        manager.close()


def test_own_documents_always_accessible() -> None:
    manager = _new_manager(allow_cross_agent=False)
    try:
        _insert_doc_chunk(
            manager,
            doc_id="doc-agent-a-own",
            chunk_id="chunk-agent-a-own",
            owner_agent_id="agent-a",
            content="private notes",
        )

        results = manager.search_documents(
            query="private",
            requesting_agent_id="agent-a",
            allow_cross_agent=False,
        )

        assert len(results) == 1
        assert results[0]["owner_agent_id"] == "agent-a"
    finally:
        manager.close()


def test_null_owner_agent_id_treated_as_public() -> None:
    manager = _new_manager_with_legacy_nullable_owner(allow_cross_agent=False)
    try:
        _insert_doc_chunk(
            manager,
            doc_id="doc-public",
            chunk_id="chunk-public",
            owner_agent_id=None,
            content="legacy public document",
        )

        results = manager.search_documents(
            query="legacy",
            requesting_agent_id="agent-b",
            allow_cross_agent=False,
        )

        assert len(results) == 1
        assert results[0]["owner_agent_id"] is None
    finally:
        manager.close()

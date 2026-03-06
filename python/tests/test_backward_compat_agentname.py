from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from memu.database.lazy_db import execute_with_locked_retry
from memu.scope_model import AgentScopeModel
from tests.shared_user_model import SharedUserModel


def _new_manager_with_legacy_nullable_owner(*, tmp_root: str) -> HybridDatabaseManager:
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


def test_backward_compat_legacy_agentname_access_and_warning() -> None:
    previous_memory_root = os.environ.get("MEMU_MEMORY_ROOT")

    with tempfile.TemporaryDirectory(prefix="memu_backward_compat_agentname_") as tmp_root:
        manager = _new_manager_with_legacy_nullable_owner(tmp_root=tmp_root)
        try:
            _insert_doc_chunk(
                manager,
                doc_id="doc-legacy-public",
                chunk_id="chunk-legacy-public",
                owner_agent_id=None,
                content="compat-keyword legacy public row",
            )
            _insert_doc_chunk(
                manager,
                doc_id="doc-private-agent-a",
                chunk_id="chunk-private-agent-a",
                owner_agent_id="agent-a",
                content="compat-keyword private row",
            )

            assert "agentName" in AgentScopeModel.model_fields, (
                "Backward compatibility regression: AgentScopeModel must retain legacy agentName field"
            )

            results = manager.search_documents(
                query="compat-keyword",
                requesting_agent_id="agent-b",
                allow_cross_agent=False,
            )

            assert len(results) == 1, (
                "Legacy/public backward compatibility failed: rows with owner_agent_id IS NULL "
                "must remain visible in restricted mode"
            )
            assert results[0]["owner_agent_id"] is None, (
                "Legacy/public backward compatibility failed: expected owner_agent_id IS NULL row "
                "to stay accessible"
            )
        finally:
            manager.close()

    if previous_memory_root is None:
        os.environ.pop("MEMU_MEMORY_ROOT", None)
    else:
        os.environ["MEMU_MEMORY_ROOT"] = previous_memory_root

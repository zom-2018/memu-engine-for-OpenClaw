from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from scripts.migrate_agent_id import migrate_agent_id
from tests.shared_user_model import SharedUserModel


class _DeterministicEmbedClient:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def _create_migration_fixture(db_path: Path, config_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE memu_memory_items ("
            "id TEXT PRIMARY KEY, "
            "agentName TEXT, "
            "user_id TEXT, "
            "content TEXT"
            ")"
        )
        conn.execute(
            "INSERT INTO memu_memory_items (id, agentName, user_id, content) VALUES (?, ?, ?, ?)",
            ("item-1", "Agent Alpha", "user-1", "seed memory"),
        )
        conn.commit()
    finally:
        conn.close()

    config_payload = {
        "agents": {
            "list": [
                {"id": "agent-a", "name": "Agent Alpha"},
                {"id": "agent-b", "name": "Agent Beta"},
            ]
        }
    }
    config_path.write_text(json.dumps(config_payload), encoding="utf-8")


def test_full_hybrid_workflow() -> None:
    with tempfile.TemporaryDirectory(prefix="memu_hybrid_e2e_") as tmp_root:
        tmp_path = Path(tmp_root)
        migration_db = tmp_path / "migration.db"
        migration_config = tmp_path / "openclaw.json"

        _create_migration_fixture(migration_db, migration_config)
        migrate_agent_id(db_path=str(migration_db), config_path=str(migration_config), dry_run=False)

        conn = sqlite3.connect(migration_db)
        try:
            columns = conn.execute("PRAGMA table_info(memu_memory_items)").fetchall()
            column_names = {str(col[1]) for col in columns}
            assert "agent_id" in column_names, "Migration should add agent_id column to memu_memory_items"

            row = conn.execute("SELECT agent_id FROM memu_memory_items WHERE id = ?", ("item-1",)).fetchone()
            assert row is not None, "Expected seeded memu_memory_items row to exist after migration"
            assert row[0] == "agent-a", "Migration should backfill agent_id based on agentName mapping"
        finally:
            conn.close()

        previous_memory_root = os.environ.get("MEMU_MEMORY_ROOT")
        os.environ["MEMU_MEMORY_ROOT"] = str(tmp_path / "memory-root")

        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )

        try:
            doc_path = tmp_path / "hybrid_doc.md"
            doc_content = "Hybrid E2E secret phrase: blue-orchid-42"
            doc_path.write_text(doc_content, encoding="utf-8")

            ingest_result = asyncio.run(
                manager.ingest_document(
                    file_path=str(doc_path),
                    agent_id="agent-a",
                    user_id="user-1",
                    embed_client=_DeterministicEmbedClient(),
                )
            )
            assert ingest_result.chunk_count >= 1, "Document ingestion should produce at least one chunk"

            agent_a_results = manager.search_documents(
                query="blue-orchid-42",
                requesting_agent_id="agent-a",
                allow_cross_agent=False,
            )
            assert len(agent_a_results) >= 1, "Agent A should retrieve its own ingested document"
            assert any(
                result.get("owner_agent_id") == "agent-a" for result in agent_a_results
            ), "Agent A results should include a chunk owned by agent-a"

            agent_b_results = manager.search_documents(
                query="blue-orchid-42",
                requesting_agent_id="agent-b",
                allow_cross_agent=False,
            )
            assert len(agent_b_results) == 0, (
                "Agent B should not retrieve Agent A's document when cross-agent access is disabled"
            )
        finally:
            manager.close()
            if previous_memory_root is None:
                os.environ.pop("MEMU_MEMORY_ROOT", None)
            else:
                os.environ["MEMU_MEMORY_ROOT"] = previous_memory_root

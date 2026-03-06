"""
Test shared docs independence - P0-4 coverage gap.

Tests that docs_ingest writes only to shared DB and agent memories
don't pollute shared DB.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from memu.database.lazy_db import execute_with_locked_retry, fetch_with_locked_retry
from tests.shared_user_model import SharedUserModel


class _DeterministicEmbedClient:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def _count_shared_documents(manager: HybridDatabaseManager) -> int:
    db_path = manager._shared_db_path()
    with manager.connection_pool.checkout("shared", str(db_path)) as db:
        rows = fetch_with_locked_retry(db, "SELECT COUNT(*) FROM documents", ())
        return rows[0][0] if rows else 0


def _count_shared_chunks(manager: HybridDatabaseManager) -> int:
    db_path = manager._shared_db_path()
    with manager.connection_pool.checkout("shared", str(db_path)) as db:
        rows = fetch_with_locked_retry(db, "SELECT COUNT(*) FROM document_chunks", ())
        return rows[0][0] if rows else 0


def _count_agent_memories(manager: HybridDatabaseManager, agent_id: str) -> int:
    db_path = manager._agent_db_path(agent_id)
    manager._ensure_agent_schema(agent_id)
    with manager.connection_pool.checkout(f"agent:{agent_id}", str(db_path)) as db:
        rows = fetch_with_locked_retry(
            db,
            "SELECT COUNT(*) FROM memories WHERE agent_id = ?",
            (agent_id,),
        )
        return rows[0][0] if rows else 0


def _insert_agent_memory(
    manager: HybridDatabaseManager,
    *,
    memory_id: str,
    agent_id: str,
    user_id: str,
    content: str,
) -> None:
    db_path = manager._agent_db_path(agent_id)
    manager._ensure_agent_schema(agent_id)
    with manager.connection_pool.checkout(f"agent:{agent_id}", str(db_path)) as db:
        execute_with_locked_retry(
            db,
            "INSERT INTO memories (id, agent_id, user_id, content, embedding, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (memory_id, agent_id, user_id, content, None, 0),
        )


def test_docs_ingest_writes_only_to_shared_db() -> None:
    """
    Test P0-4: docs_ingest writes only to shared DB.
    
    When ingesting a document, it should write to shared/memu.db
    and not touch any agent-specific databases.
    """
    with tempfile.TemporaryDirectory(prefix="memu_ingest_shared_") as tmp_root:
        tmp_path = Path(tmp_root)
        os.environ["MEMU_MEMORY_ROOT"] = str(tmp_path)
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            agent_id = "ingest-agent"
            
            doc_path = tmp_path / "test_doc.md"
            doc_path.write_text("Test document content for ingestion", encoding="utf-8")
            
            shared_docs_before = _count_shared_documents(manager)
            shared_chunks_before = _count_shared_chunks(manager)
            agent_memories_before = _count_agent_memories(manager, agent_id)
            
            result = asyncio.run(
                manager.ingest_document(
                    file_path=str(doc_path),
                    agent_id=agent_id,
                    user_id="user-1",
                    embed_client=_DeterministicEmbedClient(),
                )
            )
            
            shared_docs_after = _count_shared_documents(manager)
            shared_chunks_after = _count_shared_chunks(manager)
            agent_memories_after = _count_agent_memories(manager, agent_id)
            
            assert shared_docs_after > shared_docs_before, "Should add document to shared DB"
            assert shared_chunks_after > shared_chunks_before, "Should add chunks to shared DB"
            assert agent_memories_after == agent_memories_before, "Should NOT add to agent DB"
            
        finally:
            manager.close()


def test_agent_memories_dont_pollute_shared_db() -> None:
    """
    Test P0-4: Agent memories don't pollute shared DB.
    
    When writing agent-specific memories, they should go to the
    agent's private DB and not appear in shared/memu.db.
    """
    with tempfile.TemporaryDirectory(prefix="memu_agent_isolation_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            agent_id = "isolated-agent"
            
            shared_docs_before = _count_shared_documents(manager)
            shared_chunks_before = _count_shared_chunks(manager)
            
            _insert_agent_memory(
                manager,
                memory_id="mem-isolated",
                agent_id=agent_id,
                user_id="user-1",
                content="private agent memory",
            )
            
            shared_docs_after = _count_shared_documents(manager)
            shared_chunks_after = _count_shared_chunks(manager)
            agent_memories = _count_agent_memories(manager, agent_id)
            
            assert shared_docs_after == shared_docs_before, "Agent memory should NOT add to shared documents"
            assert shared_chunks_after == shared_chunks_before, "Agent memory should NOT add to shared chunks"
            assert agent_memories == 1, "Agent memory should be in agent DB"
            
        finally:
            manager.close()


def test_shared_docs_accessible_to_all_agents() -> None:
    """
    Test P0-4: Shared docs accessible to all agents.
    
    Documents in shared DB should be accessible to all agents
    when cross-agent retrieval is enabled.
    """
    with tempfile.TemporaryDirectory(prefix="memu_shared_access_") as tmp_root:
        tmp_path = Path(tmp_root)
        os.environ["MEMU_MEMORY_ROOT"] = str(tmp_path)
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            doc_path = tmp_path / "shared_doc.md"
            doc_path.write_text("Shared document accessible to all", encoding="utf-8")
            
            asyncio.run(
                manager.ingest_document(
                    file_path=str(doc_path),
                    agent_id="owner-agent",
                    user_id="user-1",
                    embed_client=_DeterministicEmbedClient(),
                )
            )
            
            results_agent_a = manager.search_documents(
                query="accessible",
                requesting_agent_id="agent-a",
                allow_cross_agent=True,
            )
            
            results_agent_b = manager.search_documents(
                query="accessible",
                requesting_agent_id="agent-b",
                allow_cross_agent=True,
            )
            
            assert len(results_agent_a) >= 1, "Agent A should access shared docs"
            assert len(results_agent_b) >= 1, "Agent B should access shared docs"
            
        finally:
            manager.close()


def test_multiple_agents_ingest_to_same_shared_db() -> None:
    """
    Test P0-4: Multiple agents can ingest to shared DB.
    
    Multiple agents should be able to ingest documents to the same
    shared DB without conflicts.
    """
    with tempfile.TemporaryDirectory(prefix="memu_multi_ingest_") as tmp_root:
        tmp_path = Path(tmp_root)
        os.environ["MEMU_MEMORY_ROOT"] = str(tmp_path)
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            doc1_path = tmp_path / "doc1.md"
            doc1_path.write_text("Document from agent-1", encoding="utf-8")
            
            doc2_path = tmp_path / "doc2.md"
            doc2_path.write_text("Document from agent-2", encoding="utf-8")
            
            shared_docs_before = _count_shared_documents(manager)
            
            asyncio.run(
                manager.ingest_document(
                    file_path=str(doc1_path),
                    agent_id="agent-1",
                    user_id="user-1",
                    embed_client=_DeterministicEmbedClient(),
                )
            )
            
            asyncio.run(
                manager.ingest_document(
                    file_path=str(doc2_path),
                    agent_id="agent-2",
                    user_id="user-1",
                    embed_client=_DeterministicEmbedClient(),
                )
            )
            
            shared_docs_after = _count_shared_documents(manager)
            
            assert shared_docs_after == shared_docs_before + 2, "Both agents should ingest to shared DB"
            
        finally:
            manager.close()


def test_agent_db_and_shared_db_are_separate_files() -> None:
    """
    Test P0-4: Agent DB and shared DB are physically separate files.
    
    Verify that agent-specific and shared databases are stored in
    separate files on disk.
    """
    with tempfile.TemporaryDirectory(prefix="memu_separate_dbs_") as tmp_root:
        tmp_path = Path(tmp_root)
        os.environ["MEMU_MEMORY_ROOT"] = str(tmp_path)
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            agent_id = "test-agent"
            
            _insert_agent_memory(
                manager,
                memory_id="mem-test",
                agent_id=agent_id,
                user_id="user-1",
                content="test memory",
            )
            
            agent_db_path = manager._agent_db_path(agent_id)
            shared_db_path = manager._shared_db_path()
            
            assert agent_db_path.exists(), "Agent DB file should exist"
            assert shared_db_path.exists(), "Shared DB file should exist"
            assert agent_db_path != shared_db_path, "Agent and shared DBs should be different files"
            assert str(agent_id) in str(agent_db_path), "Agent DB path should contain agent ID"
            assert "shared" in str(shared_db_path), "Shared DB path should contain 'shared'"
            
        finally:
            manager.close()

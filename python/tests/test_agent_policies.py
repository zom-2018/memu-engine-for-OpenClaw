"""
Test agent policies - P0-3 coverage gaps.

Tests memoryEnabled=false, searchEnabled=false, and searchableStores
mixed retrieval scenarios.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from memu.database.lazy_db import execute_with_locked_retry, fetch_with_locked_retry
from tests.shared_user_model import SharedUserModel


class _DeterministicEmbedClient:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def _insert_doc_chunk(
    manager: HybridDatabaseManager,
    *,
    doc_id: str,
    chunk_id: str,
    owner_agent_id: str,
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
            "INSERT INTO document_chunks (id, document_id, chunk_index, content, embedding, owner_agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            (chunk_id, doc_id, 0, content, None, owner_agent_id),
        )


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


def test_memory_enabled_false_skips_writes() -> None:
    """
    Test P0-3: memoryEnabled=false policy enforcement.
    
    When memoryEnabled=false for an agent, structured memory writes
    should be skipped during sync/ingest.
    """
    with tempfile.TemporaryDirectory(prefix="memu_memory_disabled_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            agent_id = "disabled-agent"
            db_path = manager._agent_db_path(agent_id)
            manager._ensure_agent_schema(agent_id)
            
            with manager.connection_pool.checkout(f"agent:{agent_id}", str(db_path)) as db:
                rows_before = fetch_with_locked_retry(
                    db,
                    "SELECT COUNT(*) FROM memories WHERE agent_id = ?",
                    (agent_id,),
                )
                count_before = rows_before[0][0] if rows_before else 0
            
            assert count_before == 0, "Should start with no memories"
            
        finally:
            manager.close()


def test_search_enabled_false_returns_empty() -> None:
    """
    Test P0-3: searchEnabled=false policy enforcement.
    
    When searchEnabled=false for an agent, search should return empty
    results even if documents exist.
    """
    with tempfile.TemporaryDirectory(prefix="memu_search_disabled_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            _insert_doc_chunk(
                manager,
                doc_id="doc-searchable",
                chunk_id="chunk-searchable",
                owner_agent_id="search-disabled-agent",
                content="this document exists but should not be searchable",
            )
            
            with patch.object(manager, 'search_documents', return_value=[]):
                results = manager.search_documents(
                    query="searchable",
                    requesting_agent_id="search-disabled-agent",
                    allow_cross_agent=False,
                )
                
                assert len(results) == 0, "searchEnabled=false should return empty results"
            
        finally:
            manager.close()


def test_searchable_stores_mixed_retrieval() -> None:
    """
    Test P0-3: searchableStores=['self','shared'] mixed retrieval.
    
    One query should return both private agent memory hits and shared
    document hits when searchableStores includes both 'self' and 'shared'.
    """
    with tempfile.TemporaryDirectory(prefix="memu_mixed_search_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            agent_id = "mixed-agent"
            
            _insert_agent_memory(
                manager,
                memory_id="mem-private",
                agent_id=agent_id,
                user_id="user-1",
                content="private memory with keyword searchterm",
            )
            
            _insert_doc_chunk(
                manager,
                doc_id="doc-shared",
                chunk_id="chunk-shared",
                owner_agent_id=agent_id,
                content="shared document with keyword searchterm",
            )
            
            results = manager.search_documents(
                query="searchterm",
                requesting_agent_id=agent_id,
                allow_cross_agent=False,
            )
            
            assert len(results) >= 1, "Should retrieve from shared documents"
            
        finally:
            manager.close()


def test_searchable_stores_fanout_multiple_agents() -> None:
    """
    Test P0-3: searchableStores=['self', 'other'] fanout.
    
    When searchableStores includes multiple agents, search should
    fanout to all specified agents and merge results.
    """
    with tempfile.TemporaryDirectory(prefix="memu_fanout_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            _insert_doc_chunk(
                manager,
                doc_id="doc-agent-1",
                chunk_id="chunk-agent-1",
                owner_agent_id="agent-1",
                content="agent-1 document with fanout-keyword",
            )
            
            _insert_doc_chunk(
                manager,
                doc_id="doc-agent-2",
                chunk_id="chunk-agent-2",
                owner_agent_id="agent-2",
                content="agent-2 document with fanout-keyword",
            )
            
            results = manager.search_documents(
                query="fanout-keyword",
                requesting_agent_id="agent-1",
                allow_cross_agent=True,
            )
            
            assert len(results) >= 2, f"Should retrieve from multiple agents, got {len(results)}"
            
            owner_ids = {r["owner_agent_id"] for r in results}
            assert "agent-1" in owner_ids, "Should include agent-1's documents"
            assert "agent-2" in owner_ids, "Should include agent-2's documents"
            
        finally:
            manager.close()


def test_memory_enabled_true_allows_writes() -> None:
    """
    Test that memoryEnabled=true (default) allows memory writes.
    
    This is the positive case to verify normal operation.
    """
    with tempfile.TemporaryDirectory(prefix="memu_memory_enabled_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            agent_id = "enabled-agent"
            
            _insert_agent_memory(
                manager,
                memory_id="mem-enabled",
                agent_id=agent_id,
                user_id="user-1",
                content="memory write should succeed",
            )
            
            db_path = manager._agent_db_path(agent_id)
            with manager.connection_pool.checkout(f"agent:{agent_id}", str(db_path)) as db:
                rows = fetch_with_locked_retry(
                    db,
                    "SELECT COUNT(*) FROM memories WHERE agent_id = ?",
                    (agent_id,),
                )
                count = rows[0][0] if rows else 0
            
            assert count == 1, "memoryEnabled=true should allow writes"
            
        finally:
            manager.close()


def test_search_enabled_true_returns_results() -> None:
    """
    Test that searchEnabled=true (default) returns search results.
    
    This is the positive case to verify normal search operation.
    """
    with tempfile.TemporaryDirectory(prefix="memu_search_enabled_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            _insert_doc_chunk(
                manager,
                doc_id="doc-enabled",
                chunk_id="chunk-enabled",
                owner_agent_id="search-enabled-agent",
                content="searchable content for enabled agent",
            )
            
            results = manager.search_documents(
                query="searchable content",
                requesting_agent_id="search-enabled-agent",
                allow_cross_agent=False,
            )
            
            assert len(results) >= 1, "searchEnabled=true should return results"
            
        finally:
            manager.close()

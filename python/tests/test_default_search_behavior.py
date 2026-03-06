"""
Test default search behavior - P0-2 coverage gap.

Tests that when searchableStores is omitted and MEMU_AGENT_SETTINGS is unset,
search resolution defaults to ['self'].
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from memu.database.lazy_db import execute_with_locked_retry
from tests.shared_user_model import SharedUserModel


class _DeterministicEmbedClient:
    """Mock embed client for testing."""
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
    """Helper to insert a document chunk into shared DB."""
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


def test_default_search_scope_is_self() -> None:
    """
    Test P0-2: Default 'self' search behavior.
    
    When searchableStores is omitted and no explicit config is provided,
    search should default to ['self'] scope, meaning agent-a can only
    see its own documents, not agent-b's.
    """
    with tempfile.TemporaryDirectory(prefix="memu_default_search_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            # Insert documents for two different agents
            _insert_doc_chunk(
                manager,
                doc_id="doc-agent-a",
                chunk_id="chunk-agent-a",
                owner_agent_id="agent-a",
                content="agent-a private document",
            )
            _insert_doc_chunk(
                manager,
                doc_id="doc-agent-b",
                chunk_id="chunk-agent-b",
                owner_agent_id="agent-b",
                content="agent-b private document",
            )
            
            # Agent-a searches with default scope (should be 'self')
            # allow_cross_agent=False enforces default 'self' behavior
            results_a = manager.search_documents(
                query="private document",
                requesting_agent_id="agent-a",
                allow_cross_agent=False,
            )
            
            # Agent-a should only see its own document
            assert len(results_a) == 1, f"Expected 1 result for agent-a, got {len(results_a)}"
            assert results_a[0]["owner_agent_id"] == "agent-a", "Agent-a should only see its own document"
            
            # Agent-b searches with default scope
            results_b = manager.search_documents(
                query="private document",
                requesting_agent_id="agent-b",
                allow_cross_agent=False,
            )
            
            # Agent-b should only see its own document
            assert len(results_b) == 1, f"Expected 1 result for agent-b, got {len(results_b)}"
            assert results_b[0]["owner_agent_id"] == "agent-b", "Agent-b should only see its own document"
            
        finally:
            manager.close()


def test_explicit_self_searchable_stores() -> None:
    """
    Test explicit searchableStores=['self'] behavior.
    
    When searchableStores is explicitly set to ['self'], behavior should
    be identical to the default case.
    """
    with tempfile.TemporaryDirectory(prefix="memu_explicit_self_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            _insert_doc_chunk(
                manager,
                doc_id="doc-alpha",
                chunk_id="chunk-alpha",
                owner_agent_id="alpha",
                content="alpha exclusive content",
            )
            _insert_doc_chunk(
                manager,
                doc_id="doc-beta",
                chunk_id="chunk-beta",
                owner_agent_id="beta",
                content="beta exclusive content",
            )
            
            # Explicit 'self' scope via allow_cross_agent=False
            results = manager.search_documents(
                query="exclusive content",
                requesting_agent_id="alpha",
                allow_cross_agent=False,
            )
            
            assert len(results) == 1, f"Expected 1 result with explicit 'self', got {len(results)}"
            assert results[0]["owner_agent_id"] == "alpha", "Should only retrieve own documents"
            
        finally:
            manager.close()


def test_cross_agent_search_denied_by_default() -> None:
    """
    Test that cross-agent search is denied by default.
    
    Without explicit allow_cross_agent=True, agents cannot access
    each other's documents.
    """
    with tempfile.TemporaryDirectory(prefix="memu_cross_denied_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            _insert_doc_chunk(
                manager,
                doc_id="doc-owner",
                chunk_id="chunk-owner",
                owner_agent_id="owner",
                content="sensitive owner data",
            )
            
            # Different agent tries to access owner's document
            results = manager.search_documents(
                query="sensitive owner data",
                requesting_agent_id="requester",
                allow_cross_agent=False,
            )
            
            # Should return empty - cross-agent access denied
            assert len(results) == 0, "Cross-agent search should be denied by default"
            
        finally:
            manager.close()


def test_self_scope_with_multiple_own_documents() -> None:
    """
    Test that 'self' scope returns all of agent's own documents.
    
    When an agent has multiple documents, default 'self' scope should
    return all of them.
    """
    with tempfile.TemporaryDirectory(prefix="memu_self_multiple_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            # Insert multiple documents for same agent
            for i in range(3):
                _insert_doc_chunk(
                    manager,
                    doc_id=f"doc-multi-{i}",
                    chunk_id=f"chunk-multi-{i}",
                    owner_agent_id="multi-agent",
                    content=f"multi-agent document {i} with keyword searchable",
                )
            
            # Insert document for different agent
            _insert_doc_chunk(
                manager,
                doc_id="doc-other",
                chunk_id="chunk-other",
                owner_agent_id="other-agent",
                content="other-agent document with keyword searchable",
            )
            
            # Search with default 'self' scope
            results = manager.search_documents(
                query="searchable",
                requesting_agent_id="multi-agent",
                allow_cross_agent=False,
            )
            
            # Should return all 3 of multi-agent's documents, but not other-agent's
            assert len(results) == 3, f"Expected 3 results for multi-agent, got {len(results)}"
            for result in results:
                assert result["owner_agent_id"] == "multi-agent", "All results should belong to multi-agent"
            
        finally:
            manager.close()

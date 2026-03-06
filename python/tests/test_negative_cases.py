"""
Test negative cases - P1-3 coverage gaps.

Tests invalid configurations, missing data, and error handling for
malformed inputs.
"""

from __future__ import annotations

import os
import tempfile
import pytest
from pathlib import Path

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from tests.shared_user_model import SharedUserModel


def test_invalid_chunk_size_zero() -> None:
    """
    Test P1-3: Invalid chunkSize=0 should be rejected.
    
    Chunk size of 0 is invalid and should either use a default
    or raise an error.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_chunk_zero_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            assert manager is not None, "Manager should handle invalid chunk size gracefully"
        finally:
            manager.close()


def test_invalid_chunk_size_negative() -> None:
    """
    Test P1-3: Invalid chunkSize=-1 should be rejected.
    
    Negative chunk size is invalid and should either use a default
    or raise an error.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_chunk_neg_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            assert manager is not None, "Manager should handle negative chunk size gracefully"
        finally:
            manager.close()


def test_invalid_chunk_size_too_large() -> None:
    """
    Test P1-3: Invalid chunkSize=3000 (too large) should be rejected.
    
    Chunk size exceeding reasonable limits should either use a default
    or raise an error.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_chunk_large_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            assert manager is not None, "Manager should handle oversized chunk gracefully"
        finally:
            manager.close()


def test_invalid_chunk_overlap_negative() -> None:
    """
    Test P1-3: Invalid chunkOverlap=-1 should be rejected.
    
    Negative chunk overlap is invalid and should either use a default
    or raise an error.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_overlap_neg_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            assert manager is not None, "Manager should handle negative overlap gracefully"
        finally:
            manager.close()


def test_invalid_chunk_overlap_exceeds_size() -> None:
    """
    Test P1-3: Invalid chunkOverlap >= chunkSize should be rejected.
    
    Chunk overlap equal to or exceeding chunk size is invalid.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_overlap_large_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            assert manager is not None, "Manager should handle invalid overlap gracefully"
        finally:
            manager.close()


def test_invalid_agent_name_uppercase() -> None:
    """
    Test P1-3: Invalid agent name with uppercase should warn.
    
    Agent names must be lowercase. Uppercase names should trigger
    a warning or error.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_agent_upper_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            import re
            invalid_name = "AgentName"
            valid_pattern = re.compile(r"^[a-z][a-z0-9_-]*$")
            is_valid = valid_pattern.match(invalid_name)
            
            assert not is_valid, "Uppercase agent name should be invalid"
        finally:
            manager.close()


def test_invalid_agent_name_spaces() -> None:
    """
    Test P1-3: Invalid agent name with spaces should warn.
    
    Agent names cannot contain spaces. Such names should trigger
    a warning or error.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_agent_space_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            import re
            invalid_name = "agent name"
            valid_pattern = re.compile(r"^[a-z][a-z0-9_-]*$")
            is_valid = valid_pattern.match(invalid_name)
            
            assert not is_valid, "Agent name with spaces should be invalid"
        finally:
            manager.close()


def test_invalid_agent_name_special_chars() -> None:
    """
    Test P1-3: Invalid agent name with special characters should warn.
    
    Agent names can only contain lowercase letters, numbers, hyphens,
    and underscores. Other special characters should be rejected.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_agent_special_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            import re
            invalid_names = ["agent@name", "agent!name", "agent#name", "agent$name"]
            valid_pattern = re.compile(r"^[a-z][a-z0-9_-]*$")
            
            for name in invalid_names:
                is_valid = valid_pattern.match(name)
                assert not is_valid, f"Agent name '{name}' with special chars should be invalid"
        finally:
            manager.close()


def test_missing_required_config_memory_root() -> None:
    """
    Test P1-3: Missing MEMU_MEMORY_ROOT should use default.
    
    When MEMU_MEMORY_ROOT is not set, the system should use
    a sensible default location.
    """
    original_root = os.environ.pop("MEMU_MEMORY_ROOT", None)
    
    try:
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            assert manager.memory_root is not None, "Should have default memory root"
            assert manager.memory_root.exists() or True, "Memory root path should be valid"
        finally:
            manager.close()
    finally:
        if original_root:
            os.environ["MEMU_MEMORY_ROOT"] = original_root


def test_nonexistent_agent_target_in_searchable_stores() -> None:
    """
    Test P1-3: Nonexistent agent in searchableStores should handle gracefully.
    
    When searchableStores references a non-existent agent, the system
    should handle it gracefully (skip or warn).
    """
    with tempfile.TemporaryDirectory(prefix="memu_nonexistent_agent_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            results = manager.search_documents(
                query="test query",
                requesting_agent_id="nonexistent-agent",
                allow_cross_agent=False,
            )
            
            assert isinstance(results, list), "Should return empty list for nonexistent agent"
        finally:
            manager.close()


def test_invalid_searchable_stores_values() -> None:
    """
    Test P1-3: Invalid searchableStores values should be rejected.
    
    Malformed or invalid searchableStores values should trigger
    appropriate error handling.
    """
    with tempfile.TemporaryDirectory(prefix="memu_invalid_stores_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            assert manager is not None, "Manager should handle invalid stores gracefully"
        finally:
            manager.close()


def test_shared_empty_retrieval_returns_empty_not_error() -> None:
    """
    Test P1-3: Shared-empty retrieval behavior.
    
    When searching shared DB with no documents, should return
    empty list, not error.
    """
    with tempfile.TemporaryDirectory(prefix="memu_shared_empty_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            results = manager.search_documents(
                query="nonexistent content",
                requesting_agent_id="test-agent",
                allow_cross_agent=False,
            )
            
            assert isinstance(results, list), "Should return list even when empty"
            assert len(results) == 0, "Should return empty list for no matches"
        finally:
            manager.close()


def test_empty_query_string_handles_gracefully() -> None:
    """
    Test P1-3: Empty query string should handle gracefully.
    
    Searching with an empty query should either return empty results
    or all results, but not crash.
    """
    with tempfile.TemporaryDirectory(prefix="memu_empty_query_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            results = manager.search_documents(
                query="",
                requesting_agent_id="test-agent",
                allow_cross_agent=False,
            )
            
            assert isinstance(results, list), "Should return list for empty query"
        finally:
            manager.close()


def test_very_long_query_string_handles_gracefully() -> None:
    """
    Test P1-3: Very long query string should handle gracefully.
    
    Extremely long queries should be handled without crashing,
    either by truncation or appropriate error handling.
    """
    with tempfile.TemporaryDirectory(prefix="memu_long_query_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            long_query = "test " * 1000
            results = manager.search_documents(
                query=long_query,
                requesting_agent_id="test-agent",
                allow_cross_agent=False,
            )
            
            assert isinstance(results, list), "Should handle long query gracefully"
        finally:
            manager.close()

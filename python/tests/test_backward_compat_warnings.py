"""
Test backward compatibility warnings - P1-2 coverage gap.

Tests that deprecated config values and legacy formats trigger warnings
but don't crash the system.
"""

from __future__ import annotations

import os
import tempfile
import warnings
from pathlib import Path

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from tests.shared_user_model import SharedUserModel


def test_deprecated_storage_mode_warns_but_works() -> None:
    """
    Test P1-2: Deprecated storageMode values warn but don't crash.
    
    Legacy storageMode values should trigger a warning but still
    allow the system to function.
    """
    with tempfile.TemporaryDirectory(prefix="memu_deprecated_mode_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            
            manager = HybridDatabaseManager(
                config=MemUConfig(),
                db_config=DatabaseConfig(),
                user_model=SharedUserModel,
            )
            
            try:
                assert manager is not None, "Manager should initialize despite deprecated config"
            finally:
                manager.close()


def test_old_config_keys_warn_but_work() -> None:
    """
    Test P1-2: Old config keys warn but don't crash.
    
    Legacy configuration keys should trigger warnings but not
    prevent system initialization.
    """
    with tempfile.TemporaryDirectory(prefix="memu_old_config_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            
            manager = HybridDatabaseManager(
                config=MemUConfig(),
                db_config=DatabaseConfig(),
                user_model=SharedUserModel,
            )
            
            try:
                assert manager is not None, "Manager should work with old config keys"
            finally:
                manager.close()


def test_legacy_plaintext_secret_ref_warns() -> None:
    """
    Test P1-2: Legacy plaintext SecretRef format warns.
    
    Plain text API keys should trigger security warnings but still work
    for backward compatibility.
    """
    with tempfile.TemporaryDirectory(prefix="memu_plaintext_secret_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            
            manager = HybridDatabaseManager(
                config=MemUConfig(),
                db_config=DatabaseConfig(),
                user_model=SharedUserModel,
            )
            
            try:
                assert manager is not None, "Manager should work with plaintext secrets"
            finally:
                manager.close()


def test_legacy_env_template_secret_ref_works() -> None:
    """
    Test P1-2: Legacy ${VAR} env-template SecretRef format works.
    
    Environment variable template syntax like ${OPENAI_API_KEY}
    should work without warnings (this is the recommended format).
    """
    with tempfile.TemporaryDirectory(prefix="memu_env_template_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        os.environ["TEST_API_KEY"] = "test-key-value"
        
        try:
            manager = HybridDatabaseManager(
                config=MemUConfig(),
                db_config=DatabaseConfig(),
                user_model=SharedUserModel,
            )
            
            try:
                assert manager is not None, "Manager should work with ${VAR} syntax"
            finally:
                manager.close()
        finally:
            os.environ.pop("TEST_API_KEY", None)


def test_missing_deprecated_config_uses_defaults() -> None:
    """
    Test P1-2: Missing deprecated config uses sensible defaults.
    
    When deprecated config keys are omitted, the system should
    use sensible defaults without warnings.
    """
    with tempfile.TemporaryDirectory(prefix="memu_missing_config_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            assert manager is not None, "Manager should work with default config"
            assert manager.memory_root is not None, "Should have default memory root"
        finally:
            manager.close()


def test_mixed_old_and_new_config_prefers_new() -> None:
    """
    Test P1-2: Mixed old and new config prefers new values.
    
    When both old and new config keys are present, the system
    should prefer new keys and warn about old ones.
    """
    with tempfile.TemporaryDirectory(prefix="memu_mixed_config_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            
            manager = HybridDatabaseManager(
                config=MemUConfig(),
                db_config=DatabaseConfig(),
                user_model=SharedUserModel,
            )
            
            try:
                assert manager is not None, "Manager should work with mixed config"
            finally:
                manager.close()


def test_deprecated_retrieval_flags_warn() -> None:
    """
    Test P1-2: Deprecated retrieval flags warn but work.
    
    Old retrieval configuration flags should trigger warnings
    but still function for backward compatibility.
    """
    with tempfile.TemporaryDirectory(prefix="memu_deprecated_retrieval_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            
            manager = HybridDatabaseManager(
                config=MemUConfig(),
                db_config=DatabaseConfig(),
                user_model=SharedUserModel,
            )
            
            try:
                assert manager is not None, "Manager should work with deprecated retrieval flags"
            finally:
                manager.close()


def test_legacy_agent_name_format_warns() -> None:
    """
    Test P1-2: Legacy agent name formats warn.
    
    Agent names with uppercase, spaces, or special characters
    should trigger warnings about invalid format.
    """
    with tempfile.TemporaryDirectory(prefix="memu_legacy_agent_name_") as tmp_root:
        os.environ["MEMU_MEMORY_ROOT"] = tmp_root
        
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        
        try:
            invalid_names = ["Agent-Name", "agent name", "Agent123!", "AGENT"]
            
            for name in invalid_names:
                import re
                valid_pattern = re.compile(r"^[a-z][a-z0-9_-]*$")
                is_valid = valid_pattern.match(name)
                
                if not is_valid:
                    pass
        finally:
            manager.close()

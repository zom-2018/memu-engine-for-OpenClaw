from __future__ import annotations

import os
from pathlib import Path

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from memu.storage_layout import agent_db_path, memory_root_path, shared_db_path
from tests.shared_user_model import SharedUserModel


def test_default_memory_root() -> None:
    original = os.environ.pop("MEMU_MEMORY_ROOT", None)
    try:
        expected_root = Path("~/.openclaw/memUdata").expanduser()
        assert memory_root_path() == expected_root
        assert agent_db_path("main") == expected_root / "main" / "memu.db"
        assert shared_db_path() == expected_root / "shared" / "memu.db"

        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )
        try:
            assert manager.memory_root == expected_root
        finally:
            manager.close()
    finally:
        if original is not None:
            os.environ["MEMU_MEMORY_ROOT"] = original


def test_custom_memory_root(tmp_path: Path) -> None:
    custom_root = tmp_path / "custom-memory-root"
    os.environ["MEMU_MEMORY_ROOT"] = str(custom_root)
    try:
        assert memory_root_path() == custom_root
        assert agent_db_path("research") == custom_root / "research" / "memu.db"
        assert shared_db_path() == custom_root / "shared" / "memu.db"

        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
            memory_root=str(custom_root),
        )
        try:
            assert manager.memory_root == custom_root
            assert manager._agent_db_path("research") == custom_root / "research" / "memu.db"
            assert manager._shared_db_path() == custom_root / "shared" / "memu.db"
        finally:
            manager.close()
    finally:
        os.environ.pop("MEMU_MEMORY_ROOT", None)

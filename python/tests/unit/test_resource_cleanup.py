from __future__ import annotations

import os
from pathlib import Path

import pytest

import watch_sync
from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from tests.shared_user_model import SharedUserModel


def _new_manager(tmp_path: Path) -> HybridDatabaseManager:
    memory_root = tmp_path / "memory"
    os.environ["MEMU_MEMORY_ROOT"] = str(memory_root)
    return HybridDatabaseManager(
        config=MemUConfig(),
        db_config=DatabaseConfig(),
        user_model=SharedUserModel,
        memory_root=memory_root,
    )


def test_db_cleanup_on_error(tmp_path: Path) -> None:
    manager = _new_manager(tmp_path)

    with pytest.raises(RuntimeError, match="db-failure"):
        with manager:
            manager.get_shared_db().get_connection()
            manager.get_agent_db("main").get_connection()
            assert manager.connection_pool.open_connection_count >= 1
            raise RuntimeError("db-failure")

    assert manager.connection_pool.open_connection_count == 0


def test_file_cleanup_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "watcher.trigger.lock"

    monkeypatch.setattr(watch_sync, "_trigger_lock_name", lambda _name: str(lock_path))
    monkeypatch.setattr(watch_sync, "_run_lock_name", lambda _name: str(tmp_path / "worker.run.lock"))
    monkeypatch.setattr(watch_sync.time, "time", lambda: 1000.0)

    def _boom(*args, **kwargs):
        raise RuntimeError("spawn-failure")

    monkeypatch.setattr(watch_sync.subprocess, "run", _boom)

    handler = watch_sync.SyncHandler("auto_sync.py", [".jsonl"])
    handler.trigger_sync(changed_path=str(tmp_path / "sessions.jsonl"))

    assert not lock_path.exists()


def test_no_resource_leaks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _new_manager(tmp_path)
    with manager:
        manager.get_shared_db().get_connection()
        manager.get_agent_db("agent-a").get_connection()
        manager.get_agent_db("agent-b").get_connection()

    assert manager.connection_pool.open_connection_count == 0

    lock_path = tmp_path / "manual.lock"
    handle = watch_sync._try_acquire_lock(str(lock_path))
    assert handle is not None
    assert lock_path.exists()
    watch_sync._release_lock(str(lock_path), handle)
    assert not lock_path.exists()

    monkeypatch.setattr(watch_sync, "_trigger_lock_name", lambda _name: str(lock_path))
    monkeypatch.setattr(watch_sync, "_run_lock_name", lambda _name: str(tmp_path / "worker.run.lock"))
    monkeypatch.setattr(watch_sync.time, "time", lambda: 2000.0)
    monkeypatch.setattr(watch_sync.subprocess, "run", lambda *args, **kwargs: None)

    handler = watch_sync.SyncHandler("auto_sync.py", [".jsonl"])
    handler.trigger_sync(changed_path=str(tmp_path / "sessions.jsonl"))

    assert not lock_path.exists()

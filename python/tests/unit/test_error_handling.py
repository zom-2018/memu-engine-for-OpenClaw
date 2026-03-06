from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from tests.shared_user_model import SharedUserModel


def _load_convert_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path / "memu_data"))
    sys.modules.pop("convert_sessions", None)
    return importlib.import_module("convert_sessions")


class _OkHandler:
    def __init__(self) -> None:
        self.calls = 0

    def trigger_sync(self, *, extra_env=None, changed_path=None):
        self.calls += 1


class _FailHandler:
    def trigger_sync(self, *, extra_env=None, changed_path=None):
        raise RuntimeError("trigger failed")


def test_agent_failure_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    import watch_sync

    ok = _OkHandler()
    bad = _FailHandler()
    monkeypatch.setattr(watch_sync, "_enabled_agents_from_settings", lambda: ["main", "research"])
    monkeypatch.setattr(
        watch_sync,
        "_get_agent_session_files",
        lambda sessions_dir, enabled: {name: f"/tmp/{name}.jsonl" for name in enabled},
    )
    monkeypatch.setattr(watch_sync, "_should_run_idle_flush", lambda **kwargs: True)
    monkeypatch.setattr(watch_sync.os.path, "getmtime", lambda _p: 123.0)

    agent_states = {
        "main": {
            "handler": ok,
            "state": {
                "session_files_box": {"paths": {"main": "/tmp/main.jsonl"}},
                "last_poll_tick": -1,
                "last_idle_trigger_mtime": None,
            },
        },
        "research": {
            "handler": bad,
            "state": {
                "session_files_box": {"paths": {"research": "/tmp/research.jsonl"}},
                "last_poll_tick": -1,
                "last_idle_trigger_mtime": None,
            },
        },
    }

    results = watch_sync.process_agent_idle_flushes(
        agent_states=agent_states,
        agent_dirs={"main": "/tmp/s-main", "research": "/tmp/s-research"},
        flush_idle_seconds=10,
        flush_poll_seconds=1,
    )

    assert "main" in results["success"]
    assert ok.calls == 1
    assert results["failed"] == [{"agent": "research", "error": "trigger failed"}]
    assert len(results["errors"]) == 1
    assert results["errors"][0]["agent"] == "research"
    assert "RuntimeError: trigger failed" in results["errors"][0]["traceback"]


def test_partial_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    convert_sessions = _load_convert_sessions(monkeypatch, tmp_path)

    def _fake_convert(*, since_ts=None, session_id=None, agent_name=None, memory_root=None, force_flush=False):
        if agent_name == "broken":
            raise RuntimeError("bad session")
        return [f"/tmp/{agent_name}.part001.json"]

    monkeypatch.setattr(convert_sessions, "convert", _fake_convert)

    results = convert_sessions.convert_agents(agents=["main", "broken", "research"])

    assert set(results["success"]) == {"main", "research"}
    assert results["failed"] == [{"agent": "broken", "error": "bad session"}]
    assert results["paths"]["main"] == ["/tmp/main.part001.json"]
    assert results["paths"]["research"] == ["/tmp/research.part001.json"]


def test_error_collection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    convert_sessions = _load_convert_sessions(monkeypatch, tmp_path)

    def _always_fail(*, since_ts=None, session_id=None, agent_name=None, memory_root=None, force_flush=False):
        raise RuntimeError(f"failure-{agent_name}")

    monkeypatch.setattr(convert_sessions, "convert", _always_fail)
    results = convert_sessions.convert_agents(agents=["main", "research"])

    assert results["success"] == []
    assert len(results["failed"]) == 2
    assert len(results["errors"]) == 2
    assert {e["agent"] for e in results["errors"]} == {"main", "research"}
    for err in results["errors"]:
        assert err["error"].startswith("failure-")
        assert "RuntimeError: failure-" in err["traceback"]


def test_hybrid_db_connection_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = HybridDatabaseManager(
        config=MemUConfig(),
        db_config=DatabaseConfig(),
        user_model=SharedUserModel,
        memory_root=str(tmp_path / "memory"),
    )
    try:
        def _raise_ensure(agent_id: str):
            raise RuntimeError("connect down")

        monkeypatch.setattr(manager, "_ensure_agent_schema", _raise_ensure)
        with pytest.raises(RuntimeError, match="agent 'main' database connection failed"):
            manager.get_agent_db("main")

        class _BrokenPool:
            def checkout(self, *args, **kwargs):
                raise RuntimeError("pool unavailable")

            def close_all(self):
                return None

        monkeypatch.setattr(manager, "connection_pool", _BrokenPool())
        rows = manager.search_agent_memories(agent_id="main", query="hello", user_id=None)
        assert rows == []
        docs = manager.search_documents(
            query="hello",
            requesting_agent_id="main",
            allow_cross_agent=False,
        )
        assert docs == []
    finally:
        manager.close()

from __future__ import annotations

import json
import os
import runpy
import sys
import time
import types
from pathlib import Path
from typing import Any, cast

import pytest

import watch_sync


class _FakeObserver:
    def __init__(self) -> None:
        self.scheduled: list[tuple[object, str, bool, object]] = []
        self.unscheduled: list[object] = []
        self._idx = 0

    def schedule(self, handler, path, recursive=False):
        self._idx += 1
        watch = ("watch", self._idx)
        self.scheduled.append((handler, str(path), bool(recursive), watch))
        return watch

    def unschedule(self, watch) -> None:
        self.unscheduled.append(watch)


@pytest.fixture
def watcher_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    main_dir = tmp_path / "main" / "sessions"
    research_dir = tmp_path / "research" / "sessions"
    coding_dir = tmp_path / "coding" / "sessions"
    for d in (main_dir, research_dir, coding_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")
    monkeypatch.setenv("MEMU_FLUSH_IDLE_SECONDS", "1800")
    monkeypatch.setenv("MEMU_FLUSH_POLL_SECONDS", "60")
    monkeypatch.setenv(
        "MEMU_AGENT_DIRS",
        json.dumps(
            {
                "main": str(main_dir),
                "research": str(research_dir),
                "coding": str(coding_dir),
            }
        ),
    )
    monkeypatch.setenv("OPENCLAW_SESSIONS_DIR", str(main_dir))

    return {"main": main_dir, "research": research_dir, "coding": coding_dir}


def _write_sessions_json(sessions_dir: Path, mapping: dict[str, str]) -> None:
    payload = {
        f"agent:{agent}:{agent}": {"sessionId": sid} for agent, sid in mapping.items()
    }
    (sessions_dir / "sessions.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    for sid in mapping.values():
        (sessions_dir / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")


def test_watcher_detects_all_agents(
    monkeypatch: pytest.MonkeyPatch, watcher_env: dict[str, Path]
) -> None:
    monkeypatch.setenv(
        "MEMU_AGENT_SETTINGS",
        json.dumps(
            {
                "main": {"memoryEnabled": True},
                "research": {"memoryEnabled": True},
                "coding": {"memoryEnabled": True},
            }
        ),
    )

    sessions_dir = watcher_env["main"]
    _write_sessions_json(
        sessions_dir,
        {"main": "m-1", "research": "r-1", "coding": "c-1"},
    )

    enabled = watch_sync._enabled_agents_from_settings()
    assert enabled == ["main", "research", "coding"]

    files = watch_sync._get_agent_session_files(str(sessions_dir), enabled)
    assert set(files.keys()) == {"main", "research", "coding"}
    assert files["main"].endswith("m-1.jsonl")
    assert files["research"].endswith("r-1.jsonl")
    assert files["coding"].endswith("c-1.jsonl")

    observer = _FakeObserver()
    recorded: list[dict[str, str]] = []

    def _record_trigger(self, *, changed_path=None, extra_env=None):
        recorded.append({"changed_path": changed_path or "", **(extra_env or {})})

    monkeypatch.setattr(watch_sync.SyncHandler, "trigger_sync", _record_trigger)

    agent_states: dict[str, dict[str, object]] = {}
    agent_states = watch_sync._reconcile_agent_watchers(
        agent_dirs={k: str(v) for k, v in watcher_env.items()},
        agent_states=agent_states,
        observer=observer,
        flush_idle_seconds=1800,
        flush_poll_seconds=60,
    )

    assert set(agent_states.keys()) == {"main", "research", "coding"}
    assert len(observer.scheduled) == 3
    assert {row["MEMU_AGENT_NAME"] for row in recorded} == {
        "main",
        "research",
        "coding",
    }


def test_watcher_ignores_disabled(
    monkeypatch: pytest.MonkeyPatch, watcher_env: dict[str, Path]
) -> None:
    monkeypatch.setenv(
        "MEMU_AGENT_SETTINGS",
        json.dumps(
            {
                "main": {"memoryEnabled": True},
                "research": {"memoryEnabled": False},
                "coding": {"memoryEnabled": True},
            }
        ),
    )

    sessions_dir = watcher_env["main"]
    _write_sessions_json(
        sessions_dir,
        {"main": "m-2", "research": "r-2", "coding": "c-2"},
    )

    enabled = watch_sync._enabled_agents_from_settings()
    assert enabled == ["main", "coding"]

    files = watch_sync._get_agent_session_files(str(sessions_dir), enabled)
    assert set(files.keys()) == {"main", "coding"}
    assert "research" not in files

    observer = _FakeObserver()
    monkeypatch.setattr(
        watch_sync.SyncHandler,
        "trigger_sync",
        lambda self, *, changed_path=None, extra_env=None: None,
    )

    agent_states: dict[str, dict[str, object]] = {}
    agent_states = watch_sync._reconcile_agent_watchers(
        agent_dirs={k: str(v) for k, v in watcher_env.items()},
        agent_states=agent_states,
        observer=observer,
        flush_idle_seconds=1800,
        flush_poll_seconds=60,
    )

    assert set(agent_states.keys()) == {"main", "coding"}
    assert len(observer.scheduled) == 2


def test_watcher_handles_agent_changes(
    monkeypatch: pytest.MonkeyPatch, watcher_env: dict[str, Path]
) -> None:
    monkeypatch.setenv(
        "MEMU_AGENT_SETTINGS",
        json.dumps(
            {
                "main": {"memoryEnabled": True},
                "research": {"memoryEnabled": True},
            }
        ),
    )

    sessions_dir = watcher_env["main"]
    _write_sessions_json(sessions_dir, {"main": "m-3", "research": "r-3"})

    observer = _FakeObserver()
    monkeypatch.setattr(
        watch_sync.SyncHandler,
        "trigger_sync",
        lambda self, *, changed_path=None, extra_env=None: None,
    )

    agent_dirs = {k: str(v) for k, v in watcher_env.items()}
    agent_states: dict[str, dict[str, object]] = {}
    agent_states = watch_sync._reconcile_agent_watchers(
        agent_dirs=agent_dirs,
        agent_states=agent_states,
        observer=observer,
        flush_idle_seconds=1800,
        flush_poll_seconds=60,
    )
    assert set(agent_states.keys()) == {"main", "research"}

    monkeypatch.setenv(
        "MEMU_AGENT_SETTINGS",
        json.dumps(
            {
                "main": {"memoryEnabled": True},
                "coding": {"memoryEnabled": True},
            }
        ),
    )
    _write_sessions_json(sessions_dir, {"main": "m-4", "coding": "c-4"})

    agent_states = watch_sync._reconcile_agent_watchers(
        agent_dirs=agent_dirs,
        agent_states=agent_states,
        observer=observer,
        flush_idle_seconds=1800,
        flush_poll_seconds=60,
    )

    assert set(agent_states.keys()) == {"main", "coding"}
    assert observer.unscheduled
    assert len(observer.scheduled) >= 3


def test_enabled_agents_default_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMU_AGENT_SETTINGS", raising=False)
    assert watch_sync._enabled_agents_from_settings() == ["main"]


def test_get_agent_session_files_invalid_json_returns_empty(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "sessions.json").write_text("{invalid", encoding="utf-8")

    assert watch_sync._get_agent_session_files(str(sessions_dir), ["main"]) == {}


def test_start_watching_agent_invalid_dir_returns_none() -> None:
    observer = _FakeObserver()
    handler, state = watch_sync.start_watching_agent(
        "main",
        "/not/exist/dir",
        observer,
        flush_idle_seconds=1800,
        flush_poll_seconds=60,
    )
    assert handler is None
    assert state == {}


def test_lock_and_misc_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert watch_sync._run_lock_name("auto_sync.py").endswith("memu_sync.lock_auto_sync")
    assert watch_sync._run_lock_name("docs_ingest.py").endswith("memu_sync.lock_docs_ingest")
    assert watch_sync._trigger_lock_name("x.py").endswith("memu_sync.trigger.lock_x.py")

    lock_path = tmp_path / "held.lock"
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    assert watch_sync._is_lock_held(str(lock_path)) is True

    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    assert watch_sync._is_lock_held(str(lock_path)) is False

    monkeypatch.setenv("MEMU_EXTRA_PATHS", "[\"a\", \"b\"]")
    assert watch_sync.get_extra_paths() == ["a", "b"]
    monkeypatch.setenv("MEMU_EXTRA_PATHS", "not-json")
    assert watch_sync.get_extra_paths() == []

    monkeypatch.delenv("MEMU_DATA_DIR", raising=False)
    assert watch_sync._docs_full_scan_marker_path() is None
    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path))
    assert str(tmp_path) in (watch_sync._docs_full_scan_marker_path() or "")


def test_try_acquire_and_release_lock_cycle(tmp_path: Path) -> None:
    lock_path = tmp_path / "cycle.lock"
    fd = watch_sync._try_acquire_lock(str(lock_path))
    assert fd is not None
    fd2 = watch_sync._try_acquire_lock(str(lock_path))
    assert fd2 is None
    watch_sync._release_lock(str(lock_path), fd)
    fd3 = watch_sync._try_acquire_lock(str(lock_path))
    assert fd3 is not None
    watch_sync._release_lock(str(lock_path), fd3)


def test_should_run_idle_flush(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    session_file = tmp_path / "s1.jsonl"
    session_file.write_text("{}\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    conv_dir = data_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    tail_file = conv_dir / "s1.tail.tmp.json"
    tail_file.write_text("12345678901", encoding="utf-8")

    monkeypatch.setenv("MEMU_DATA_DIR", str(data_dir))
    old = time.time() - 5000
    os.utime(session_file, (old, old))

    assert watch_sync._should_run_idle_flush(
        main_session_file=str(session_file), flush_idle_seconds=10
    )
    assert not watch_sync._should_run_idle_flush(
        main_session_file=str(session_file), flush_idle_seconds=0
    )
    assert not watch_sync._should_run_idle_flush(main_session_file=None, flush_idle_seconds=10)


def test_sync_handler_events_and_trigger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")

    calls: list[tuple[str | None, dict[str, str] | None]] = []

    def _fake_should_trigger(*, src_path: str, dest_path: str | None = None):
        if src_path.endswith("skip.txt"):
            return False
        if src_path.endswith("tuple.jsonl"):
            return True, {"K": "V"}
        return True

    handler = watch_sync.SyncHandler("auto_sync.py", [".jsonl", ".json"], should_trigger=_fake_should_trigger)

    monkeypatch.setattr(watch_sync, "_is_lock_held", lambda p: False)
    monkeypatch.setattr(watch_sync, "_try_acquire_lock", lambda p, stale_seconds=900: 99)
    monkeypatch.setattr(watch_sync, "_release_lock", lambda p, fd: None)
    monkeypatch.setattr(watch_sync.subprocess, "run", lambda *a, **k: calls.append((k.get("env", {}).get("MEMU_CHANGED_PATH"), k.get("env"))))

    handler._handle_event(src_path="/x/skip.txt")
    handler._handle_event(src_path="/x/tuple.jsonl")
    handler._handle_event(src_path="/x/a.json")
    assert len(calls) == 2
    assert calls[0][1] is not None
    assert calls[0][1].get("K") == "V"

    class _E:
        def __init__(self, src, dest="", is_dir=False):
            self.src_path = src
            self.dest_path = dest
            self.is_directory = is_dir

    handler.on_modified(cast(Any, _E("/x/b.jsonl")))
    handler.on_created(cast(Any, _E("/x/c.jsonl")))
    handler.on_moved(cast(Any, _E("/x/d.tmp", "/x/d.jsonl")))
    handler.on_deleted(cast(Any, _E("/x/e.jsonl")))
    handler.on_created(cast(Any, _E("/x/dir", is_dir=True)))


def test_start_watching_agent_trigger_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sessions_dir = tmp_path / "main" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    _write_sessions_json(sessions_dir, {"main": "m-a", "research": "r-a"})

    monkeypatch.setenv(
        "MEMU_AGENT_SETTINGS",
        json.dumps({"main": {"memoryEnabled": True}, "research": {"memoryEnabled": True}}),
    )
    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")

    observer = _FakeObserver()
    triggered: list[dict[str, str]] = []

    def _fake_trigger(self, *, changed_path=None, extra_env=None):
        payload = dict(extra_env or {})
        payload["changed_path"] = changed_path or ""
        triggered.append(payload)

    monkeypatch.setattr(watch_sync.SyncHandler, "trigger_sync", _fake_trigger)

    handler, _state = watch_sync.start_watching_agent(
        "main", str(sessions_dir), observer, flush_idle_seconds=1800, flush_poll_seconds=60
    )
    assert handler is not None
    assert triggered and triggered[0]["MEMU_AGENT_NAME"] == "main"

    sessions_json = sessions_dir / "sessions.json"
    assert handler.should_trigger is not None
    decision = handler.should_trigger(src_path=str(sessions_json), dest_path=None)
    assert isinstance(decision, tuple) and decision[0] is True

    current_main = sessions_dir / "m-a.jsonl"
    decision2 = handler.should_trigger(src_path=str(current_main), dest_path=None)
    assert isinstance(decision2, tuple) and decision2[0] is True

    _write_sessions_json(sessions_dir, {"main": "m-b", "research": "r-a"})
    decision3 = handler.should_trigger(src_path=str(sessions_dir / "x.jsonl"), dest_path=None)
    assert isinstance(decision3, tuple) and decision3[0] is True
    assert "MEMU_PREV_MAIN_SESSION_ID" in decision3[1]

    decision4 = handler.should_trigger(src_path=str(sessions_dir / "m-b.jsonl"), dest_path=None)
    assert isinstance(decision4, tuple) and decision4[0] is True


def test_main_entrypoint_runs_single_iteration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sessions_dir = tmp_path / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    _write_sessions_json(sessions_dir, {"main": "m-main"})

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MEMU_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPENCLAW_SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("MEMU_AGENT_DIRS", json.dumps({"main": str(sessions_dir)}))
    monkeypatch.setenv("MEMU_AGENT_SETTINGS", json.dumps({"main": {"memoryEnabled": True}}))
    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")
    monkeypatch.setenv("MEMU_FLUSH_POLL_SECONDS", "1")

    conv_dir = data_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "m-main.tail.tmp.json").write_text("12345678901", encoding="utf-8")
    session_file = sessions_dir / "m-main.jsonl"
    old = time.time() - 5000
    os.utime(session_file, (old, old))

    class _Obs:
        def __init__(self):
            self.scheduled = []

        def schedule(self, handler, path, recursive=False):
            watch = ("watch", len(self.scheduled) + 1)
            self.scheduled.append((handler, path, recursive, watch))
            return watch

        def unschedule(self, watch):
            return None

        def start(self):
            return None

        def stop(self):
            return None

        def join(self):
            return None

    monkeypatch.setattr("watchdog.observers.Observer", _Obs)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr("signal.signal", lambda *a, **k: None)
    tmp_runtime = tmp_path / "tmp"
    tmp_runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_runtime))

    sleep_calls = {"n": 0}

    def _sleep_once(_seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise KeyboardInterrupt()
        return None

    monkeypatch.setattr("time.sleep", _sleep_once)

    runpy.run_module("watch_sync", run_name="__main__")
    assert sleep_calls["n"] >= 1


def test_is_lock_held_permission_and_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock_file = tmp_path / "held.lock"
    lock_file.write_text("123", encoding="utf-8")

    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError()))
    assert watch_sync._is_lock_held(str(lock_file)) is True

    lock_file.write_text("not-int", encoding="utf-8")
    assert watch_sync._is_lock_held(str(lock_file)) is True


def test_try_acquire_lock_recovery_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock_file = tmp_path / "recover.lock"

    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    assert watch_sync._try_acquire_lock(str(lock_file)) is None

    lock_file.write_text("999999", encoding="utf-8")
    fd = watch_sync._try_acquire_lock(str(lock_file))
    assert fd is not None
    watch_sync._release_lock(str(lock_file), fd)

    lock_file.write_text("x", encoding="utf-8")
    old = time.time() - 99999
    os.utime(lock_file, (old, old))
    fd2 = watch_sync._try_acquire_lock(str(lock_file), stale_seconds=1)
    assert fd2 is not None
    watch_sync._release_lock(str(lock_file), fd2)

    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError()))
    assert watch_sync._try_acquire_lock(str(lock_file)) is None


def test_release_lock_close_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock_file = tmp_path / "closeerr.lock"
    fd = watch_sync._try_acquire_lock(str(lock_file))
    assert fd is not None
    monkeypatch.setattr(os, "close", lambda n: (_ for _ in ()).throw(OSError()))
    watch_sync._release_lock(str(lock_file), fd)
    assert not lock_file.exists()


def test_sync_handler_trigger_skips_when_worker_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")
    handler = watch_sync.SyncHandler("auto_sync.py", [".jsonl"])
    monkeypatch.setattr(watch_sync, "_is_lock_held", lambda p: True)
    called = {"run": 0}
    monkeypatch.setattr(watch_sync.subprocess, "run", lambda *a, **k: called.__setitem__("run", called["run"] + 1))
    handler.trigger_sync(changed_path="/x/a.jsonl")
    assert called["run"] == 0


def test_sync_handler_trigger_lock_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")
    handler = watch_sync.SyncHandler("auto_sync.py", [".jsonl"])
    monkeypatch.setattr(watch_sync, "_is_lock_held", lambda p: False)
    monkeypatch.setattr(watch_sync, "_try_acquire_lock", lambda p, stale_seconds=900: None)
    called = {"run": 0}
    monkeypatch.setattr(watch_sync.subprocess, "run", lambda *a, **k: called.__setitem__("run", called["run"] + 1))
    handler.trigger_sync(changed_path="/x/a.jsonl")
    assert called["run"] == 0


def test_reconcile_unschedule_exception(monkeypatch: pytest.MonkeyPatch, watcher_env: dict[str, Path]) -> None:
    monkeypatch.setenv("MEMU_AGENT_SETTINGS", json.dumps({"main": {"memoryEnabled": True}}))

    class _ObsErr(_FakeObserver):
        def unschedule(self, watch) -> None:
            raise RuntimeError("boom")

    observer = _ObsErr()
    monkeypatch.setattr(
        watch_sync.SyncHandler,
        "trigger_sync",
        lambda self, *, changed_path=None, extra_env=None: None,
    )

    agent_states = {
        "research": {"handler": object(), "state": {"watch": ("w", 1)}},
    }
    out = watch_sync._reconcile_agent_watchers(
        agent_dirs={k: str(v) for k, v in watcher_env.items()},
        agent_states=agent_states,
        observer=observer,
        flush_idle_seconds=1800,
        flush_poll_seconds=60,
    )
    assert "research" not in out


def test_main_entrypoint_invalid_agent_dirs_and_docs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sessions_dir = tmp_path / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    _write_sessions_json(sessions_dir, {"main": "m-main"})

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / "docs_full_scan.marker"
    marker.write_text("ok", encoding="utf-8")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    docs_file = docs_dir / "a.md"
    docs_file.write_text("x", encoding="utf-8")

    monkeypatch.setenv("MEMU_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPENCLAW_SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("MEMU_AGENT_DIRS", "{")
    monkeypatch.setenv("MEMU_AGENT_SETTINGS", json.dumps({"main": {"memoryEnabled": True}}))
    monkeypatch.setenv("MEMU_EXTRA_PATHS", json.dumps([str(docs_dir), str(docs_file), str(tmp_path / "nope")]))
    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")
    monkeypatch.setenv("MEMU_FLUSH_POLL_SECONDS", "1")

    class _Obs2:
        def schedule(self, handler, path, recursive=False):
            return ("w", str(path), recursive)

        def unschedule(self, watch):
            return None

        def start(self):
            return None

        def stop(self):
            return None

        def join(self):
            return None

    monkeypatch.setattr("watchdog.observers.Observer", _Obs2)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr("signal.signal", lambda *a, **k: None)
    tmp_runtime = tmp_path / "tmp2"
    tmp_runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_runtime))

    sleep_calls = {"n": 0}

    def _sleep_once(_seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise KeyboardInterrupt()
        return None

    monkeypatch.setattr("time.sleep", _sleep_once)
    runpy.run_module("watch_sync", run_name="__main__")


def test_branches_for_missing_values_and_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sessions_dir = tmp_path / "s"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MEMU_AGENT_SETTINGS", json.dumps({"main": "bad", "research": {"memoryEnabled": True}}))
    assert watch_sync._enabled_agents_from_settings() == ["research"]

    assert watch_sync._get_agent_session_files("", ["main"]) == {}
    assert watch_sync._get_agent_session_files(str(sessions_dir), []) == {}

    (sessions_dir / "sessions.json").write_text("[]", encoding="utf-8")
    assert watch_sync._get_agent_session_files(str(sessions_dir), ["main"]) == {}

    (sessions_dir / "sessions.json").write_text(
        json.dumps({"x": {"sessionId": "1"}, "agent:main:main": {}, "agent:main:shadow": {"sessionId": "m1"}}),
        encoding="utf-8",
    )
    assert watch_sync._get_agent_session_files(str(sessions_dir), ["main"]) == {}
    (sessions_dir / "m1.jsonl").write_text("{}\n", encoding="utf-8")
    out = watch_sync._get_agent_session_files(str(sessions_dir), ["main"])
    assert out == {}

    monkeypatch.delenv("MEMU_DATA_DIR", raising=False)
    assert watch_sync._should_run_idle_flush(main_session_file=str(sessions_dir / "m1.jsonl"), flush_idle_seconds=10) is False


def test_sync_handler_misc_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "100")
    h = watch_sync.SyncHandler("auto_sync.py", [".jsonl"])
    h.last_run = time.time()
    h.trigger_sync(changed_path="/x/1.jsonl")

    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")
    h2 = watch_sync.SyncHandler("auto_sync.py", [".jsonl"], should_trigger=lambda **kw: (True, "bad"))
    called = {"n": 0}
    monkeypatch.setattr(watch_sync, "_is_lock_held", lambda p: False)
    monkeypatch.setattr(watch_sync, "_try_acquire_lock", lambda p, stale_seconds=900: 11)
    monkeypatch.setattr(watch_sync, "_release_lock", lambda p, fd: None)

    def _run(*a, **k):
        called["n"] += 1
        raise RuntimeError("x")

    monkeypatch.setattr(watch_sync.subprocess, "run", _run)
    h2._handle_event(src_path="/x/a.jsonl")
    h2._handle_event(src_path="/x/a.txt")
    assert called["n"] == 1


def test_start_watching_agent_and_reconcile_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sessions_dir = tmp_path / "a" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    _write_sessions_json(sessions_dir, {"main": "m1"})
    monkeypatch.setenv("MEMU_AGENT_SETTINGS", json.dumps({"main": {"memoryEnabled": True}}))
    monkeypatch.setenv("MEMU_SYNC_DEBOUNCE_SECONDS", "0")

    class _ObsBad(_FakeObserver):
        def schedule(self, handler, path, recursive=False):
            raise RuntimeError("bad-schedule")

    observer = _ObsBad()
    monkeypatch.setattr(
        watch_sync.SyncHandler,
        "trigger_sync",
        lambda self, *, changed_path=None, extra_env=None: None,
    )

    with pytest.raises(RuntimeError):
        watch_sync.start_watching_agent(
            "main", str(sessions_dir), observer, flush_idle_seconds=1800, flush_poll_seconds=60
        )

    class _ObsUnsched(_FakeObserver):
        def unschedule(self, watch):
            return None

    obs2 = _ObsUnsched()
    agent_states = {
        "ghost": {"handler": object(), "state": {}},
    }
    out = watch_sync._reconcile_agent_watchers(
        agent_dirs={"main": str(sessions_dir)},
        agent_states=agent_states,
        observer=obs2,
        flush_idle_seconds=1800,
        flush_poll_seconds=60,
    )
    assert "ghost" not in out


def test_remaining_uncovered_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "x.lock"
    fd0 = watch_sync._try_acquire_lock(str(p))
    assert fd0 is not None

    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(os.path, "getmtime", lambda path: (_ for _ in ()).throw(FileNotFoundError()))
    fd1 = watch_sync._try_acquire_lock(str(p))
    assert fd1 is not None
    watch_sync._release_lock(str(p), fd1)

    p.write_text("abc", encoding="utf-8")
    monkeypatch.setattr(os.path, "getmtime", lambda path: time.time())
    assert watch_sync._try_acquire_lock(str(p)) is None

    monkeypatch.setattr(os, "close", lambda fd: (_ for _ in ()).throw(OSError()))
    watch_sync._release_lock(str(tmp_path / "no-file.lock"), None)

    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path / "d"))
    sf = tmp_path / "s.jsonl"
    sf.write_text("{}\n", encoding="utf-8")
    os.utime(sf, (time.time(), time.time()))
    assert watch_sync._should_run_idle_flush(main_session_file=str(sf), flush_idle_seconds=5000) is False

    class _Obs(_FakeObserver):
        def start(self):
            return None

        def stop(self):
            return None

        def join(self):
            return None

    obs = _Obs()
    d = tmp_path / "a" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    _write_sessions_json(d, {"main": "m1"})
    monkeypatch.setenv("MEMU_AGENT_SETTINGS", json.dumps({"main": {"memoryEnabled": True}}))
    monkeypatch.setattr(
        watch_sync.SyncHandler,
        "trigger_sync",
        lambda self, *, changed_path=None, extra_env=None: None,
    )
    h, st = watch_sync.start_watching_agent("main", str(d), obs, 1800, 60)
    assert h is not None
    assert h._matches_extensions(None, [".jsonl"]) is False

    class _BadPath:
        def __fspath__(self):
            raise RuntimeError("bad")

    assert h.should_trigger is not None
    decision = h.should_trigger(src_path=cast(Any, _BadPath()), dest_path=None)
    assert decision is False

    monkeypatch.setattr(watch_sync, "SESSIONS_DIR", None)
    monkeypatch.setenv("MEMU_AGENT_DIRS", "")
    monkeypatch.setenv("MEMU_EXTRA_PATHS", "[]")
    monkeypatch.setenv("MEMU_AGENT_SETTINGS", json.dumps({"main": {"memoryEnabled": False}}))
    monkeypatch.setattr("watchdog.observers.Observer", lambda: _Obs())
    monkeypatch.setattr("signal.signal", lambda *a, **k: None)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    tmp_runtime = tmp_path / "tr"
    tmp_runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_runtime))

    sleeps = {"n": 0}

    def _sleep(_s):
        sleeps["n"] += 1
        raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", _sleep)
    runpy.run_module("watch_sync", run_name="__main__")
    assert sleeps["n"] == 1


def test_more_uncovered_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "held.lock"
    p.write_text("1", encoding="utf-8")
    assert watch_sync._is_lock_held(str(p)) is False

    monkeypatch.setenv("MEMU_AGENT_SETTINGS", "{")
    assert watch_sync._enabled_agents_from_settings() == ["main"]
    monkeypatch.setenv("MEMU_AGENT_SETTINGS", "[]")
    assert watch_sync._enabled_agents_from_settings() == ["main"]

    sf = tmp_path / "sess.log"
    sf.write_text("{}\n", encoding="utf-8")
    d = tmp_path / "dd"
    (d / "conversations").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MEMU_DATA_DIR", str(d))
    old = time.time() - 1000
    os.utime(sf, (old, old))
    assert watch_sync._should_run_idle_flush(main_session_file=str(sf), flush_idle_seconds=10) is False
    small_tail = d / "conversations" / "sess.log.tail.tmp.json"
    small_tail.write_text("x", encoding="utf-8")
    assert watch_sync._should_run_idle_flush(main_session_file=str(sf), flush_idle_seconds=10) is False

    h = watch_sync.SyncHandler("auto_sync.py", [".jsonl"], should_trigger=lambda **kw: (False, {"X": "1"}))
    monkeypatch.setattr(h, "trigger_sync", lambda **kw: (_ for _ in ()).throw(AssertionError("should not trigger")))
    h._handle_event(src_path="/x/a.jsonl")


def test_main_entrypoint_already_running_and_no_agents(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("watch_sync._try_acquire_lock", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        runpy.run_module("watch_sync", run_name="__main__")

    monkeypatch.setattr("watch_sync._try_acquire_lock", watch_sync._try_acquire_lock)
    monkeypatch.setenv("MEMU_AGENT_DIRS", "")
    monkeypatch.setenv("MEMU_EXTRA_PATHS", "[]")
    monkeypatch.delenv("OPENCLAW_SESSIONS_DIR", raising=False)
    monkeypatch.setattr("watch_sync.SESSIONS_DIR", None)

    class _Obs:
        def schedule(self, *a, **k):
            return None

        def unschedule(self, *a, **k):
            return None

        def start(self):
            return None

        def stop(self):
            return None

        def join(self):
            return None

    monkeypatch.setattr("watchdog.observers.Observer", lambda: _Obs())
    monkeypatch.setattr("signal.signal", lambda *a, **k: None)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    sleeps = {"n": 0}

    def _sleep(_s):
        sleeps["n"] += 1
        raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", _sleep)
    runpy.run_module("watch_sync", run_name="__main__")


def test_get_agent_session_files_non_dict_and_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    fake_mod = types.ModuleType("convert_sessions")
    setattr(fake_mod, "discover_session_files", lambda _sd, _a: [])
    monkeypatch.setitem(sys.modules, "convert_sessions", fake_mod)
    assert watch_sync._get_agent_session_files(str(sessions_dir), ["main"]) == {}

    def _boom(_sd, _a):
        raise RuntimeError("boom")

    fake_mod2 = types.ModuleType("convert_sessions")
    setattr(fake_mod2, "discover_session_files", _boom)
    monkeypatch.setitem(sys.modules, "convert_sessions", fake_mod2)
    assert watch_sync._get_agent_session_files(str(sessions_dir), ["main"]) == {}

from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

import pytest


def _load_convert_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path / "memu_data"))
    sys.modules.pop("convert_sessions", None)
    return importlib.import_module("convert_sessions")


def test_multi_agent_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    convert_sessions = _load_convert_sessions(monkeypatch, tmp_path)

    sessions_root = tmp_path / "sessions"
    for agent, sid in (("main", "m-1"), ("research", "r-1")):
        agent_dir = sessions_root / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "sessions.json").write_text(
            json.dumps({f"agent:{agent}:{agent}": {"sessionId": sid}}),
            encoding="utf-8",
        )
        (agent_dir / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")

    out = convert_sessions.discover_session_files(tmp_path, ["main", "research"])
    assert set(out.keys()) == {"main", "research"}
    assert out["main"].endswith("/sessions/main/m-1.jsonl")
    assert out["research"].endswith("/sessions/research/r-1.jsonl")


def test_legacy_path_support(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    convert_sessions = _load_convert_sessions(monkeypatch, tmp_path)

    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    (sessions_root / "sessions.json").write_text(
        json.dumps({"agent:main:main": {"sessionId": "legacy-main"}}),
        encoding="utf-8",
    )
    (sessions_root / "legacy-main.jsonl").write_text("{}\n", encoding="utf-8")

    out = convert_sessions.discover_session_files(tmp_path, ["main"])
    assert out == {"main": str(sessions_root / "legacy-main.jsonl")}


def test_missing_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    convert_sessions = _load_convert_sessions(monkeypatch, tmp_path)

    sessions_root = tmp_path / "sessions"
    (sessions_root / "main").mkdir(parents=True, exist_ok=True)
    (sessions_root / "main" / "sessions.json").write_text(
        json.dumps({"agent:main:main": {"sessionId": "missing-main"}}),
        encoding="utf-8",
    )

    caplog.set_level(logging.WARNING)
    out = convert_sessions.discover_session_files(tmp_path, ["main"])

    assert out == {}
    assert "session file missing for agent 'main'" in caplog.text

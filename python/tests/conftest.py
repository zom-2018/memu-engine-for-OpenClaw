from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from collections.abc import Generator
from typing import TypedDict

import pytest


class MultiAgentFixture(TypedDict):
    root: Path
    agents: dict[str, Path]
    shared: Path


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "multi_agent"


def load_multi_agent_fixture(tmp_path: Path) -> MultiAgentFixture:
    if not FIXTURE_ROOT.exists():
        msg = f"fixture root not found: {FIXTURE_ROOT}"
        raise FileNotFoundError(msg)

    destination = tmp_path / "multi_agent"
    if destination.exists():
        shutil.rmtree(destination)
    _ = shutil.copytree(FIXTURE_ROOT, destination)

    return {
        "root": destination,
        "agents": {
            "main": destination / "main",
            "research": destination / "research",
            "coding": destination / "coding",
        },
        "shared": destination / "shared",
    }


def cleanup_multi_agent_fixture(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _count_distinct_sessions(session_file: Path) -> int:
    session_ids: set[str] = set()
    for line in session_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        session_id = payload.get("session_id")
        if isinstance(session_id, str):
            session_ids.add(session_id)
    return len(session_ids)


def summarize_fixture_counts(root: Path) -> dict[str, int]:
    total_sessions = 0
    total_memories = 0

    for agent in ("main", "research", "coding"):
        session_file_idx = {"main": 1, "research": 2, "coding": 3}[agent]
        session_file = root / agent / "sessions" / f"session{session_file_idx}.jsonl"
        total_sessions += _count_distinct_sessions(session_file)

        conn = sqlite3.connect(root / agent / "memu.db")
        try:
            row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        finally:
            conn.close()
        total_memories += int(row[0] if row else 0)

    conn_shared = sqlite3.connect(root / "shared" / "memu.db")
    try:
        row_docs = conn_shared.execute("SELECT COUNT(*) FROM documents").fetchone()
    finally:
        conn_shared.close()

    return {
        "total_sessions": total_sessions,
        "total_memories": total_memories,
        "shared_documents": int(row_docs[0] if row_docs else 0),
    }


@pytest.fixture
def multi_agent_fixture(tmp_path: Path) -> Generator[MultiAgentFixture, None, None]:
    data = load_multi_agent_fixture(tmp_path)
    yield data
    cleanup_multi_agent_fixture(data["root"])

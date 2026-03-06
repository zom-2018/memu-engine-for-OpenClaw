#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "multi_agent"
AGENTS = ["main", "research", "coding"]
SESSIONS_PER_AGENT = 10


def _create_shared_db() -> None:
    shared_dir = ROOT / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(shared_dir / "memu.db")
    cur = conn.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS documents ("
        "id TEXT PRIMARY KEY, "
        "filename TEXT NOT NULL, "
        "owner_agent_id TEXT NOT NULL, "
        "created_at INTEGER"
        ")"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS document_chunks ("
        "id TEXT PRIMARY KEY, "
        "document_id TEXT NOT NULL, "
        "chunk_index INTEGER, "
        "start_token INTEGER DEFAULT 0, "
        "end_token INTEGER DEFAULT 0, "
        "content TEXT, "
        "embedding BLOB, "
        "filename TEXT, "
        "owner_agent_id TEXT, "
        "created_at INTEGER DEFAULT 0, "
        "FOREIGN KEY (document_id) REFERENCES documents(id)"
        ")"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_owner_agent ON documents(owner_agent_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_doc_id ON document_chunks(document_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_owner_agent ON document_chunks(owner_agent_id)")

    docs = [
        ("shared-doc-1", "incident_playbook.md", "main", "Incident response runbook for all agents"),
        ("shared-doc-2", "research_sources.md", "research", "Research citation standards and source validation"),
        ("shared-doc-3", "coding_style.md", "coding", "Coding standards and lint expectations"),
        ("shared-doc-4", "handoff_checklist.md", "main", "Agent handoff checklist for reliability"),
        ("shared-doc-5", "error_taxonomy.md", "research", "Error taxonomy: timeout, schema mismatch, tool failure"),
    ]

    for idx, (doc_id, filename, owner, content) in enumerate(docs):
        created_at = 1_700_000_000 + idx
        cur.execute(
            "INSERT OR REPLACE INTO documents (id, filename, owner_agent_id, created_at) VALUES (?, ?, ?, ?)",
            (doc_id, filename, owner, created_at),
        )
        cur.execute(
            "INSERT OR REPLACE INTO document_chunks "
            "(id, document_id, chunk_index, start_token, end_token, content, embedding, filename, owner_agent_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{doc_id}-chunk-0", doc_id, 0, 0, len(content.split()), content, None, filename, owner, created_at),
        )

    conn.commit()
    conn.close()


def _build_session_events(agent: str, session_idx: int, ts_base: int) -> list[dict[str, object]]:
    session_id = f"{agent}-session-{session_idx:02d}"
    events: list[dict[str, object]] = [
        {
            "session_id": session_id,
            "message_id": f"{session_id}-u",
            "role": "user",
            "type": "chat",
            "content": f"{agent} user request {session_idx}: analyze memory sync behavior",
            "timestamp": ts_base,
        },
        {
            "session_id": session_id,
            "message_id": f"{session_id}-a",
            "role": "assistant",
            "type": "chat",
            "content": f"{agent} assistant response {session_idx}: produced a concise remediation plan",
            "timestamp": ts_base + 1,
        },
        {
            "session_id": session_id,
            "message_id": f"{session_id}-t",
            "role": "assistant",
            "type": "tool_call",
            "tool_name": "memory_search",
            "tool_args": {"query": f"{agent} sync risk {session_idx}", "agentName": agent},
            "timestamp": ts_base + 2,
        },
    ]

    if session_idx % 3 == 0:
        events.append(
            {
                "session_id": session_id,
                "message_id": f"{session_id}-e",
                "role": "tool",
                "type": "error",
                "error_type": "timeout",
                "content": f"{agent} tool timeout while processing session {session_idx}",
                "timestamp": ts_base + 3,
            }
        )

    return events


def _create_agent_fixtures(agent: str, agent_index: int) -> None:
    agent_dir = ROOT / agent
    sessions_dir = agent_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_file = sessions_dir / f"session{agent_index + 1}.jsonl"

    jsonl_lines: list[str] = []
    for session_idx in range(1, SESSIONS_PER_AGENT + 1):
        ts_base = 1_700_100_000 + agent_index * 10_000 + session_idx * 100
        events = _build_session_events(agent, session_idx, ts_base)
        jsonl_lines.extend(json.dumps(evt, ensure_ascii=False) for evt in events)

    session_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

    conn = sqlite3.connect(agent_dir / "memu.db")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS memories ("
        "id TEXT PRIMARY KEY, "
        "agent_id TEXT NOT NULL, "
        "user_id TEXT NOT NULL, "
        "content TEXT, "
        "embedding BLOB, "
        "created_at INTEGER"
        ")"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_user ON memories(agent_id, user_id)")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "id TEXT PRIMARY KEY, "
        "agent_id TEXT NOT NULL, "
        "session_file TEXT NOT NULL, "
        "created_at INTEGER"
        ")"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS documents ("
        "id TEXT PRIMARY KEY, "
        "filename TEXT NOT NULL, "
        "owner_agent_id TEXT NOT NULL, "
        "created_at INTEGER"
        ")"
    )

    for session_idx in range(1, SESSIONS_PER_AGENT + 1):
        session_id = f"{agent}-session-{session_idx:02d}"
        created_at = 1_700_100_000 + agent_index * 10_000 + session_idx * 100
        cur.execute(
            "INSERT OR REPLACE INTO sessions (id, agent_id, session_file, created_at) VALUES (?, ?, ?, ?)",
            (session_id, agent, str(session_file), created_at),
        )
        cur.execute(
            "INSERT OR REPLACE INTO memories (id, agent_id, user_id, content, embedding, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"{agent}-mem-{session_idx:02d}",
                agent,
                f"user-{(session_idx % 3) + 1}",
                f"{agent} memory item {session_idx} derived from mixed chat/tool/error events",
                None,
                created_at,
            ),
        )

    cur.execute(
        "INSERT OR REPLACE INTO documents (id, filename, owner_agent_id, created_at) VALUES (?, ?, ?, ?)",
        (f"{agent}-doc-local", f"{agent}_local_notes.md", agent, 1_700_100_000 + agent_index),
    )

    conn.commit()
    conn.close()


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    _create_shared_db()
    for idx, agent in enumerate(AGENTS):
        _create_agent_fixtures(agent, idx)
    print(f"generated multi-agent fixtures at {ROOT}")


if __name__ == "__main__":
    main()

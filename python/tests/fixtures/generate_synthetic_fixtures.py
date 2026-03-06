#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parent


def _header(name: str, desc: str) -> str:
    return f"-- {name}\n-- {desc}\n\n"


def build_empty_db() -> str:
    return _header("empty_db.sql", "Intentionally empty SQL fixture (no tables).")


def build_single_agent() -> str:
    lines: list[str] = [
        _header(
            "single_agent.sql",
            "Single-agent fixture with 100 memories and no agentName column.",
        ),
        "BEGIN TRANSACTION;\n",
        "CREATE TABLE memu_memory_items (\n"
        "  id TEXT PRIMARY KEY,\n"
        "  memory_type TEXT NOT NULL,\n"
        "  summary TEXT NOT NULL,\n"
        "  created_at TEXT\n"
        ");\n\n",
    ]

    for i in range(1, 101):
        lines.append(
            "INSERT INTO memu_memory_items (id, memory_type, summary, created_at) VALUES "
            f"('single-mem-{i:03d}', 'profile', 'single agent memory {i}', '2026-01-01T00:00:00Z');\n"
        )

    lines.append("COMMIT;\n")
    return "".join(lines)


def build_multi_agent() -> str:
    return "".join(
        [
            _header(
                "multi_agent.sql",
                "Three-agent synthetic fixture with cross references.",
            ),
            "BEGIN TRANSACTION;\n",
            "CREATE TABLE memu_memory_items (\n"
            "  id TEXT PRIMARY KEY,\n"
            "  agent_id TEXT NOT NULL,\n"
            "  summary TEXT NOT NULL\n"
            ");\n\n",
            "CREATE TABLE memu_relations (\n"
            "  id TEXT PRIMARY KEY,\n"
            "  src_item_id TEXT NOT NULL,\n"
            "  dst_item_id TEXT NOT NULL,\n"
            "  relation_type TEXT NOT NULL\n"
            ");\n\n",
            "INSERT INTO memu_memory_items (id, agent_id, summary) VALUES\n"
            "  ('ma-a-001', 'agent-a', 'agent a seed memory'),\n"
            "  ('ma-b-001', 'agent-b', 'agent b seed memory'),\n"
            "  ('ma-c-001', 'agent-c', 'agent c seed memory');\n\n",
            "INSERT INTO memu_relations (id, src_item_id, dst_item_id, relation_type) VALUES\n"
            "  ('rel-001', 'ma-a-001', 'ma-b-001', 'references'),\n"
            "  ('rel-002', 'ma-b-001', 'ma-c-001', 'references'),\n"
            "  ('rel-003', 'ma-c-001', 'ma-a-001', 'references');\n",
            "COMMIT;\n",
        ]
    )


def build_large_db() -> str:
    lines: list[str] = [
        _header(
            "large_db.sql",
            "Large synthetic fixture with 1200 memories (minimal columns).",
        ),
        "BEGIN TRANSACTION;\n",
        "CREATE TABLE memu_memory_items (\n"
        "  id TEXT PRIMARY KEY,\n"
        "  memory_type TEXT NOT NULL,\n"
        "  summary TEXT NOT NULL\n"
        ");\n\n",
    ]

    for i in range(1, 1201):
        lines.append(
            "INSERT INTO memu_memory_items (id, memory_type, summary) VALUES "
            f"('large-mem-{i:04d}', 'knowledge', 'bulk memory row {i}');\n"
        )

    lines.append("COMMIT;\n")
    return "".join(lines)


def build_corrupted_schema() -> str:
    return "".join(
        [
            _header(
                "corrupted_schema.sql",
                "Schema intentionally missing legacy/new columns (e.g., agentName).",
            ),
            "BEGIN TRANSACTION;\n",
            "CREATE TABLE memu_memory_items (\n"
            "  id TEXT PRIMARY KEY,\n"
            "  summary TEXT NOT NULL\n"
            ");\n\n",
            "CREATE TABLE memu_memory_categories (\n"
            "  id TEXT PRIMARY KEY,\n"
            "  name TEXT NOT NULL\n"
            ");\n\n",
            "INSERT INTO memu_memory_items (id, summary) VALUES ('corrupt-001', 'row without expected columns');\n",
            "COMMIT;\n",
        ]
    )


def main() -> None:
    outputs = {
        "empty_db.sql": build_empty_db(),
        "single_agent.sql": build_single_agent(),
        "multi_agent.sql": build_multi_agent(),
        "large_db.sql": build_large_db(),
        "corrupted_schema.sql": build_corrupted_schema(),
    }

    for filename, content in outputs.items():
        path = FIXTURE_DIR / filename
        path.write_text(content, encoding="utf-8")
        print(f"generated {filename}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("migrate_agent_id")

PENDING_AGENT_ID = "__MIGRATION_PENDING_AGENT_ID__"
TARGET_TABLES = (
    "memu_memory_items",
    "memu_resources",
    "memu_memory_categories",
    "memu_relations",
    "memu_category_items",
)


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    name: str
    aliases: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill stable agent_id into MemU SQLite tables")
    default_memory_root = os.getenv("MEMU_MEMORY_ROOT") or os.path.expanduser(
        "~/.openclaw/memUdata/memory"
    )
    default_agent_name = (os.getenv("MEMU_AGENT_NAME") or "main").strip() or "main"
    default_db_path = os.getenv("MEMU_DB_PATH") or os.path.join(
        default_memory_root,
        default_agent_name,
        "memu.db",
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=(
            "Path to SQLite database "
            "(default: $MEMU_DB_PATH or $MEMU_MEMORY_ROOT/$MEMU_AGENT_NAME/memu.db)"
        ),
    )
    parser.add_argument(
        "--config-path",
        default=os.getenv("OPENCLAW_CONFIG") or os.path.expanduser("~/.openclaw/openclaw.json"),
        help="Path to OpenClaw config json (default: $OPENCLAW_CONFIG or ~/.openclaw/openclaw.json)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run migration checks and SQL, then rollback")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def _extract_agents_list(config_obj: dict[str, Any]) -> list[dict[str, Any]]:
    agents = config_obj.get("agents")
    if not isinstance(agents, dict):
        return []
    raw_list = agents.get("list")
    if isinstance(raw_list, list):
        return [x for x in raw_list if isinstance(x, dict)]
    if isinstance(raw_list, dict):
        return [x for x in raw_list.values() if isinstance(x, dict)]
    return []


def _normalize_aliases(agent: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for key in ("aliases", "legacyNames", "previousNames"):
        value = agent.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    aliases.add(item.strip())
    return aliases


def load_agent_records(config_path: str) -> list[AgentRecord]:
    path = Path(config_path)
    if not path.exists():
        raise RuntimeError(
            f"OpenClaw config not found: {config_path}. "
            "Provide --config-path pointing to a config containing agents.list[]."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise RuntimeError("Invalid config format: root must be a JSON object")

    agents_list = _extract_agents_list(raw)
    if not agents_list:
        raise RuntimeError(
            "Config missing agents.list[]. Cannot resolve stable agent_id mappings. "
            "Add agents.list[] with objects containing id and name."
        )

    records: list[AgentRecord] = []
    for idx, agent in enumerate(agents_list):
        agent_id = agent.get("id")
        name = agent.get("name")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise RuntimeError(f"agents.list[{idx}] missing non-empty string 'id'")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(f"agents.list[{idx}] missing non-empty string 'name'")
        aliases = _normalize_aliases(agent)
        aliases.add(name.strip())
        records.append(
            AgentRecord(
                agent_id=agent_id.strip(),
                name=name.strip(),
                aliases=tuple(sorted(aliases)),
            )
        )
    return records


def validate_agent_name_mapping(records: list[AgentRecord]) -> dict[str, str]:
    name_to_ids: dict[str, set[str]] = {}
    for record in records:
        for alias in record.aliases:
            name_to_ids.setdefault(alias, set()).add(record.agent_id)

    ambiguous = {name: ids for name, ids in name_to_ids.items() if len(ids) > 1}
    if ambiguous:
        details = "; ".join(f"{name} -> {sorted(ids)}" for name, ids in sorted(ambiguous.items()))
        raise RuntimeError(
            "Ambiguous agent name/alias mapping in agents.list[]. "
            f"Resolve duplicate names before migration: {details}"
        )

    return {name: next(iter(ids)) for name, ids in name_to_ids.items()}


def backup_database(db_path: str) -> str:
    if not Path(db_path).exists():
        raise RuntimeError(f"Database not found: {db_path}")

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = f"{db_path}.backup.{ts}"
    shutil.copy2(db_path, backup_path)
    logger.info("Created backup: %s", backup_path)
    return backup_path


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    )
    return cursor.fetchone() is not None


def _table_columns(cursor: sqlite3.Cursor, table: str) -> list[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return [str(row[1]) for row in cursor.fetchall()]


def _index_exists(cursor: sqlite3.Cursor, index_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=? LIMIT 1",
        (index_name,),
    )
    return cursor.fetchone() is not None


def _collect_unresolved_rows(cursor: sqlite3.Cursor, table: str) -> list[tuple[str, str | None]]:
    cols = _table_columns(cursor, table)
    id_col = "id" if "id" in cols else None
    select_id = id_col or "rowid"
    cursor.execute(
        f"""
        SELECT {select_id}, agentName
        FROM {table}
        WHERE agent_id IS NULL OR TRIM(agent_id) = '' OR agent_id = ?
        LIMIT 50
        """,
        (PENDING_AGENT_ID,),
    )
    out: list[tuple[str, str | None]] = []
    for raw_id, raw_name in cursor.fetchall():
        out.append((str(raw_id), raw_name if isinstance(raw_name, str) else None))
    return out


def _validate_orphaned_agent_names(
    cursor: sqlite3.Cursor,
    tables: list[str],
    name_to_id: dict[str, str],
) -> None:
    known = set(name_to_id)
    orphaned_by_table: dict[str, list[str]] = {}

    for table in tables:
        cursor.execute(
            f"SELECT DISTINCT agentName FROM {table} WHERE agentName IS NOT NULL AND TRIM(agentName) != ''"
        )
        names = [row[0] for row in cursor.fetchall() if isinstance(row[0], str)]
        missing = sorted(name for name in names if name not in known)
        if missing:
            orphaned_by_table[table] = missing

    if orphaned_by_table:
        details = "; ".join(f"{table}: {names}" for table, names in sorted(orphaned_by_table.items()))
        raise RuntimeError(
            "Found orphaned agentName values not present in agents.list[] mapping. "
            f"Add alias/name entries before migration. Details: {details}"
        )


def migrate_agent_id(*, db_path: str, config_path: str, dry_run: bool) -> None:
    db_file = Path(db_path)
    if not db_file.exists():
        logger.info("Database does not exist yet: %s. Nothing to migrate.", db_path)
        return

    records = load_agent_records(config_path)
    name_to_id = validate_agent_name_mapping(records)
    logger.info("Loaded %d agents from config", len(records))

    if not dry_run:
        backup_database(db_path)
    else:
        logger.info("Dry-run enabled: backup skipped")

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row

    tables: list[str] = []
    try:
        cursor = conn.cursor()
        for table in TARGET_TABLES:
            if _table_exists(cursor, table):
                tables.append(table)

        if not tables:
            logger.info("No target tables found. Nothing to migrate.")
            return

        if "memu_relations" in tables and "memu_category_items" in tables:
            raise RuntimeError(
                "Both memu_relations and memu_category_items exist. "
                "Resolve table naming before running migration."
            )

        logger.info("Target tables: %s", tables)

        for table in tables:
            cols = _table_columns(cursor, table)
            if "agentName" not in cols:
                raise RuntimeError(
                    f"Table {table} is missing agentName column. "
                    "Run the prior agentName migration before agent_id migration."
                )

        _validate_orphaned_agent_names(cursor, tables, name_to_id)

        conn.execute("BEGIN IMMEDIATE")

        for table in tables:
            cols = _table_columns(cursor, table)
            if "agent_id" not in cols:
                logger.info("Adding agent_id column to %s", table)
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN agent_id TEXT NOT NULL DEFAULT '{PENDING_AGENT_ID}'",
                )
            else:
                logger.info("Table %s already has agent_id column", table)

            for name, agent_id in name_to_id.items():
                cursor.execute(
                    f"""
                    UPDATE {table}
                    SET agent_id = ?
                    WHERE agentName = ?
                      AND (agent_id IS NULL OR TRIM(agent_id) = '' OR agent_id = ?)
                    """,
                    (agent_id, name, PENDING_AGENT_ID),
                )

            unresolved = _collect_unresolved_rows(cursor, table)
            if unresolved:
                by_name: dict[str, list[str]] = {}
                for row_id, name in unresolved:
                    key = name or "<NULL_OR_EMPTY_AGENTNAME>"
                    by_name.setdefault(key, []).append(row_id)
                detail = "; ".join(
                    f"{name}: ids={ids[:10]}{'...' if len(ids) > 10 else ''}"
                    for name, ids in sorted(by_name.items())
                )
                raise RuntimeError(
                    f"Unresolved agent_id values remain in {table}. "
                    f"Likely orphaned/renamed agent names. Details: {detail}"
                )

        if _table_exists(cursor, "memu_memory_items") and not _index_exists(cursor, "idx_agent_scope"):
            logger.info("Creating composite index idx_agent_scope")
            cursor.execute(
                "CREATE INDEX idx_agent_scope ON memu_memory_items(agent_id, user_id)"
            )

        for table in tables:
            cursor.execute(
                f"SELECT COUNT(*) FROM {table} WHERE agent_id IS NULL OR TRIM(agent_id) = '' OR agent_id = ?",
                (PENDING_AGENT_ID,),
            )
            count = int(cursor.fetchone()[0])
            if count != 0:
                raise RuntimeError(f"Validation failed: {table} still has {count} unresolved rows")

        if dry_run:
            conn.rollback()
            logger.info("Dry-run successful. Transaction rolled back.")
        else:
            conn.commit()
            logger.info("Migration committed successfully.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    try:
        migrate_agent_id(db_path=args.db_path, config_path=args.config_path, dry_run=args.dry_run)
        return 0
    except Exception as exc:
        logger.error("Migration failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

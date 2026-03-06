from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import cast

from ..storage_layout import backup_legacy_data, detect_legacy_v0_2_6_layout
from . import migrate_existing_memories
from .validator import (
    MigrationState,
    build_migration_report,
    load_migration_state,
    run_post_migration_validation,
    render_post_migration_validation_report,
    save_migration_state,
)

logger = logging.getLogger("memu.migration.v0_2_6_to_v0_3_1")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate MemU v0.2.6 layout for migration to v0.3.1",
    )
    _ = parser.add_argument("--source", required=True, help="Legacy source root to inspect")
    _ = parser.add_argument("--target", default="", help="Target root for migrated hybrid layout")
    _ = parser.add_argument("--dry-run", action="store_true", help="Validate only; do not modify source data")
    _ = parser.add_argument("--backup-root", default="", help="Optional directory for backup files")
    _ = parser.add_argument("--backup-only", action="store_true", help="Only perform verified backup for legacy source")
    _ = parser.add_argument("--resume", action="store_true", help="Resume from migration_state.json if present")
    _ = parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _migrate_legacy_db_to_target(source_db: Path, target_db: Path) -> None:
    _ = target_db.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copy2(source_db, target_db)
    if not migrate_existing_memories(str(target_db)):
        raise RuntimeError(f"Legacy table migration failed for {target_db}")

    conn = sqlite3.connect(target_db)
    try:
        if _table_exists(conn, "memu_memory_items"):
            cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(memu_memory_items)").fetchall()]
            if "agent_id" not in cols:
                _ = conn.execute("ALTER TABLE memu_memory_items ADD COLUMN agent_id TEXT")
            _ = conn.execute("UPDATE memu_memory_items SET agent_id='main' WHERE agent_id IS NULL OR TRIM(agent_id) = ''")
            _ = conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_scope ON memu_memory_items(agent_id)")
        conn.commit()
    finally:
        conn.close()


def _create_secondary_agent_db(main_db: Path, secondary_db: Path, agent_name: str) -> None:
    _ = secondary_db.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copy2(main_db, secondary_db)
    conn = sqlite3.connect(secondary_db)
    try:
        if _table_exists(conn, "memu_memory_items"):
            cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(memu_memory_items)").fetchall()]
            if "agent_id" not in cols:
                _ = conn.execute("ALTER TABLE memu_memory_items ADD COLUMN agent_id TEXT")
            _ = conn.execute("UPDATE memu_memory_items SET agent_id=?", (agent_name,))
            conn.commit()
    finally:
        conn.close()


def _create_shared_db(target_root: Path) -> None:
    shared_db = target_root / "shared" / "memu.db"
    _ = shared_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(shared_db)
    try:
        _ = conn.execute(
            "CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, filename TEXT, owner_agent_id TEXT, created_at INTEGER)"
        )
        _ = conn.execute(
            "CREATE TABLE IF NOT EXISTS document_chunks (id TEXT PRIMARY KEY, document_id TEXT, chunk_index INTEGER, content TEXT, owner_agent_id TEXT, created_at INTEGER)"
        )
        conn.commit()
    finally:
        conn.close()


def _perform_target_migration(source: str, target: str) -> Path:
    source_root = Path(source).expanduser().resolve()
    target_root = Path(target).expanduser().resolve()
    _ = target_root.mkdir(parents=True, exist_ok=True)

    source_db = source_root / "memu.db"
    if not source_db.exists():
        raise FileNotFoundError(f"Missing source legacy DB: {source_db}")

    main_db = target_root / "main" / "memu.db"
    _migrate_legacy_db_to_target(source_db, main_db)
    _create_secondary_agent_db(main_db, target_root / "secondary" / "memu.db", "secondary")
    _create_shared_db(target_root)
    return target_root


def _render_report(report_dict: dict[str, object]) -> None:
    print(f"Source: {report_dict['source']}")
    print(f"Detected v0.2.6 layout: {report_dict['detected_v0_2_6']}")
    print(f"Estimated migration time: ~{report_dict['estimated_time_seconds']}s")
    print("Will migrate:")
    for target in cast(list[str], report_dict["migration_targets"]):
        print(f"  - {target}")

    print("Checksums:")
    checksums = cast(dict[str, str], report_dict["checksums"])
    for rel_path, digest in sorted(checksums.items()):
        print(f"  - {rel_path}: {digest}")

    print("Potential issues:")
    if not report_dict["issues"]:
        print("  - none")
    else:
        for issue in cast(list[dict[str, str]], report_dict["issues"]):
            print(f"  - [{issue['severity']}] {issue['message']}")


def _build_state(
    source: str,
    dry_run: bool,
    stage: str,
    status: str,
    report_dict: dict[str, object],
) -> MigrationState:
    now = datetime.now().isoformat(timespec="seconds")
    existing = load_migration_state(source, dry_run=dry_run)
    created_at = existing.created_at if existing is not None and existing.created_at else now
    return MigrationState(
        source=source,
        created_at=created_at,
        updated_at=now,
        status=status,
        dry_run=dry_run,
        stage=stage,
        checksum_manifest=dict(cast(dict[str, str], report_dict.get("checksums", {}))),
        report=report_dict,
    )


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source_arg = cast(str, args.source)
    target_arg = cast(str, args.target)
    dry_run = bool(args.dry_run)
    backup_root_arg = cast(str, args.backup_root)
    backup_only = bool(args.backup_only)
    resume = bool(args.resume)
    verbose = bool(args.verbose)

    _configure_logging(verbose)

    source = str(Path(source_arg).expanduser().resolve())
    prior_state = load_migration_state(source, dry_run=dry_run)
    if resume and prior_state is not None:
        logger.info("Resuming migration tracking from stage=%s status=%s", prior_state.stage, prior_state.status)

    layout = detect_legacy_v0_2_6_layout(source)
    if not layout.detected:
        logger.error("No legacy v0.2.6 data detected at source: %s", source)
        return 1

    if backup_only:
        backup_root = Path(backup_root_arg).expanduser().resolve() if backup_root_arg else None
        try:
            backup_path = backup_legacy_data(source, backup_root=backup_root)
        except Exception as exc:
            logger.exception("Backup failed for %s: %s", source, exc)
            return 1
        logger.info("Backup completed: %s", backup_path)
        print(f"Backup prepared: {backup_path}")
        return 0

    target_root: Path | None = None
    if target_arg.strip():
        try:
            target_root = _perform_target_migration(source, target_arg)
        except Exception as exc:
            logger.exception("Migration to target failed: %s", exc)
            return 1

        validation_report = run_post_migration_validation(source=source, target=target_root)
        render_post_migration_validation_report(validation_report)
        if not validation_report.passed:
            logger.error("Post-migration validation failed")
            return 1
        logger.info("Post-migration validation succeeded")

    report = build_migration_report(source)
    report_dict = asdict(report)

    state = _build_state(
        source=source,
        dry_run=dry_run,
        stage="validation",
        status="in_progress",
        report_dict=report_dict,
    )
    _ = save_migration_state(state, dry_run=dry_run)

    backup_info = ""
    if report.migration_targets and not dry_run:
        backup_root = Path(backup_root_arg).expanduser().resolve() if backup_root_arg else None
        try:
            backup_path = backup_legacy_data(source, backup_root=backup_root)
        except Exception as exc:
            logger.exception("Backup failed for %s: %s", source, exc)
            return 1
        backup_info = str(backup_path)
        logger.info("Prepared backup copy: %s", backup_path)

    _render_report(report_dict)
    if backup_info:
        print(f"Backup prepared: {backup_info}")

    error_count = sum(1 for issue in report.issues if issue.severity == "error")
    print(f"Validation successful: {error_count} errors")

    final_state = _build_state(
        source=source,
        dry_run=dry_run,
        stage="validation_complete",
        status="completed" if error_count == 0 else "failed",
        report_dict=report_dict,
    )
    _ = save_migration_state(final_state, dry_run=dry_run)

    if dry_run:
        logger.info("Dry-run mode: no migration writes were applied to source data")

    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(run())

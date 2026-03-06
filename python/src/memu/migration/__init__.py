from __future__ import annotations

import importlib.util
from pathlib import Path

from .validator import (
    MigrationIssue,
    MigrationReport,
    MigrationState,
    build_migration_report,
    create_timestamped_backup,
    load_migration_state,
    save_migration_state,
)

_LEGACY_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migration.py"
_legacy_spec = importlib.util.spec_from_file_location(
    "memu._legacy_migration",
    _LEGACY_MIGRATION_PATH,
)
if _legacy_spec is None or _legacy_spec.loader is None:
    raise RuntimeError(f"Unable to load legacy migration module at {_LEGACY_MIGRATION_PATH}")

_legacy_module = importlib.util.module_from_spec(_legacy_spec)
_legacy_spec.loader.exec_module(_legacy_module)

TABLES_TO_MIGRATE = _legacy_module.TABLES_TO_MIGRATE
backup_database = _legacy_module.backup_database
check_database_version = _legacy_module.check_database_version
migrate_existing_memories = _legacy_module.migrate_existing_memories
verify_migration = _legacy_module.verify_migration
_execute_migration = _legacy_module._execute_migration

__all__ = [
    "MigrationIssue",
    "MigrationReport",
    "MigrationState",
    "TABLES_TO_MIGRATE",
    "backup_database",
    "build_migration_report",
    "check_database_version",
    "create_timestamped_backup",
    "load_migration_state",
    "migrate_existing_memories",
    "save_migration_state",
    "verify_migration",
    "_execute_migration",
]

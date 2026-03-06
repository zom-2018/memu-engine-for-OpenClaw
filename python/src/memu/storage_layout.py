from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

TARGET_AGENT_ID_TABLES = (
    "memu_memory_items",
    "memu_resources",
    "memu_memory_categories",
    "memu_relations",
    "memu_category_items",
)

DEFAULT_MEMORY_ROOT = "~/.openclaw/" + "memU" + "data/memory"


def memory_root_path(memory_root: str | Path | None = None) -> Path:
    if memory_root is not None:
        return Path(memory_root).expanduser()
    raw = os.getenv("MEMU_MEMORY_ROOT", DEFAULT_MEMORY_ROOT)
    return Path(raw).expanduser()


def legacy_data_dir_path() -> Path:
    raw = os.getenv("MEMU_DATA_DIR")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[3] / "data"


def legacy_db_path() -> Path:
    return legacy_data_dir_path() / "memu.db"


def agent_db_path(agent_name: str, memory_root: str | Path | None = None) -> Path:
    return (memory_root_path(memory_root) / agent_name / "memu.db").expanduser()


def shared_db_path(memory_root: str | Path | None = None) -> Path:
    return (memory_root_path(memory_root) / "shared" / "memu.db").expanduser()


def agent_db_dsn(agent_name: str, memory_root: str | Path | None = None) -> str:
    return f"sqlite:///{agent_db_path(agent_name, memory_root)}"


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
    return cursor.fetchone() is not None


def _table_columns(cursor: sqlite3.Cursor, table: str) -> list[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return [str(row[1]) for row in cursor.fetchall()]


def _index_exists(cursor: sqlite3.Cursor, index_name: str) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=? LIMIT 1", (index_name,))
    return cursor.fetchone() is not None


def _ensure_agent_id_columns(db_path: Path, default_agent: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for table in TARGET_AGENT_ID_TABLES:
            if not _table_exists(cur, table):
                continue
            columns = _table_columns(cur, table)
            if "agent_id" not in columns:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN agent_id TEXT")
            cur.execute(
                f"""
                UPDATE {table}
                SET agent_id = ?
                WHERE agent_id IS NULL OR TRIM(agent_id) = ''
                """,
                (default_agent,),
            )

        if _table_exists(cur, "memu_memory_items") and not _index_exists(cur, "idx_agent_scope"):
            cur.execute("CREATE INDEX idx_agent_scope ON memu_memory_items(agent_id, user_id)")
        conn.commit()
    finally:
        conn.close()


@dataclass
class LegacyMigrationResult:
    migrated: bool
    reason: str
    source_db: str
    target_db: str
    backup_path: str | None = None


@dataclass
class LegacyV026LayoutInfo:
    source_root: str
    legacy_db_path: str
    has_legacy_db: bool
    has_global_last_sync_ts: bool
    has_global_pending_ingest: bool
    conversation_files: list[str]
    conversation_subdirectories: list[str]

    @property
    def detected(self) -> bool:
        return (
            self.has_legacy_db
            or self.has_global_last_sync_ts
            or self.has_global_pending_ingest
            or bool(self.conversation_files)
        )


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _iter_files(root: Path) -> list[Path]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return sorted(files, key=lambda p: str(p.relative_to(root)))


def detect_legacy_v0_2_6_layout(source_root: str | Path | None = None) -> LegacyV026LayoutInfo:
    root = memory_root_path(source_root)

    legacy_db = root / "memu.db"
    last_sync_ts = root / "data" / "last_sync_ts"
    pending_ingest = root / "data" / "pending_ingest.json"
    conv_root = root / "data" / "conversations"

    conversation_files: list[str] = []
    conversation_subdirectories: list[str] = []
    if conv_root.exists() and conv_root.is_dir():
        for entry in sorted(conv_root.iterdir()):
            if entry.is_file() and entry.suffix.lower() == ".json":
                conversation_files.append(str(entry))
            elif entry.is_dir():
                conversation_subdirectories.append(str(entry))

    return LegacyV026LayoutInfo(
        source_root=str(root),
        legacy_db_path=str(legacy_db),
        has_legacy_db=legacy_db.exists(),
        has_global_last_sync_ts=last_sync_ts.exists(),
        has_global_pending_ingest=pending_ingest.exists(),
        conversation_files=conversation_files,
        conversation_subdirectories=conversation_subdirectories,
    )


def backup_legacy_data(source_root: str | Path, backup_root: str | Path | None = None) -> str:
    source = Path(source_root).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"Legacy source root does not exist or is not a directory: {source}")

    backup_base = Path(backup_root).expanduser().resolve() if backup_root is not None else source
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(f"{backup_base}.backup.{timestamp}").resolve()

    if backup_dir.exists():
        raise FileExistsError(f"Backup path already exists: {backup_dir}")

    try:
        shutil.copytree(source, backup_dir)

        source_files = _iter_files(source)
        source_by_rel = {str(path.relative_to(source)): path for path in source_files}
        source_checksums = {rel: _sha256_file(path) for rel, path in source_by_rel.items()}

        copied_files = _iter_files(backup_dir)
        copied_by_rel = {str(path.relative_to(backup_dir)): path for path in copied_files}
        copied_checksums = {rel: _sha256_file(path) for rel, path in copied_by_rel.items()}

        if set(source_checksums.keys()) != set(copied_checksums.keys()):
            missing = sorted(set(source_checksums.keys()) - set(copied_checksums.keys()))
            extra = sorted(set(copied_checksums.keys()) - set(source_checksums.keys()))
            raise RuntimeError(f"Backup file set mismatch (missing={missing}, extra={extra})")

        checksum_mismatch = [
            rel_path
            for rel_path, src_checksum in source_checksums.items()
            if copied_checksums.get(rel_path) != src_checksum
        ]
        if checksum_mismatch:
            raise RuntimeError(f"Checksum mismatch for copied files: {checksum_mismatch}")

        manifest_path = backup_dir / "manifest.json"
        manifest = {
            "source_root": str(source),
            "backup_root": str(backup_dir),
            "timestamp": timestamp,
            "file_count": len(source_checksums),
            "files": [
                {"path": rel_path, "sha256": source_checksums[rel_path]}
                for rel_path in sorted(source_checksums.keys())
            ],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        return str(backup_dir)
    except Exception:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def migrate_legacy_single_db_to_agent_db(
    default_agent: str = "main",
    memory_root: str | Path | None = None,
) -> LegacyMigrationResult:
    src = legacy_db_path()
    target = agent_db_path(default_agent, memory_root)

    if not src.exists():
        return LegacyMigrationResult(
            migrated=False,
            reason="legacy_db_missing",
            source_db=str(src),
            target_db=str(target),
        )

    if target.exists():
        return LegacyMigrationResult(
            migrated=False,
            reason="target_exists",
            source_db=str(src),
            target_db=str(target),
        )

    target.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = src.with_suffix(f".db.backup_{timestamp}")
    shutil.copy2(src, backup)
    shutil.copy2(src, target)

    legacy_migration_path = Path(__file__).with_name("migration.py")
    legacy_spec = importlib.util.spec_from_file_location(
        "memu._legacy_migration",
        legacy_migration_path,
    )
    if legacy_spec is None or legacy_spec.loader is None:
        raise RuntimeError(f"Unable to load legacy migration module at {legacy_migration_path}")
    legacy_module = importlib.util.module_from_spec(legacy_spec)
    legacy_spec.loader.exec_module(legacy_module)
    legacy_module.migrate_existing_memories(str(target))
    _ensure_agent_id_columns(target, default_agent=default_agent)

    return LegacyMigrationResult(
        migrated=True,
        reason="ok",
        source_db=str(src),
        target_db=str(target),
        backup_path=str(backup),
    )


def parse_agent_settings_from_env() -> dict[str, dict[str, Any]]:
    raw = os.getenv("MEMU_AGENT_SETTINGS", "")
    if not raw.strip():
        return {}
    try:
        import json

        loaded = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, cfg in loaded.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(cfg, dict):
            cfg = {}
        search_stores = cfg.get("searchableStores")
        normalized_stores: list[str] = []
        if isinstance(search_stores, list):
            for store in search_stores:
                if isinstance(store, str) and store.strip():
                    normalized_stores.append(store.strip())
        if not normalized_stores:
            normalized_stores = ["self"]
        out[name] = {
            "memoryEnabled": bool(cfg.get("memoryEnabled", True)),
            "searchEnabled": bool(cfg.get("searchEnabled", True)),
            "searchableStores": normalized_stores,
        }
    return out


def resolve_agent_policy(agent_name: str, settings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cfg = settings.get(agent_name) or {}
    raw_stores = cfg.get("searchableStores")
    out_stores: list[str] = []
    if isinstance(raw_stores, list):
        out_stores = [str(v).strip() for v in raw_stores if str(v).strip()]
    if not out_stores:
        out_stores = ["self"]
    return {
        "memoryEnabled": bool(cfg.get("memoryEnabled", True)),
        "searchEnabled": bool(cfg.get("searchEnabled", True)),
        "searchableStores": out_stores,
    }

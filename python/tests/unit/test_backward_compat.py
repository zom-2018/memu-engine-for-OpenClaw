from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from memu.storage_layout import (
    detect_legacy_v0_2_6_layout,
    migrate_legacy_single_db_to_agent_db,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
INDEX_TS = REPO_ROOT / "index.ts"


def _index_source() -> str:
    return INDEX_TS.read_text(encoding="utf-8")


def _create_legacy_v026_db(db_path: Path, summaries: list[str]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE memu_memory_items (id TEXT PRIMARY KEY, user_id TEXT, summary TEXT)"
        )
        conn.execute(
            "CREATE TABLE memu_memory_categories (id TEXT PRIMARY KEY, name TEXT)"
        )
        conn.execute("CREATE TABLE memu_resources (id TEXT PRIMARY KEY, title TEXT)")
        for idx, summary in enumerate(summaries, start=1):
            conn.execute(
                "INSERT INTO memu_memory_items (id, user_id, summary) VALUES (?, ?, ?)",
                (f"m-{idx}", "u-1", summary),
            )
        conn.execute("INSERT INTO memu_memory_categories (id, name) VALUES ('c-1', 'general')")
        conn.execute("INSERT INTO memu_resources (id, title) VALUES ('r-1', 'resource')")
        conn.commit()
    finally:
        conn.close()


def _read_summaries(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT summary FROM memu_memory_items ORDER BY id").fetchall()
        return [str(row[0]) for row in rows]
    finally:
        conn.close()


def test_old_config_works() -> None:
    source = _index_source()

    assert "const hasLegacyAllowCrossAgentRetrieval = typeof config.allowCrossAgentRetrieval === \"boolean\";" in source
    assert "const legacyStores = config.allowCrossAgentRetrieval === true ? [\"self\", \"shared\"] : [\"self\"];" in source


def test_allow_cross_agent_retrieval_logs_deprecation_warning() -> None:
    source = _index_source()

    assert "'allowCrossAgentRetrieval' is deprecated" in source
    assert "Migration guide: set per-agent 'agentSettings.<agent>.searchableStores' instead." in source


def test_agent_settings_preferred_when_both_present() -> None:
    source = _index_source()

    assert "if (!normalizedAgentSettings[agentName])" in source


def test_v026_data_compatibility_detection(tmp_path: Path) -> None:
    source_root = tmp_path / "legacy-v026"
    data_root = source_root / "data"
    conv_root = data_root / "conversations"
    conv_root.mkdir(parents=True)

    _create_legacy_v026_db(source_root / "memu.db", ["legacy summary"])
    (data_root / "last_sync_ts").write_text("1700000000", encoding="utf-8")
    (data_root / "pending_ingest.json").write_text('{"queue": []}', encoding="utf-8")
    (conv_root / "session-1.json").write_text('{"messages": []}', encoding="utf-8")
    (conv_root / "main").mkdir(parents=True)

    layout = detect_legacy_v0_2_6_layout(source_root)
    assert layout.detected is True
    assert layout.has_legacy_db is True
    assert layout.has_global_last_sync_ts is True
    assert layout.has_global_pending_ingest is True
    assert len(layout.conversation_files) == 1
    assert layout.conversation_files[0].endswith("session-1.json")
    assert len(layout.conversation_subdirectories) == 1


def test_migration_triggered(tmp_path: Path, monkeypatch) -> None:
    legacy_data_dir = tmp_path / "legacy-data"
    _create_legacy_v026_db(legacy_data_dir / "memu.db", ["before migration"])

    memory_root = tmp_path / "memory-root"
    monkeypatch.setenv("MEMU_DATA_DIR", str(legacy_data_dir))

    result = migrate_legacy_single_db_to_agent_db(default_agent="main", memory_root=memory_root)

    assert result.migrated is True
    assert result.reason == "ok"
    assert result.backup_path is not None
    assert Path(result.backup_path).exists()

    target_db = memory_root / "main" / "memu.db"
    assert target_db.exists()

    conn = sqlite3.connect(target_db)
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(memu_memory_items)").fetchall()]
        assert "agentName" in columns
        assert "agent_id" in columns
        count = conn.execute("SELECT COUNT(*) FROM memu_memory_items").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_upgrade_scenario_preserves_legacy_data(tmp_path: Path, monkeypatch) -> None:
    legacy_data_dir = tmp_path / "legacy-upgrade"
    original = ["legacy-1", "legacy-2"]
    _create_legacy_v026_db(legacy_data_dir / "memu.db", original)
    monkeypatch.setenv("MEMU_DATA_DIR", str(legacy_data_dir))

    memory_root = tmp_path / "memory-upgrade"
    result = migrate_legacy_single_db_to_agent_db(default_agent="main", memory_root=memory_root)

    assert result.migrated is True
    assert _read_summaries(memory_root / "main" / "memu.db") == original


def test_fresh_install_scenario_no_legacy_migration(tmp_path: Path, monkeypatch) -> None:
    clean_legacy_dir = tmp_path / "fresh-install"
    clean_legacy_dir.mkdir(parents=True)
    monkeypatch.setenv("MEMU_DATA_DIR", str(clean_legacy_dir))

    result = migrate_legacy_single_db_to_agent_db(default_agent="main", memory_root=tmp_path / "memory-root")

    assert result.migrated is False
    assert result.reason == "legacy_db_missing"


def test_partial_migration_scenario_target_exists(tmp_path: Path, monkeypatch) -> None:
    legacy_data_dir = tmp_path / "legacy-partial"
    _create_legacy_v026_db(legacy_data_dir / "memu.db", ["legacy-source"])

    memory_root = tmp_path / "memory-partial"
    target_db = memory_root / "main" / "memu.db"
    _create_legacy_v026_db(target_db, ["already-migrated-target"])

    monkeypatch.setenv("MEMU_DATA_DIR", str(legacy_data_dir))
    result = migrate_legacy_single_db_to_agent_db(default_agent="main", memory_root=memory_root)

    assert result.migrated is False
    assert result.reason == "target_exists"
    assert _read_summaries(target_db) == ["already-migrated-target"]


def test_rollback_scenario_restore_from_backup(tmp_path: Path, monkeypatch) -> None:
    legacy_data_dir = tmp_path / "legacy-rollback"
    source_db = legacy_data_dir / "memu.db"
    _create_legacy_v026_db(source_db, ["rollback-original"])
    monkeypatch.setenv("MEMU_DATA_DIR", str(legacy_data_dir))

    result = migrate_legacy_single_db_to_agent_db(default_agent="main", memory_root=tmp_path / "memory-rollback")
    assert result.migrated is True
    assert result.backup_path is not None

    conn = sqlite3.connect(source_db)
    try:
        conn.execute("UPDATE memu_memory_items SET summary='mutated-after-upgrade' WHERE id='m-1'")
        conn.commit()
    finally:
        conn.close()

    shutil.copy2(Path(result.backup_path), source_db)
    assert _read_summaries(source_db) == ["rollback-original"]


def test_rollback_scenario_backup_is_timestamped(tmp_path: Path, monkeypatch) -> None:
    legacy_data_dir = tmp_path / "legacy-backup"
    _create_legacy_v026_db(legacy_data_dir / "memu.db", ["backup-check"])
    monkeypatch.setenv("MEMU_DATA_DIR", str(legacy_data_dir))

    result = migrate_legacy_single_db_to_agent_db(default_agent="main", memory_root=tmp_path / "memory-backup")

    assert result.migrated is True
    assert result.backup_path is not None
    backup_name = Path(result.backup_path).name
    assert backup_name.startswith("memu.db.backup_")
    assert Path(result.backup_path).exists()

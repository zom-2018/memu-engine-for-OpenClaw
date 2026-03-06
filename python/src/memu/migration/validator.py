from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import shutil
import sys
import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger("memu.migration.validator")

@dataclass
class MigrationIssue:
    severity: str
    message: str


@dataclass
class MigrationState:
    source: str
    created_at: str
    updated_at: str
    status: str
    dry_run: bool
    stage: str
    checksum_manifest: dict[str, str]
    report: dict[str, object]


@dataclass
class MigrationReport:
    source: str
    detected_v0_2_6: bool
    migration_targets: list[str]
    checksums: dict[str, str]
    estimated_time_seconds: int
    issues: list[MigrationIssue]


@dataclass
class SmokeTestResult:
    name: str
    passed: bool
    details: str


@dataclass
class PostMigrationValidationReport:
    source: str
    target: str
    checksum_passed: bool
    checksum_details: list[str]
    smoke_tests: list[SmokeTestResult]

    @property
    def passed(self) -> bool:
        return self.checksum_passed and all(test.passed for test in self.smoke_tests)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _estimate_time_seconds(target_count: int, total_bytes: int) -> int:
    base = target_count
    by_size = total_bytes // (1024 * 1024)
    return max(1, int(base + by_size))


def _relative_key(path: Path, source: Path) -> str:
    try:
        return str(path.relative_to(source))
    except Exception:
        return str(path)


def build_migration_report(source: str | Path) -> MigrationReport:
    from ..storage_layout import detect_legacy_v0_2_6_layout

    source_root = Path(source).expanduser().resolve()
    layout = detect_legacy_v0_2_6_layout(source_root)

    migration_targets: list[str] = []
    checksums: dict[str, str] = {}
    issues: list[MigrationIssue] = []
    total_bytes = 0

    db_path = Path(layout.legacy_db_path)
    if db_path.exists():
        migration_targets.append(str(db_path))
        checksums[_relative_key(db_path, source_root)] = _sha256_file(db_path)
        total_bytes += db_path.stat().st_size
    else:
        issues.append(MigrationIssue(severity="error", message=f"Missing legacy database: {db_path}"))

    global_last_sync = source_root / "data" / "last_sync_ts"
    if global_last_sync.exists():
        migration_targets.append(str(global_last_sync))
        checksums[_relative_key(global_last_sync, source_root)] = _sha256_file(global_last_sync)
        total_bytes += global_last_sync.stat().st_size
    else:
        issues.append(MigrationIssue(severity="warning", message="Missing global state file data/last_sync_ts"))

    pending_ingest = source_root / "data" / "pending_ingest.json"
    if pending_ingest.exists():
        migration_targets.append(str(pending_ingest))
        checksums[_relative_key(pending_ingest, source_root)] = _sha256_file(pending_ingest)
        total_bytes += pending_ingest.stat().st_size
    else:
        issues.append(MigrationIssue(severity="warning", message="Missing global queue file data/pending_ingest.json"))

    config_candidates = [
        source_root / "config.json",
        source_root / "data" / "config.json",
        source_root / "data" / "settings.json",
    ]
    config_found = False
    for cfg in config_candidates:
        if cfg.exists() and cfg.is_file():
            config_found = True
            migration_targets.append(str(cfg))
            checksums[_relative_key(cfg, source_root)] = _sha256_file(cfg)
            total_bytes += cfg.stat().st_size
    if not config_found:
        issues.append(MigrationIssue(severity="warning", message="No legacy config file found"))

    if layout.conversation_subdirectories:
        issues.append(
            MigrationIssue(
                severity="warning",
                message="Found per-agent conversation directories; source may already be partially migrated",
            )
        )

    for conv_file in layout.conversation_files:
        file_path = Path(conv_file)
        migration_targets.append(str(file_path))
        checksums[_relative_key(file_path, source_root)] = _sha256_file(file_path)
        total_bytes += file_path.stat().st_size

    if not layout.conversation_files:
        issues.append(
            MigrationIssue(
                severity="warning",
                message="No legacy conversation files found under data/conversations/*.json",
            )
        )

    report = MigrationReport(
        source=str(source_root),
        detected_v0_2_6=layout.detected,
        migration_targets=migration_targets,
        checksums=checksums,
        estimated_time_seconds=_estimate_time_seconds(len(migration_targets), total_bytes),
        issues=issues,
    )
    return report


def create_timestamped_backup(source: str | Path, backup_root: str | Path | None = None) -> Path:
    src = Path(source).expanduser().resolve()
    backup_parent = Path(backup_root).expanduser().resolve() if backup_root else src.parent
    _ = backup_parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_parent / f"{src.name}.backup_{timestamp}"
    _ = shutil.copy2(src, backup_path)
    return backup_path


def _state_file_path(source: str | Path, dry_run: bool = False) -> Path:
    src = Path(source).expanduser().resolve()
    if not dry_run:
        return src / "migration_state.json"
    key = hashlib.sha256(str(src).encode("utf-8")).hexdigest()[:12]
    return src.parent / ".memu_migration" / f"migration_state_{src.name}_{key}.json"


def load_migration_state(source: str | Path, dry_run: bool = False) -> MigrationState | None:
    state_path = _state_file_path(source, dry_run=dry_run)
    if not state_path.exists() and dry_run:
        state_path = _state_file_path(source, dry_run=False)
    if not state_path.exists():
        return None
    raw = cast(dict[str, object], json.loads(state_path.read_text(encoding="utf-8")))
    return MigrationState(
        source=str(raw.get("source", "")),
        created_at=str(raw.get("created_at", "")),
        updated_at=str(raw.get("updated_at", "")),
        status=str(raw.get("status", "unknown")),
        dry_run=bool(raw.get("dry_run", False)),
        stage=str(raw.get("stage", "unknown")),
        checksum_manifest=dict(cast(dict[str, str], raw.get("checksum_manifest", {}))),
        report=dict(cast(dict[str, object], raw.get("report", {}))),
    )


def save_migration_state(state: MigrationState, dry_run: bool = False) -> Path:
    state_path = _state_file_path(state.source, dry_run=dry_run)
    _ = state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    tmp_path = state_path.with_suffix(".tmp")
    _ = tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _ = tmp_path.replace(state_path)
    return state_path


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def _validation_state_path(target: str | Path) -> Path:
    root = Path(target).expanduser().resolve()
    return root / ".memu_migration" / "validation_state.json"


def _collect_target_db_checksums(target: str | Path) -> dict[str, str]:
    root = Path(target).expanduser().resolve()
    checksums: dict[str, str] = {}
    for db_path in sorted(root.glob("*/memu.db")):
        if not db_path.is_file():
            continue
        rel = str(db_path.relative_to(root))
        checksums[rel] = _sha256_file(db_path)
    return checksums


def _detect_memory_table_and_column(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    candidates: list[tuple[str, str]] = [
        ("memories", "content"),
        ("memu_memory_items", "summary"),
        ("memu_memory_items", "content"),
    ]
    for table, column in candidates:
        cursor = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
        if cursor.fetchone() is None:
            continue
        col_rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        columns = {str(row[1]) for row in col_rows if len(row) > 1}
        if column in columns:
            return table, column
    return None, None


def smoke_test_open_db(db_path: str | Path) -> tuple[bool, str]:
    path = Path(db_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        return False, f"Smoke test failed: Cannot open agent DB: {path}"
    try:
        conn = sqlite3.connect(path)
        try:
            _ = conn.execute("PRAGMA quick_check").fetchone()
            _ = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
            return True, f"Opened database: {path}"
        finally:
            conn.close()
    except Exception as exc:
        return False, f"Smoke test failed: Cannot open agent DB: {path} ({exc})"


def smoke_test_query_sessions(db_path: str | Path, expected_count: int) -> tuple[bool, str]:
    path = Path(db_path).expanduser().resolve()
    try:
        conn = sqlite3.connect(path)
        try:
            cursor = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions' LIMIT 1")
            has_sessions = cursor.fetchone() is not None
            if not has_sessions:
                actual = 0
            else:
                row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
                actual = int(row[0] if row else 0)
            if actual != expected_count:
                return (
                    False,
                    (
                        "Smoke test failed: Session count mismatch "
                        f"for {path} (expected={expected_count}, actual={actual})"
                    ),
                )
            return True, f"Session count matches source ({actual}) for {path}"
        finally:
            conn.close()
    except Exception as exc:
        return False, f"Smoke test failed: Session query error for {path} ({exc})"


def smoke_test_search_memories(db_path: str | Path) -> tuple[bool, str]:
    path = Path(db_path).expanduser().resolve()
    try:
        conn = sqlite3.connect(path)
        try:
            table, text_col = _detect_memory_table_and_column(conn)
            if not table or not text_col:
                return False, f"Smoke test failed: No searchable memory table in {path}"

            row = conn.execute(
                f"SELECT {text_col} FROM {table} WHERE {text_col} IS NOT NULL AND TRIM({text_col}) != '' LIMIT 1"
            ).fetchone()
            if row is None or not str(row[0]).strip():
                return False, f"Smoke test failed: No memory content available in {path}"

            sample_text = str(row[0]).strip()
            probe = sample_text[: min(16, len(sample_text))]
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {text_col} LIKE ?",
                (f"%{probe}%",),
            ).fetchone()
            hits = int(count_row[0] if count_row else 0)
            if hits <= 0:
                return False, f"Smoke test failed: Search returned no results in {path}"
            return True, f"Memory search returned {hits} result(s) in {path}"
        finally:
            conn.close()
    except Exception as exc:
        return False, f"Smoke test failed: Memory search query failed for {path} ({exc})"


def smoke_test_agent_isolation(agent_a_db: str | Path, agent_b_db: str | Path) -> tuple[bool, str]:
    path_a = Path(agent_a_db).expanduser().resolve()
    path_b = Path(agent_b_db).expanduser().resolve()
    try:
        conn_a = sqlite3.connect(path_a)
        conn_b = sqlite3.connect(path_b)
        try:
            table_a, col_a = _detect_memory_table_and_column(conn_a)
            table_b, col_b = _detect_memory_table_and_column(conn_b)
            if not table_a or not col_a or not table_b or not col_b:
                return (
                    False,
                    (
                        "Smoke test failed: Agent isolation check requires searchable memory tables "
                        f"({path_a}, {path_b})"
                    ),
                )

            cols_a = {str(row[1]) for row in conn_a.execute(f"PRAGMA table_info({table_a})").fetchall() if len(row) > 1}
            cols_b = {str(row[1]) for row in conn_b.execute(f"PRAGMA table_info({table_b})").fetchall() if len(row) > 1}

            scope_col_a = "agent_id" if "agent_id" in cols_a else ("agentName" if "agentName" in cols_a else "")
            scope_col_b = "agent_id" if "agent_id" in cols_b else ("agentName" if "agentName" in cols_b else "")
            if not scope_col_a or not scope_col_b:
                return False, "Smoke test failed: Agent isolation check requires agent scope columns"

            agent_a = path_a.parent.name
            agent_b = path_b.parent.name

            leak_into_a = conn_a.execute(
                f"SELECT COUNT(*) FROM {table_a} WHERE {scope_col_a} = ?",
                (agent_b,),
            ).fetchone()
            leak_into_b = conn_b.execute(
                f"SELECT COUNT(*) FROM {table_b} WHERE {scope_col_b} = ?",
                (agent_a,),
            ).fetchone()

            leak_a = int(leak_into_a[0] if leak_into_a else 0)
            leak_b = int(leak_into_b[0] if leak_into_b else 0)
            if leak_a > 0 or leak_b > 0:
                return (
                    False,
                    (
                        "Smoke test failed: Agent isolation violated "
                        f"(A sees B hits={leak_a}, B sees A hits={leak_b})"
                    ),
                )

            return True, f"Agent isolation verified between {agent_a} and {agent_b}"
        finally:
            conn_a.close()
            conn_b.close()
    except Exception as exc:
        return False, f"Smoke test failed: Agent isolation check error ({exc})"


def _source_session_count(source_root: Path) -> int:
    source_db = source_root / "memu.db"
    if not source_db.exists():
        return 0
    try:
        conn = sqlite3.connect(source_db)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions' LIMIT 1"
            ).fetchone()
            if row is None:
                return 0
            count_row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
            return int(count_row[0] if count_row else 0)
        finally:
            conn.close()
    except Exception:
        return 0


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        out[str(key)] = value
    return out


def _save_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _ = path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    _ = tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _ = tmp.replace(path)


def run_post_migration_validation(source: str | Path, target: str | Path) -> PostMigrationValidationReport:
    source_root = Path(source).expanduser().resolve()
    target_root = Path(target).expanduser().resolve()

    source_report = build_migration_report(source_root)
    source_checksums = source_report.checksums
    target_checksums = _collect_target_db_checksums(target_root)

    checksum_details: list[str] = []
    checksum_passed = True

    if not target_checksums:
        checksum_passed = False
        checksum_details.append(f"Checksum mismatch: no migrated databases found under {target_root}")

    state_path = _validation_state_path(target_root)
    if state_path.exists():
        state_payload = _load_json(state_path)
        baseline = dict(cast(dict[str, str], state_payload.get("target_checksums", {})))
        baseline_keys = set(baseline.keys())
        current_keys = set(target_checksums.keys())
        missing_keys = sorted(baseline_keys - current_keys)
        extra_keys = sorted(current_keys - baseline_keys)
        if missing_keys:
            checksum_passed = False
            checksum_details.append(f"Checksum mismatch: missing migrated DB files {missing_keys}")
        if extra_keys:
            checksum_passed = False
            checksum_details.append(f"Checksum mismatch: unexpected migrated DB files {extra_keys}")
        for rel, digest in baseline.items():
            current = target_checksums.get(rel)
            if current is None:
                continue
            if current != digest:
                checksum_passed = False
                checksum_details.append(
                    f"Checksum mismatch: {rel} expected={digest} actual={current}"
                )
        if checksum_passed:
            checksum_details.append("Checksums verified against baseline manifest")
    else:
        _save_json_atomic(
            state_path,
            {
                "source": str(source_root),
                "target": str(target_root),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "source_checksums": source_checksums,
                "target_checksums": target_checksums,
            },
        )
        checksum_details.append(
            f"Captured checksum baseline manifest at {state_path}"
        )

    smoke_results: list[SmokeTestResult] = []
    agent_db_paths = sorted(p for p in target_root.glob("*/memu.db") if p.parent.name != "shared")
    main_db = target_root / "main" / "memu.db"
    primary_db = main_db if main_db.exists() else (agent_db_paths[0] if agent_db_paths else main_db)

    ok_open, msg_open = smoke_test_open_db(primary_db)
    smoke_results.append(SmokeTestResult(name="open_db", passed=ok_open, details=msg_open))

    expected_sessions = _source_session_count(source_root)
    ok_sessions, msg_sessions = smoke_test_query_sessions(primary_db, expected_sessions)
    smoke_results.append(SmokeTestResult(name="query_sessions", passed=ok_sessions, details=msg_sessions))

    ok_search, msg_search = smoke_test_search_memories(primary_db)
    smoke_results.append(SmokeTestResult(name="search_memories", passed=ok_search, details=msg_search))

    if len(agent_db_paths) >= 2:
        ok_isolation, msg_isolation = smoke_test_agent_isolation(agent_db_paths[0], agent_db_paths[1])
    else:
        ok_isolation = False
        msg_isolation = "Smoke test failed: Agent isolation check requires at least two agent databases"
    smoke_results.append(SmokeTestResult(name="agent_isolation", passed=ok_isolation, details=msg_isolation))

    return PostMigrationValidationReport(
        source=str(source_root),
        target=str(target_root),
        checksum_passed=checksum_passed,
        checksum_details=checksum_details,
        smoke_tests=smoke_results,
    )


def render_post_migration_validation_report(report: PostMigrationValidationReport) -> None:
    print(f"Source: {report.source}")
    print(f"Target: {report.target}")
    print("Validation summary:")
    print(f"  - Checksum validation: {'PASSED' if report.checksum_passed else 'FAILED'}")
    passed_smoke = sum(1 for test in report.smoke_tests if test.passed)
    print(f"  - Smoke tests: {passed_smoke}/{len(report.smoke_tests)} passed")

    print("Detailed log:")
    for line in report.checksum_details:
        print(f"  - {line}")
    for test in report.smoke_tests:
        status = "PASS" if test.passed else "FAIL"
        print(f"  - [{status}] {test.name}: {test.details}")

    if report.passed:
        print("All validations passed")
    else:
        print("Validation failed")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post-migration validator for MemU v0.2.6 -> v0.3.1")
    _ = parser.add_argument("--source", required=True, help="Legacy source root used for migration")
    _ = parser.add_argument("--target", required=True, help="Migrated target root to validate")
    _ = parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(bool(args.verbose))
    source = cast(str, args.source)
    target = cast(str, args.target)

    report = run_post_migration_validation(source=source, target=target)
    render_post_migration_validation_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(run())

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("migrate_storage_layout")


@dataclass(frozen=True)
class MigrationPlan:
    source_data_dir: Path
    target_data_dir: Path
    source_memory_root: Path
    target_memory_root: Path
    default_agent: str


@dataclass
class MigrationResult:
    migrated: bool
    marker_path: Path
    backup_path: Path | None
    copied: list[str]
    skipped: list[str]


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate MemU plugin storage layout into ~/.openclaw/memUdata structure",
    )
    parser.add_argument(
        "--source-data-dir",
        default=os.getenv("MEMU_DATA_DIR") or os.path.expanduser("~/.openclaw/memUdata"),
        help="Legacy/current MEMU_DATA_DIR source (default: $MEMU_DATA_DIR or ~/.openclaw/memUdata)",
    )
    parser.add_argument(
        "--target-data-dir",
        default=os.path.expanduser("~/.openclaw/memUdata"),
        help="Unified MEMU_DATA_DIR target (default: ~/.openclaw/memUdata)",
    )
    parser.add_argument(
        "--source-memory-root",
        default=os.path.expanduser("~/.openclaw/memory"),
        help="Legacy memory root source (default: ~/.openclaw/memory)",
    )
    parser.add_argument(
        "--target-memory-root",
        default=os.getenv("MEMU_MEMORY_ROOT") or os.path.expanduser("~/.openclaw/memUdata/memory"),
        help="Unified memory root target (default: $MEMU_MEMORY_ROOT or ~/.openclaw/memUdata/memory)",
    )
    parser.add_argument(
        "--default-agent",
        default=(os.getenv("MEMU_AGENT_NAME") or "main").strip() or "main",
        help="Default agent for legacy flat conversation files (default: $MEMU_AGENT_NAME or main)",
    )
    parser.add_argument("--backup", action="store_true", help="Create verified backup before migration")
    parser.add_argument("--force", action="store_true", help="Ignore existing marker and run again")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not modify files")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    return parser.parse_args()


def _build_plan(args: argparse.Namespace) -> MigrationPlan:
    return MigrationPlan(
        source_data_dir=Path(str(args.source_data_dir)).expanduser().resolve(),
        target_data_dir=Path(str(args.target_data_dir)).expanduser().resolve(),
        source_memory_root=Path(str(args.source_memory_root)).expanduser().resolve(),
        target_memory_root=Path(str(args.target_memory_root)).expanduser().resolve(),
        default_agent=str(args.default_agent).strip() or "main",
    )


def _marker_path(plan: MigrationPlan) -> Path:
    return plan.target_data_dir / "state" / "layout_migration.json"


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup_source_data(source_root: Path) -> Path:
    backup_root = source_root.parent / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_dir = backup_root / f"storage-layout-{source_root.name}"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(source_root, backup_dir)

    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root)
        copied = backup_dir / rel
        if not copied.exists() or _compute_sha256(path) != _compute_sha256(copied):
            raise RuntimeError(f"backup verification failed for {path}")
    return backup_dir


def _copy_file(src: Path, dst: Path, copied: list[str], skipped: list[str], *, dry_run: bool) -> None:
    rel = f"{src} -> {dst}"
    if dst.exists():
        skipped.append(f"target_exists:{rel}")
        return
    copied.append(rel)
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _move_file(src: Path, dst: Path, copied: list[str], skipped: list[str], *, dry_run: bool) -> None:
    rel = f"{src} -> {dst}"
    if dst.exists():
        skipped.append(f"target_exists:{rel}")
        return
    copied.append(rel)
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _migrate_memory_root(plan: MigrationPlan, copied: list[str], skipped: list[str], *, dry_run: bool) -> None:
    if not plan.source_memory_root.exists() or not plan.source_memory_root.is_dir():
        skipped.append(f"missing_source_memory_root:{plan.source_memory_root}")
        return

    for db_file in sorted(plan.source_memory_root.glob("*/memu.db")):
        if not db_file.is_file():
            continue
        agent_dir = db_file.parent.name
        target_db = plan.target_memory_root / agent_dir / "memu.db"
        _copy_file(db_file, target_db, copied, skipped, dry_run=dry_run)


def _migrate_flat_conversations(plan: MigrationPlan, copied: list[str], skipped: list[str], *, dry_run: bool) -> None:
    conv_root = plan.source_data_dir / "conversations"
    if not conv_root.exists() or not conv_root.is_dir():
        skipped.append(f"missing_conversations_root:{conv_root}")
        return

    target_agent_dir = plan.target_data_dir / "conversations" / plan.default_agent
    flat_files = [
        p
        for p in conv_root.glob("*")
        if p.is_file()
        and (
            (p.suffix.lower() == ".json" and ".part" in p.name)
            or p.name.endswith(".tmp.json")
            or (p.name.endswith(".json") and p.name != "state.json")
        )
    ]

    for src in sorted(flat_files):
        dst = target_agent_dir / src.name
        _move_file(src, dst, copied, skipped, dry_run=dry_run)


def _migrate_state_files(plan: MigrationPlan, copied: list[str], skipped: list[str], *, dry_run: bool) -> None:
    source_root = plan.source_data_dir
    target_root = plan.target_data_dir
    state_root = target_root / "state"
    sync_root = state_root / "sync" / plan.default_agent
    convert_root = state_root / "convert"

    legacy_map: list[tuple[Path, Path]] = [
        (source_root / "last_sync_ts", sync_root / "last_sync_ts"),
        (source_root / "pending_ingest.json", sync_root / "pending_ingest.json"),
        (source_root / "pending_backoff.json", sync_root / "pending_backoff.json"),
        (source_root / "empty_sync_log.marker", sync_root / "empty_sync_log.marker"),
        (source_root / "sync.log", state_root / "sync.log"),
        (source_root / "docs_full_scan.marker", state_root / "docs_full_scan.marker"),
        (source_root / "watch_sync.pid", state_root / "watch_sync.pid"),
        (source_root / "conversations" / "state.json", convert_root / f"{plan.default_agent}.json"),
    ]

    for src, dst in legacy_map:
        if not src.exists() or not src.is_file():
            skipped.append(f"missing_legacy_state:{src}")
            continue
        _move_file(src, dst, copied, skipped, dry_run=dry_run)


def _copy_tree_files(
    src_root: Path,
    dst_root: Path,
    copied: list[str],
    skipped: list[str],
    *,
    dry_run: bool,
) -> None:
    if not src_root.exists() or not src_root.is_dir():
        skipped.append(f"missing_source_tree:{src_root}")
        return

    for path in sorted(src_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src_root)
        dst = dst_root / rel
        _copy_file(path, dst, copied, skipped, dry_run=dry_run)


def _migrate_resources(plan: MigrationPlan, copied: list[str], skipped: list[str], *, dry_run: bool) -> None:
    target_resources = plan.target_data_dir / "resources" / "shared"

    repo_root = Path(__file__).resolve().parents[2]
    default_candidates = [
        plan.source_data_dir / "resources" / "shared",
        plan.source_data_dir / "resources",
        repo_root / "python" / "data" / "resources",
        Path.cwd() / "data" / "resources",
    ]

    seen: set[str] = set()
    for src in default_candidates:
        key = str(src.resolve()) if src.exists() else str(src)
        if key in seen:
            continue
        seen.add(key)
        _copy_tree_files(src, target_resources, copied, skipped, dry_run=dry_run)


def _write_marker(
    plan: MigrationPlan,
    *,
    backup_path: Path | None,
    copied: list[str],
    skipped: list[str],
    dry_run: bool,
) -> Path:
    marker = _marker_path(plan)
    payload: dict[str, Any] = {
        "version": 1,
        "source_data_dir": str(plan.source_data_dir),
        "target_data_dir": str(plan.target_data_dir),
        "source_memory_root": str(plan.source_memory_root),
        "target_memory_root": str(plan.target_memory_root),
        "default_agent": plan.default_agent,
        "backup_path": str(backup_path) if backup_path else None,
        "copied_count": len(copied),
        "skipped_count": len(skipped),
        "copied": copied,
        "skipped": skipped,
    }
    if dry_run:
        return marker
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return marker


def migrate_storage_layout(
    *,
    plan: MigrationPlan,
    backup: bool,
    force: bool,
    dry_run: bool,
) -> MigrationResult:
    copied: list[str] = []
    skipped: list[str] = []

    marker = _marker_path(plan)
    if marker.exists() and not force:
        return MigrationResult(
            migrated=False,
            marker_path=marker,
            backup_path=None,
            copied=[],
            skipped=[f"marker_exists:{marker}"],
        )

    backup_path: Path | None = None
    if backup and not dry_run:
        backup_path = _backup_source_data(plan.source_data_dir)

    if not dry_run:
        plan.target_data_dir.mkdir(parents=True, exist_ok=True)
        plan.target_memory_root.mkdir(parents=True, exist_ok=True)

    _migrate_memory_root(plan, copied, skipped, dry_run=dry_run)
    _migrate_flat_conversations(plan, copied, skipped, dry_run=dry_run)
    _migrate_state_files(plan, copied, skipped, dry_run=dry_run)
    _migrate_resources(plan, copied, skipped, dry_run=dry_run)

    marker_path = _write_marker(
        plan,
        backup_path=backup_path,
        copied=copied,
        skipped=skipped,
        dry_run=dry_run,
    )

    return MigrationResult(
        migrated=bool(copied) or dry_run,
        marker_path=marker_path,
        backup_path=backup_path,
        copied=copied,
        skipped=skipped,
    )


def main() -> int:
    args = parse_args()
    _configure_logging(bool(args.verbose))

    plan = _build_plan(args)
    try:
        result = migrate_storage_layout(
            plan=plan,
            backup=bool(args.backup),
            force=bool(args.force),
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:
        logger.exception("storage layout migration failed: %s", exc)
        return 1

    print(f"migrated: {result.migrated}")
    print(f"marker: {result.marker_path}")
    print(f"backup: {result.backup_path}")
    print(f"copied: {len(result.copied)}")
    print(f"skipped: {len(result.skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

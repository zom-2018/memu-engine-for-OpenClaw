from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

docs_ingest = importlib.import_module("docs_ingest")


class _FakeManager:
    def __init__(self, seed_docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = [dict(row) for row in (seed_docs or [])]
        self.chunks = [
            {"id": f"chunk-{row['id']}", "document_id": row["id"]}
            for row in self.docs
            if row.get("id")
        ]
        self.ingest_calls: list[tuple[str, str]] = []

    def get_shared_db(self):
        return self

    async def ingest_document(
        self,
        *,
        file_path: str,
        agent_id: str,
        user_id: str,
        embed_client: object,
    ):
        doc_id = f"doc-{len(self.docs) + 1}"
        self.ingest_calls.append((file_path, agent_id))
        self.docs.append(
            {
                "id": doc_id,
                "filename": os.path.basename(file_path),
                "owner_agent_id": agent_id,
                "content_hash": None,
                "mtime": None,
                "created_at": len(self.docs) + 100,
            }
        )
        self.chunks.append({"id": f"chunk-{doc_id}", "document_id": doc_id})
        return SimpleNamespace(document_id=doc_id)

    def close(self) -> None:
        return None


class _FakeMemoryService:
    def __init__(self, *args, **kwargs) -> None:
        return None

    def _get_llm_client(self, _name: str) -> object:
        return object()


class _FailingManager(_FakeManager):
    async def ingest_document(
        self,
        *,
        file_path: str,
        agent_id: str,
        user_id: str,
        embed_client: object,
    ):
        raise RuntimeError("ingest boom")


def _mock_fetch_with_locked_retry(db, sql: str, params=(), **kwargs):
    if "SELECT id, content_hash, mtime FROM documents" in sql:
        filename, owner_agent_id = params
        rows = [
            (row["id"], row.get("content_hash"), row.get("mtime"))
            for row in db.docs
            if row["filename"] == filename and row["owner_agent_id"] == owner_agent_id
        ]
        rows.sort(
            key=lambda x: next((d["created_at"] for d in db.docs if d["id"] == x[0]), 0),
            reverse=True,
        )
        return rows
    raise AssertionError(f"Unexpected fetch SQL: {sql}")


def _mock_execute_with_locked_retry(db, sql: str, params=(), **kwargs):
    if sql.startswith("UPDATE documents SET content_hash = ?, mtime = ? WHERE id = ?"):
        content_hash, mtime, document_id = params
        for row in db.docs:
            if row["id"] == document_id:
                row["content_hash"] = content_hash
                row["mtime"] = mtime
        return

    if sql.startswith("DELETE FROM document_chunks WHERE document_id = ?"):
        document_id = params[0]
        db.chunks = [row for row in db.chunks if row["document_id"] != document_id]
        return

    if sql.startswith("DELETE FROM documents WHERE id = ?"):
        document_id = params[0]
        db.docs = [row for row in db.docs if row["id"] != document_id]
        return

    raise AssertionError(f"Unexpected execute SQL: {sql}")


@pytest.fixture
def patch_db_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docs_ingest, "fetch_with_locked_retry", _mock_fetch_with_locked_retry)
    monkeypatch.setattr(
        docs_ingest,
        "execute_with_locked_retry",
        _mock_execute_with_locked_retry,
    )


async def _run_update_cycle(
    manager: _FakeManager,
    *,
    file_path: Path,
    owner_agent_id: str,
    user_id: str = "u1",
) -> str:
    content_hash = docs_ingest._calculate_content_hash(str(file_path))
    mtime = float(os.path.getmtime(file_path))
    exists, legacy_doc_ids, stale_doc_ids = docs_ingest._shared_document_exists(
        manager,
        filename=file_path.name,
        owner_agent_id=owner_agent_id,
        content_hash=content_hash,
        mtime=mtime,
    )

    if legacy_doc_ids:
        docs_ingest._backfill_document_metadata(
            manager,
            document_ids=legacy_doc_ids,
            content_hash=content_hash,
            mtime=mtime,
        )
        return "legacy-backfilled"

    if exists:
        return "skipped"

    if stale_doc_ids:
        docs_ingest._delete_documents_and_chunks(manager, document_ids=stale_doc_ids)

    result = await manager.ingest_document(
        file_path=str(file_path),
        agent_id=owner_agent_id,
        user_id=user_id,
        embed_client=object(),
    )
    docs_ingest._persist_document_metadata(
        manager,
        document_id=result.document_id,
        content_hash=content_hash,
        mtime=mtime,
    )
    return "ingested"


def _doc_row(manager: _FakeManager, filename: str, owner_agent_id: str) -> dict[str, object]:
    rows = [
        r
        for r in manager.docs
        if r["filename"] == filename and r["owner_agent_id"] == owner_agent_id
    ]
    assert len(rows) == 1
    return rows[0]


def test_content_changed_reingests_and_replaces_old_doc(
    patch_db_calls: None,
    tmp_path: Path,
) -> None:
    doc_path = tmp_path / "doc.md"
    doc_path.write_text("new content", encoding="utf-8")

    manager = _FakeManager(
        [
            {
                "id": "old-1",
                "filename": "doc.md",
                "owner_agent_id": "main",
                "content_hash": "old-hash",
                "mtime": 1.0,
                "created_at": 1,
            }
        ]
    )

    status = asyncio.run(_run_update_cycle(manager, file_path=doc_path, owner_agent_id="main"))
    assert status == "ingested"
    assert manager.ingest_calls == [(str(doc_path), "main")]
    assert all(r["id"] != "old-1" for r in manager.docs)
    row = _doc_row(manager, "doc.md", "main")
    assert row["content_hash"] == hashlib.sha256(doc_path.read_bytes()).hexdigest()


def test_mtime_changed_with_same_content_triggers_reingest(
    patch_db_calls: None,
    tmp_path: Path,
) -> None:
    doc_path = tmp_path / "touched.md"
    doc_path.write_text("stable", encoding="utf-8")
    digest = hashlib.sha256(doc_path.read_bytes()).hexdigest()
    first_mtime = float(os.path.getmtime(doc_path))

    manager = _FakeManager(
        [
            {
                "id": "old-touch",
                "filename": "touched.md",
                "owner_agent_id": "main",
                "content_hash": digest,
                "mtime": first_mtime,
                "created_at": 1,
            }
        ]
    )

    os.utime(doc_path, (first_mtime + 1.0, first_mtime + 1.0))
    status = asyncio.run(_run_update_cycle(manager, file_path=doc_path, owner_agent_id="main"))
    assert status == "ingested"
    assert manager.ingest_calls == [(str(doc_path), "main")]


def test_unchanged_document_skips_reingestion(
    patch_db_calls: None,
    tmp_path: Path,
) -> None:
    doc_path = tmp_path / "same.md"
    doc_path.write_text("same content", encoding="utf-8")
    digest = hashlib.sha256(doc_path.read_bytes()).hexdigest()
    mtime = float(os.path.getmtime(doc_path))
    manager = _FakeManager(
        [
            {
                "id": "same-1",
                "filename": "same.md",
                "owner_agent_id": "main",
                "content_hash": digest,
                "mtime": mtime,
                "created_at": 1,
            }
        ]
    )

    status = asyncio.run(_run_update_cycle(manager, file_path=doc_path, owner_agent_id="main"))
    assert status == "skipped"
    assert manager.ingest_calls == []


def test_multiple_updates_in_sequence(
    patch_db_calls: None,
    tmp_path: Path,
) -> None:
    doc_path = tmp_path / "seq.md"
    doc_path.write_text("v1", encoding="utf-8")
    manager = _FakeManager()

    status_1 = asyncio.run(_run_update_cycle(manager, file_path=doc_path, owner_agent_id="main"))
    doc_path.write_text("v2", encoding="utf-8")
    status_2 = asyncio.run(_run_update_cycle(manager, file_path=doc_path, owner_agent_id="main"))
    second_mtime = float(os.path.getmtime(doc_path))
    os.utime(doc_path, (second_mtime + 2.0, second_mtime + 2.0))
    status_3 = asyncio.run(_run_update_cycle(manager, file_path=doc_path, owner_agent_id="main"))

    assert [status_1, status_2, status_3] == ["ingested", "ingested", "ingested"]
    assert len(manager.ingest_calls) == 3
    assert len([r for r in manager.docs if r["filename"] == "seq.md" and r["owner_agent_id"] == "main"]) == 1


def test_concurrent_updates_from_different_agents(
    patch_db_calls: None,
    tmp_path: Path,
) -> None:
    doc_main = tmp_path / "shared-main.md"
    doc_research = tmp_path / "shared-research.md"
    doc_main.write_text("main v2", encoding="utf-8")
    doc_research.write_text("research v2", encoding="utf-8")

    manager = _FakeManager(
        [
            {
                "id": "old-main",
                "filename": "shared-main.md",
                "owner_agent_id": "main",
                "content_hash": "x",
                "mtime": 1.0,
                "created_at": 1,
            },
            {
                "id": "old-research",
                "filename": "shared-research.md",
                "owner_agent_id": "research",
                "content_hash": "y",
                "mtime": 1.0,
                "created_at": 1,
            },
        ]
    )

    async def _run_both() -> tuple[str, str]:
        return await asyncio.gather(
            _run_update_cycle(manager, file_path=doc_main, owner_agent_id="main"),
            _run_update_cycle(manager, file_path=doc_research, owner_agent_id="research"),
        )

    status_main, status_research = asyncio.run(_run_both())
    assert (status_main, status_research) == ("ingested", "ingested")
    assert sorted(agent for _, agent in manager.ingest_calls) == ["main", "research"]


def test_edge_cases_empty_large_binary_and_symlink(
    patch_db_calls: None,
    tmp_path: Path,
) -> None:
    manager = _FakeManager()

    empty_file = tmp_path / "empty.md"
    empty_file.write_bytes(b"")
    assert asyncio.run(_run_update_cycle(manager, file_path=empty_file, owner_agent_id="main")) == "ingested"

    large_file = tmp_path / "large.md"
    large_file.write_bytes((b"0123456789abcdef" * 1024) * 700)
    assert large_file.stat().st_size > 10 * 1024 * 1024
    assert asyncio.run(_run_update_cycle(manager, file_path=large_file, owner_agent_id="main")) == "ingested"

    binary_file = tmp_path / "binary.md"
    binary_file.write_bytes(b"\x00\x01\x02\x03\x00\x04")

    original_ingest = manager.ingest_document

    async def _reject_binary(**kwargs):
        payload = Path(kwargs["file_path"]).read_bytes()
        if b"\x00" in payload:
            raise ValueError("binary file rejected")
        return await original_ingest(**kwargs)

    manager.ingest_document = _reject_binary  # type: ignore[assignment]
    with pytest.raises(ValueError, match="binary file rejected"):
        asyncio.run(_run_update_cycle(manager, file_path=binary_file, owner_agent_id="main"))

    target = tmp_path / "target.md"
    target.write_text("symlink source", encoding="utf-8")
    symlink_path = tmp_path / "link.md"
    symlink_path.symlink_to(target)

    files = docs_ingest._collect_markdown_files(
        extra_paths=[str(tmp_path)],
        changed_path=str(symlink_path),
    )
    assert files == [str(symlink_path.absolute())]


def test_reingestion_processes_only_changed_docs(
    patch_db_calls: None,
    tmp_path: Path,
) -> None:
    manager = _FakeManager()
    docs: list[Path] = []
    for idx in range(10):
        p = tmp_path / f"doc-{idx}.md"
        p.write_text(f"content-{idx}", encoding="utf-8")
        docs.append(p)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        manager.docs.append(
            {
                "id": f"seed-{idx}",
                "filename": p.name,
                "owner_agent_id": "main",
                "content_hash": digest,
                "mtime": float(os.path.getmtime(p)),
                "created_at": idx,
            }
        )

    docs[4].write_text("content-4-updated", encoding="utf-8")

    statuses = [
        asyncio.run(_run_update_cycle(manager, file_path=p, owner_agent_id="main"))
        for p in docs
    ]
    assert statuses.count("ingested") == 1
    assert statuses.count("skipped") == 9
    assert len(manager.ingest_calls) == 1
    assert manager.ingest_calls[0][0].endswith("doc-4.md")


def test_update_detection_performance(patch_db_calls: None) -> None:
    rows = [
        {
            "id": f"doc-{i}",
            "filename": "perf.md",
            "owner_agent_id": "main",
            "content_hash": f"h-{i}",
            "mtime": float(i),
            "created_at": i,
        }
        for i in range(1000)
    ]
    manager = _FakeManager(rows)

    start = time.perf_counter()
    exists, legacy_doc_ids, stale_doc_ids = docs_ingest._shared_document_exists(
        manager,
        filename="perf.md",
        owner_agent_id="main",
        content_hash="new-hash",
        mtime=99999.0,
    )
    elapsed = time.perf_counter() - start

    assert exists is False
    assert legacy_doc_ids == []
    assert len(stale_doc_ids) == 1000
    assert elapsed < 0.1, f"Update detection took {elapsed:.6f}s for 1000 docs"


def test_helper_branches_for_env_paths_and_mtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TEST_ENV", "  value ")
    assert docs_ingest._env("TEST_ENV", "d") == "  value "
    monkeypatch.setenv("TEST_ENV", "   ")
    assert docs_ingest._env("TEST_ENV", "d") == "d"

    md_root = tmp_path / "docs"
    md_root.mkdir()
    md_file = md_root / "a.md"
    txt_file = md_root / "a.txt"
    md_file.write_text("a", encoding="utf-8")
    txt_file.write_text("b", encoding="utf-8")
    assert docs_ingest._collect_markdown_files(extra_paths=[str(md_root)], changed_path=None) == [
        str(md_file.resolve())
    ]
    assert docs_ingest._collect_markdown_files(
        extra_paths=[str(md_root)], changed_path=str(tmp_path / "outside.md")
    ) == []

    assert docs_ingest._is_under_prefix(str(md_file), str(md_root)) is True
    assert docs_ingest._is_under_prefix(str(md_file), str(tmp_path / "other")) is False
    assert docs_ingest._is_same_mtime(1.0, 1.0 + 1e-7) is True
    assert docs_ingest._is_same_mtime(None, 1.0) is False
    assert docs_ingest._is_same_mtime("bad", 1.0) is False

    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path / "data"))
    dsn = docs_ingest.get_db_dsn()
    assert dsn.endswith("/memu.db")
    marker = docs_ingest._full_scan_marker_path()
    assert marker is not None
    assert marker.endswith("docs_full_scan.marker")

    monkeypatch.setenv("MEMU_EXTRA_PATHS", "[\"x\", 1, \"y\"]")
    assert docs_ingest.get_extra_paths() == ["x", "y"]
    monkeypatch.setenv("MEMU_EXTRA_PATHS", "not-json")
    assert docs_ingest.get_extra_paths() == []

    cfg_default = docs_ingest._build_language_aware_memorize_config("xx")
    cfg_zh = docs_ingest._build_language_aware_memorize_config("zh")
    assert cfg_default.memory_types == ["profile", "event", "knowledge", "behavior", "skill", "tool"]
    assert cfg_zh.memory_type_prompts is not None


def test_lock_and_log_helpers_cover_edge_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MEMU_DATA_DIR", str(tmp_path))
    docs_ingest._log("hello")
    assert (tmp_path / "sync.log").exists()

    real_open = open

    def _boom_open(*args, **kwargs):
        raise OSError("no write")

    monkeypatch.setattr("builtins.open", _boom_open)
    docs_ingest._log("ignore")
    monkeypatch.setattr("builtins.open", real_open)

    lock = tmp_path / "sync.lock"
    fd = docs_ingest._try_acquire_lock(str(lock))
    assert fd is not None
    assert docs_ingest._try_acquire_lock(str(lock)) is None
    docs_ingest._release_lock(str(lock), fd)
    assert not lock.exists()

    lock.write_text("123", encoding="utf-8")
    monkeypatch.setattr(docs_ingest.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    fd2 = docs_ingest._try_acquire_lock(str(lock))
    assert fd2 is not None
    docs_ingest._release_lock(str(lock), fd2)


def test_main_update_flow_with_legacy_unchanged_and_reingest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    doc_path = tmp_path / "doc.md"
    doc_path.write_text("stable", encoding="utf-8")
    digest = hashlib.sha256(doc_path.read_bytes()).hexdigest()
    mtime = float(os.path.getmtime(doc_path))

    manager = _FakeManager(
        [
            {
                "id": "legacy-1",
                "filename": "doc.md",
                "owner_agent_id": "main",
                "content_hash": None,
                "mtime": None,
                "created_at": 1,
            }
        ]
    )
    monkeypatch.setattr(docs_ingest, "MemoryService", _FakeMemoryService)
    monkeypatch.setattr(docs_ingest, "HybridDatabaseManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(docs_ingest, "fetch_with_locked_retry", _mock_fetch_with_locked_retry)
    monkeypatch.setattr(docs_ingest, "execute_with_locked_retry", _mock_execute_with_locked_retry)
    monkeypatch.setattr(docs_ingest, "migrate_legacy_single_db_to_agent_db", lambda default_agent="main": None)
    monkeypatch.setattr(docs_ingest, "_try_acquire_lock", lambda _path: object())
    monkeypatch.setattr(docs_ingest, "_release_lock", lambda _path, _fd: None)
    monkeypatch.setattr(docs_ingest, "_log", lambda _msg: None)
    monkeypatch.setenv("MEMU_EXTRA_PATHS", f"[\"{tmp_path}\"]")
    monkeypatch.setenv("MEMU_CHANGED_PATH", str(doc_path))
    monkeypatch.setenv("MEMU_DOC_OWNER_AGENT", "main")

    asyncio.run(docs_ingest.main())
    assert manager.ingest_calls == []
    row = _doc_row(manager, "doc.md", "main")
    assert row["content_hash"] == digest
    row_mtime = row["mtime"]
    assert isinstance(row_mtime, int | float)
    assert abs(float(row_mtime) - mtime) < 1e-6

    manager.docs = [
        {
            "id": "same-1",
            "filename": "doc.md",
            "owner_agent_id": "main",
            "content_hash": digest,
            "mtime": mtime,
            "created_at": 1,
        }
    ]
    asyncio.run(docs_ingest.main())
    assert manager.ingest_calls == []

    manager.docs = [
        {
            "id": "stale-1",
            "filename": "doc.md",
            "owner_agent_id": "main",
            "content_hash": "old-h",
            "mtime": 1.0,
            "created_at": 1,
        }
    ]
    doc_path.write_text("changed", encoding="utf-8")
    asyncio.run(docs_ingest.main())
    assert len(manager.ingest_calls) == 1


def test_main_short_circuit_failure_and_full_scan_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    logs: list[str] = []
    monkeypatch.setattr(docs_ingest, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(docs_ingest, "_try_acquire_lock", lambda _path: None)
    asyncio.run(docs_ingest.main())
    assert any("already running" in line for line in logs)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "doc.md").write_text("x", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manager = _FailingManager()
    monkeypatch.setattr(docs_ingest, "_try_acquire_lock", lambda _path: object())
    monkeypatch.setattr(docs_ingest, "_release_lock", lambda _path, _fd: None)
    monkeypatch.setattr(docs_ingest, "MemoryService", _FakeMemoryService)
    monkeypatch.setattr(docs_ingest, "HybridDatabaseManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(docs_ingest, "fetch_with_locked_retry", _mock_fetch_with_locked_retry)
    monkeypatch.setattr(docs_ingest, "execute_with_locked_retry", _mock_execute_with_locked_retry)
    monkeypatch.setattr(docs_ingest, "migrate_legacy_single_db_to_agent_db", lambda default_agent="main": None)
    monkeypatch.delenv("MEMU_CHANGED_PATH", raising=False)
    monkeypatch.setenv("MEMU_EXTRA_PATHS", f"[\"{docs_dir}\"]")
    monkeypatch.setenv("MEMU_DOC_OWNER_AGENT", "main")
    monkeypatch.setenv("MEMU_DATA_DIR", str(data_dir))

    asyncio.run(docs_ingest.main())
    assert any("failed:" in line for line in logs)
    assert (data_dir / "docs_full_scan.marker").exists()

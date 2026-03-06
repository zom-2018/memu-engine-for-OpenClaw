from __future__ import annotations

from pathlib import Path

from tests.conftest import cleanup_multi_agent_fixture, load_multi_agent_fixture, summarize_fixture_counts


def test_load_multi_agent_fixture(tmp_path: Path) -> None:
    loaded = load_multi_agent_fixture(tmp_path)
    root = loaded["root"]

    assert root.exists()
    assert (root / "main" / "memu.db").exists()
    assert (root / "research" / "memu.db").exists()
    assert (root / "coding" / "memu.db").exists()
    assert (root / "shared" / "memu.db").exists()

    summary = summarize_fixture_counts(root)
    assert summary["total_sessions"] == 30
    assert summary["total_memories"] == 30
    assert summary["shared_documents"] >= 5


def test_fixture_cleanup(tmp_path: Path) -> None:
    loaded = load_multi_agent_fixture(tmp_path)
    fixture_root = loaded["root"]
    assert fixture_root.exists()

    cleanup_multi_agent_fixture(fixture_root)
    assert not fixture_root.exists()


def test_fixture_isolation(tmp_path: Path) -> None:
    first = load_multi_agent_fixture(tmp_path / "case_a")
    second = load_multi_agent_fixture(tmp_path / "case_b")

    main_a = first["root"] / "main" / "sessions" / "session1.jsonl"
    original = main_a.read_text(encoding="utf-8")
    _ = main_a.write_text(
        original + '{"session_id":"mutated-a","role":"user","type":"chat","content":"mutation"}\n',
        encoding="utf-8",
    )

    main_b = second["root"] / "main" / "sessions" / "session1.jsonl"
    untouched = main_b.read_text(encoding="utf-8")
    assert "mutated-a" not in untouched

    cleanup_multi_agent_fixture(first["root"])
    cleanup_multi_agent_fixture(second["root"])

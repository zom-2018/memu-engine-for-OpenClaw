#!/usr/bin/env python3
"""Deterministic tests for search orchestration and output shaping."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


PYTHON_DIR = Path(__file__).resolve().parent
SRC_DIR = PYTHON_DIR / "src"
SCRIPTS_DIR = PYTHON_DIR / "scripts"
for candidate in (str(PYTHON_DIR), str(SRC_DIR), str(SCRIPTS_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


SEARCH_MODULE_PATH = SCRIPTS_DIR / "search.py"
SEARCH_SPEC = importlib.util.spec_from_file_location("memu_search_script", SEARCH_MODULE_PATH)
assert SEARCH_SPEC and SEARCH_SPEC.loader
search_module = importlib.util.module_from_spec(SEARCH_SPEC)
SEARCH_SPEC.loader.exec_module(search_module)


class SearchLogicTests(unittest.TestCase):
    def test_resolve_search_targets_filters_disabled_and_dedupes(self) -> None:
        settings = {
            "main": {
                "memoryEnabled": True,
                "searchEnabled": True,
                "searchableStores": ["self", "shared", "research", "research"],
            },
            "research": {
                "memoryEnabled": False,
                "searchEnabled": True,
                "searchableStores": ["self"],
            },
        }

        with mock.patch.object(search_module, "parse_agent_settings_from_env", return_value=settings):
            targets = search_module._resolve_search_targets(
                requesting_agent="main",
                requested_stores=["self", "shared", "research", "shared"],
            )

        self.assertEqual(targets, ["main", "shared"])

    def test_search_is_fail_soft_when_one_store_raises(self) -> None:
        async def fake_search_agent_store(**kwargs):
            if kwargs["agent_name"] == "broken":
                raise RuntimeError("broken store")
            return (
                [{"id": "cat-main", "summary": "Main category", "score": 0.9}],
                [{"id": "item-main", "summary": "Main item", "score": 0.8, "resource_id": "res-main"}],
                {"res-main": "/tmp/main-memory.md"},
            )

        with (
            mock.patch.object(search_module, "migrate_legacy_single_db_to_agent_db"),
            mock.patch.object(search_module, "_build_llm_configs", return_value=(object(), object())),
            mock.patch.object(search_module, "_resolve_search_targets", return_value=["main", "broken"]),
            mock.patch.object(search_module, "_search_agent_store", side_effect=fake_search_agent_store),
            mock.patch.dict(
                os.environ,
                {
                    "MEMU_WORKSPACE_DIR": "/tmp",
                    "MEMU_EXTRA_PATHS": "[]",
                    "MEMU_DEBUG_TIMING": "true",
                },
                clear=False,
            ),
        ):
            result = asyncio.run(
                search_module.search(
                    query_text="remember main state",
                    requesting_agent="main",
                    search_stores=["self", "broken"],
                    max_results=5,
                    min_score=0.0,
                    user_id="default",
                    mode="fast",
                    category_quota=None,
                    item_quota=None,
                    queries=None,
                )
            )

        self.assertTrue(result["results"])
        self.assertEqual(result["results"][0]["agentName"], "main")
        self.assertEqual(len(result["_timing"]["store_errors"]), 1)
        self.assertEqual(result["_timing"]["store_errors"][0]["store"], "broken")

    def test_search_dedupes_identical_snippets_across_stores(self) -> None:
        async def fake_search_agent_store(**kwargs):
            agent_name = kwargs["agent_name"]
            return (
                [],
                [
                    {
                        "id": f"item-{agent_name}",
                        "summary": "Repeated snippet across stores",
                        "score": 0.9 if agent_name == "main" else 0.85,
                        "resource_id": f"res-{agent_name}",
                    }
                ],
                {f"res-{agent_name}": f"/tmp/{agent_name}.md"},
            )

        with (
            mock.patch.object(search_module, "migrate_legacy_single_db_to_agent_db"),
            mock.patch.object(search_module, "_build_llm_configs", return_value=(object(), object())),
            mock.patch.object(search_module, "_resolve_search_targets", return_value=["main", "research"]),
            mock.patch.object(search_module, "_search_agent_store", side_effect=fake_search_agent_store),
            mock.patch.dict(
                os.environ,
                {
                    "MEMU_WORKSPACE_DIR": "/tmp",
                    "MEMU_EXTRA_PATHS": "[]",
                },
                clear=False,
            ),
        ):
            result = asyncio.run(
                search_module.search(
                    query_text="dedupe repeated memories",
                    requesting_agent="main",
                    search_stores=["self", "research"],
                    max_results=5,
                    min_score=0.0,
                    user_id="default",
                    mode="fast",
                    category_quota=0,
                    item_quota=5,
                    queries=None,
                )
            )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["agentName"], "main")

    def test_search_can_return_shared_documents_with_agent_name_shared(self) -> None:
        class FakeHybridDatabaseManager:
            def __init__(self, *args, **kwargs) -> None:
                self.closed = False

            def search_shared_documents(self, query: str, owner_filter=None):
                return [
                    {
                        "id": "chunk-1",
                        "document_id": "doc-1",
                        "chunk_index": 0,
                        "content": "Shared design note",
                        "score": 0.91,
                    }
                ]

            def close(self) -> None:
                self.closed = True

        with (
            mock.patch.object(search_module, "migrate_legacy_single_db_to_agent_db"),
            mock.patch.object(search_module, "_build_llm_configs", return_value=(object(), object())),
            mock.patch.object(search_module, "_resolve_search_targets", return_value=["shared"]),
            mock.patch.object(search_module, "HybridDatabaseManager", FakeHybridDatabaseManager),
            mock.patch.dict(
                os.environ,
                {
                    "MEMU_WORKSPACE_DIR": "/tmp",
                    "MEMU_EXTRA_PATHS": "[]",
                },
                clear=False,
            ),
        ):
            result = asyncio.run(
                search_module.search(
                    query_text="shared design",
                    requesting_agent="main",
                    search_stores=["shared"],
                    max_results=5,
                    min_score=0.0,
                    user_id="default",
                    mode="fast",
                    category_quota=0,
                    item_quota=5,
                    queries=None,
                )
            )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["agentName"], "shared")
        self.assertEqual(result["results"][0]["source"], "document")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Regression tests for sync batching and rate-limit backoff handling."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))
SRC_DIR = PYTHON_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class SyncBatchBackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="memu_sync_batch_")
        self.data_dir = Path(self.temp_dir) / "data"
        self.sessions_dir = Path(self.temp_dir) / "sessions"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self._old_env = {
            name: os.environ.get(name)
            for name in (
                "MEMU_DATA_DIR",
                "OPENCLAW_SESSIONS_DIR",
                "MEMU_AGENT_NAME",
                "MEMU_MAX_INGEST_ITEMS_PER_SYNC",
                "MEMU_RATE_LIMIT_BACKOFF_SECONDS",
                "MEMU_RATE_LIMIT_BACKOFF_MAX_SECONDS",
            )
        }
        os.environ["MEMU_DATA_DIR"] = str(self.data_dir)
        os.environ["OPENCLAW_SESSIONS_DIR"] = str(self.sessions_dir)
        os.environ["MEMU_AGENT_NAME"] = "main"
        os.environ["MEMU_MAX_INGEST_ITEMS_PER_SYNC"] = "2"
        os.environ["MEMU_RATE_LIMIT_BACKOFF_SECONDS"] = "60"
        os.environ["MEMU_RATE_LIMIT_BACKOFF_MAX_SECONDS"] = "900"

        if "auto_sync" in sys.modules:
            self.auto_sync = importlib.reload(sys.modules["auto_sync"])
        else:
            self.auto_sync = importlib.import_module("auto_sync")

    def tearDown(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stops_batch_after_first_rate_limit_and_defers_remaining(self) -> None:
        converted = [f"/tmp/item-{idx}.json" for idx in range(5)]
        seen_calls: list[str] = []
        captured_logs: list[str] = []

        class DummyService:
            def __init__(self) -> None:
                self.database = types.SimpleNamespace(close=lambda: None)

            async def memorize(self, *, resource_url: str, modality: str, user: dict) -> None:
                seen_calls.append(resource_url)
                if resource_url.endswith("item-0.json"):
                    raise Exception("RateLimitError: Error code: 429 - too many requests")

        with (
            mock.patch.object(self.auto_sync, "_try_acquire_lock", return_value=object()),
            mock.patch.object(self.auto_sync, "_release_lock"),
            mock.patch.object(self.auto_sync, "_read_last_sync", return_value=0.0),
            mock.patch.object(self.auto_sync, "convert", return_value=converted),
            mock.patch.object(self.auto_sync, "_read_prev_session_ids", return_value=[]),
            mock.patch.object(self.auto_sync, "_main_session_file_exists", return_value=True),
            mock.patch.object(self.auto_sync, "resource_exists", return_value=False),
            mock.patch.object(self.auto_sync, "_agent_memory_enabled", return_value=True),
            mock.patch.object(self.auto_sync, "build_service", side_effect=lambda agent_name: DummyService()),
            mock.patch.object(self.auto_sync, "_log", side_effect=captured_logs.append),
        ):
            asyncio.run(self.auto_sync.sync_once())

        self.assertEqual(seen_calls, ["/tmp/item-0.json"])

        pending = self.auto_sync._load_pending_ingest("main")
        self.assertEqual(pending, converted)

        backoff = self.auto_sync._load_backoff_state("main")
        self.assertEqual(backoff["consecutive_rate_limits"], 1)
        self.assertEqual(backoff["reason"], "rate_limit")
        self.assertGreater(float(backoff["next_retry_ts"]), 0.0)
        self.assertTrue(
            any("stopping current ingest batch immediately" in line for line in captured_logs),
            captured_logs,
        )

    def test_successful_batch_keeps_tail_pending_without_backoff(self) -> None:
        converted = [f"/tmp/success-{idx}.json" for idx in range(4)]
        seen_calls: list[str] = []

        class DummyService:
            def __init__(self) -> None:
                self.database = types.SimpleNamespace(close=lambda: None)

            async def memorize(self, *, resource_url: str, modality: str, user: dict) -> None:
                seen_calls.append(resource_url)

        with (
            mock.patch.object(self.auto_sync, "_try_acquire_lock", return_value=object()),
            mock.patch.object(self.auto_sync, "_release_lock"),
            mock.patch.object(self.auto_sync, "_read_last_sync", return_value=0.0),
            mock.patch.object(self.auto_sync, "convert", return_value=converted),
            mock.patch.object(self.auto_sync, "_read_prev_session_ids", return_value=[]),
            mock.patch.object(self.auto_sync, "_main_session_file_exists", return_value=True),
            mock.patch.object(self.auto_sync, "resource_exists", return_value=False),
            mock.patch.object(self.auto_sync, "_agent_memory_enabled", return_value=True),
            mock.patch.object(self.auto_sync, "build_service", side_effect=lambda agent_name: DummyService()),
            mock.patch.object(self.auto_sync, "_log"),
        ):
            asyncio.run(self.auto_sync.sync_once())

        self.assertEqual(seen_calls, converted[:2])
        self.assertEqual(self.auto_sync._load_pending_ingest("main"), converted[2:])
        self.assertEqual(
            self.auto_sync._load_backoff_state("main"),
            {"next_retry_ts": 0.0, "consecutive_rate_limits": 0, "reason": ""},
        )
        self.assertGreater(self.auto_sync._read_last_sync("main"), 0.0)


if __name__ == "__main__":
    unittest.main()

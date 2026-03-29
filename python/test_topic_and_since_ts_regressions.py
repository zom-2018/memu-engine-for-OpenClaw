#!/usr/bin/env python3
"""Regression tests for topic transcript discovery and since_ts behavior."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, session_id: str, messages: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "type": "session",
                    "version": 3,
                    "id": session_id,
                    "timestamp": "2026-03-30T00:00:00.000Z",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        for idx, (role, content) in enumerate(messages):
            f.write(
                json.dumps(
                    {
                        "type": "message",
                        "id": f"{session_id}-{idx}",
                        "timestamp": f"2026-03-30T00:00:{idx:02d}.000Z",
                        "message": {
                            "role": role,
                            "content": [{"type": "text", "text": content}],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


class ConvertSessionsRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="memu_topic_regression_")
        self.sessions_dir = Path(self.temp_dir) / "sessions"
        self.data_dir = Path(self.temp_dir) / "data"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._old_env = {
            "OPENCLAW_SESSIONS_DIR": os.environ.get("OPENCLAW_SESSIONS_DIR"),
            "MEMU_DATA_DIR": os.environ.get("MEMU_DATA_DIR"),
            "MEMU_MAX_MESSAGES_PER_SESSION": os.environ.get("MEMU_MAX_MESSAGES_PER_SESSION"),
        }
        os.environ["OPENCLAW_SESSIONS_DIR"] = str(self.sessions_dir)
        os.environ["MEMU_DATA_DIR"] = str(self.data_dir)
        os.environ["MEMU_MAX_MESSAGES_PER_SESSION"] = "50"

        if "convert_sessions" in sys.modules:
            self.convert_sessions = importlib.reload(sys.modules["convert_sessions"])
        else:
            self.convert_sessions = importlib.import_module("convert_sessions")

    def tearDown(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_discovers_topic_transcripts_from_sessions_index(self) -> None:
        topic_session_id = "0e61378e-5abd-4e23-acb8-3dfdef867530"
        _write_json(
            self.sessions_dir / "sessions.json",
            {
                "agent:main:telegram:topic:1": {
                    "sessionId": topic_session_id,
                    "updatedAt": 1774816800000,
                }
            },
        )
        _write_jsonl(
            self.sessions_dir / f"{topic_session_id}-topic-1.jsonl",
            topic_session_id,
            [("user", "topic question"), ("assistant", "topic answer")],
        )

        discovered = self.convert_sessions.discover_all_session_files(str(self.sessions_dir), ["main"])

        self.assertEqual(
            discovered["main"],
            [str(self.sessions_dir / f"{topic_session_id}-topic-1.jsonl")],
        )

    def test_resolves_topic_transcript_for_forced_convert(self) -> None:
        topic_session_id = "2f07dc47-a1cf-435e-a1ba-9b0bdcd8d204"
        _write_jsonl(
            self.sessions_dir / f"{topic_session_id}-topic-3.jsonl",
            topic_session_id,
            [("user", "forced topic flush"), ("assistant", "done")],
        )

        converted = self.convert_sessions.convert(
            since_ts=None,
            session_id=topic_session_id,
            force_flush=True,
            agent_name="main",
        )

        basenames = sorted(Path(path).name for path in converted)
        self.assertEqual(basenames, [f"{topic_session_id}.part000.json"])

    def test_auto_discovery_preserves_topic_suffix_in_output_parts(self) -> None:
        topic_session_id = "ca0cb4c1-5d6d-4270-9faa-25d2d5bf965a"
        _write_json(
            self.sessions_dir / "sessions.json",
            {
                "agent:main:telegram:topic:99": {
                    "sessionId": topic_session_id,
                    "updatedAt": 1774816800000,
                }
            },
        )
        _write_jsonl(
            self.sessions_dir / f"{topic_session_id}-topic-2.jsonl",
            topic_session_id,
            [("user", "auto topic question"), ("assistant", "auto topic answer")],
        )

        converted = self.convert_sessions.convert(
            since_ts=None,
            agent_name="main",
            force_flush=True,
        )
        basenames = sorted(Path(path).name for path in converted)
        self.assertEqual(basenames, [f"{topic_session_id}-topic-2.part000.json"])

    def test_first_seen_older_session_is_not_skipped_by_since_ts(self) -> None:
        session_id = "190ac435-55de-4fc7-b9ad-be16bd5daf03"
        session_path = self.sessions_dir / f"{session_id}.jsonl"
        _write_jsonl(
            session_path,
            session_id,
            [("user", "older but newly discovered"), ("assistant", "captured")],
        )

        older_mtime = 1_700_000_000
        os.utime(session_path, (older_mtime, older_mtime))

        converted = self.convert_sessions.convert(
            since_ts=older_mtime + 1000,
            session_id=session_id,
            agent_name="main",
        )
        basenames = sorted(Path(path).name for path in converted)

        self.assertEqual(basenames, [f"{session_id}.part000.json"])


if __name__ == "__main__":
    unittest.main()

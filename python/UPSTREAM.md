# Upstream Tracking (MemU)

This directory vendors (copies) the upstream MemU Python package into the OpenClaw plugin.
The goal is to keep the plugin self-contained and easy to review, while still being able to
sync from upstream with minimal friction.

## Upstream

- Upstream repository: https://github.com/NevaMind-AI/MemU
- Upstream version/tag: `v1.5.1`
- Upstream commit (best-effort snapshot): `357aefc8012705bfde723f141d52f675fe712bed`
- Date imported: 2026-03-29

## What We Vendor

We keep:

- `python/src/memu/` (the MemU Python package)

We do NOT vendor most upstream repo content (docs/examples/tests/assets) because the OpenClaw
extension only needs runtime code.

## OpenClaw Integration Files (Added)

These files are specific to OpenClaw and do not exist upstream:

- `python/watch_sync.py`: watches OpenClaw session logs and triggers ingestion.
- `python/auto_sync.py`: incremental ingestion for OpenClaw sessions (uses `OPENCLAW_SESSIONS_DIR`).
- `python/convert_sessions.py`: converts OpenClaw `.jsonl` sessions into MemU conversation resources.
- `python/docs_ingest.py`: ingests Markdown files from `MEMU_EXTRA_PATHS` (optional).
- `python/scripts/search.py`: CLI entrypoint used by the Node plugin for retrieval.
- `python/scripts/get.py`: CLI entrypoint used by the Node plugin for reading a resource.

## Upstream Delta Ledger

These are the local changes that intentionally diverge from vanilla upstream and must be preserved or
revalidated on every sync.

1) OpenClaw ingestion and sync overlay

- Files: `python/watch_sync.py`, `python/auto_sync.py`, `python/convert_sessions.py`, `python/docs_ingest.py`
- Purpose:
  - translate OpenClaw session logs and workspace docs into MemU resources
  - keep background sync and one-shot sync behavior inside the plugin
  - preserve OpenClaw-specific session and workspace semantics

2) Plugin CLI bridge

- Files: `python/scripts/search.py`, `python/scripts/get.py`, `python/scripts/flush.py`
- Purpose:
  - provide the Node plugin tool entrypoints for `memory_search`, `memory_get`, and `memory_flush`
  - keep the plugin self-contained and callable from OpenClaw without importing upstream CLI internals

3) Data layout and migration compatibility

- Files: `python/scripts/migrate_storage_layout.py`, `python/scripts/migrate_agent_id.py`, `python/src/memu/migration/*`
- Purpose:
  - preserve the OpenClaw-specific storage layout and migration path
  - maintain backward compatibility with older per-agent and legacy single-db layouts

4) SQLite and model compatibility patches

- Files: `python/src/memu/database/models.py`, `python/src/memu/database/sqlite/session.py`, `python/src/memu/database/sqlite/models.py`
- Purpose:
  - keep scoped model creation stable under Pydantic 2 / Python 3.13
  - keep SQLite connection handling resilient in plugin environments
  - preserve the `embedding_json` storage workaround for SQLite table models

5) Runtime safety patching

- File: `python/src/memu/llm/openai_sdk.py`
- Purpose:
  - keep a sane default timeout for stalled provider calls

## Sync Policy

We sync only upstream-owned runtime code and metadata, then reapply the local overlay above.

- Upstream-owned paths: `python/src/memu/**`, `python/pyproject.toml`, `python/uv.lock`
- Local overlay paths: all OpenClaw-specific scripts, plugin entrypoint, manifest, docs, and vendored patches

When reviewing a sync, prefer explicit patch files or patch sections over inline shell edits. If a patch no
longer applies cleanly, stop and reconcile the divergence instead of forcing a partial update.

## How to Sync From Upstream

There is a helper script at the plugin root:

- `update_from_upstream.sh`

Notes:

- It copies upstream `src/` into `python/src/` only for upstream-owned files.
- It keeps OpenClaw-specific scripts and plugin glue untouched.
- It reapplies the local overlay in a controlled, fail-fast order.
- It must not rewrite the plugin's packaging strategy into upstream's maturin install path.

After syncing:

1) Re-run `openclaw gateway restart`
2) Call `memory_search` once to confirm the plugin still executes

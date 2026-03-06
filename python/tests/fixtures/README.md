# Migration Fixtures (v0.2.6 + synthetic)

This directory contains reproducible fixtures for migration testing.

## Files

- `generate_v026_db.sh`: Generates `v026_real.db`, a pre-migration SQLite DB representing v0.2.6-era schema shape (notably **without** `agentName` columns), with sample data:
  - 10 memories
  - 5 categories
  - 3 resources
- `generate_synthetic_fixtures.py`: Generates 5 SQL fixtures:
  - `empty_db.sql`
  - `single_agent.sql` (100 memories)
  - `multi_agent.sql` (3 agents + cross references)
  - `large_db.sql` (1200 memories)
  - `corrupted_schema.sql` (intentionally missing expected columns, including `agentName`)
- `generate_multi_agent_fixtures.py`: Generates directory fixtures under `tests/fixtures/multi_agent/` for per-agent + shared hybrid-memory testing.

## Regeneration

From repository root:

```bash
bash python/tests/fixtures/generate_v026_db.sh
python3 python/tests/fixtures/generate_synthetic_fixtures.py
python3 python/tests/fixtures/generate_multi_agent_fixtures.py
```

## Quick verification

Ensure `v026_real.db` has no `agentName` column:

```bash
sqlite3 python/tests/fixtures/v026_real.db "PRAGMA table_info(memu_memory_items);"
```

The output should not include `agentName`.

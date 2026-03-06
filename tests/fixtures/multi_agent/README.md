# Multi-agent fixture set

This fixture set models a hybrid memory root with three agent stores and one shared store.

## Structure

```text
tests/fixtures/multi_agent/
├── main/
│   ├── sessions/
│   │   └── session1.jsonl
│   └── memu.db
├── research/
│   ├── sessions/
│   │   └── session2.jsonl
│   └── memu.db
├── coding/
│   ├── sessions/
│   │   └── session3.jsonl
│   └── memu.db
└── shared/
    └── memu.db
```

## Dataset profile

- 10 sessions per agent (30 total distinct session IDs)
- Session JSONL includes mixed message types: `chat`, `tool_call`, and periodic `error`
- Each agent DB contains:
  - `memories` table with 10 rows
  - `sessions` table with 10 rows
  - `documents` table with a local marker row
- Shared DB contains at least 5 documents in `documents` and matching rows in `document_chunks`

## Regeneration

From repository root:

```bash
python3 python/tests/fixtures/generate_multi_agent_fixtures.py
```

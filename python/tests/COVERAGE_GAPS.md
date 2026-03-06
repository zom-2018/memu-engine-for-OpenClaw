# Test Coverage Gaps

Audit scope: `HYBRID_UNIFIED_VALIDATION.md` P0/P1 requirements vs existing tests:

- `python/tests/test_e2e_hybrid.py`
- `python/tests/test_access_control.py`
- `python/tests/test_backward_compat_agentname.py`
- `python/tests/test_acceptance.py`
- `python/tests/test_ingest_pipeline.py`

## P0 Requirements

### ✅ Covered
- **Legacy migration (partial):**
  - `test_e2e_hybrid.py::test_full_hybrid_workflow` verifies `migrate_agent_id` adds `agent_id` and backfills from `agentName` mapping.
  - `test_acceptance.py::test_migration_and_backward_compat` exercises legacy schema migration + backup + idempotency path.
- **Shared docs ingest to shared DB (partial):**
  - `test_ingest_pipeline.py::test_ingest_document_writes_shared_rows` verifies document rows/chunks are persisted in `shared/memu.db`.
- **Cross-agent access guardrails on shared docs (adjacent coverage):**
  - `test_access_control.py` verifies deny/allow behavior and NULL owner compatibility in shared-doc search path.

### ❌ Missing
- **P0-2 Default `self` search behavior is not directly tested at script entrypoint level** (`python/scripts/search.py`):
  - No test proves that when `search-stores` is omitted and `MEMU_AGENT_SETTINGS` is unset, search resolution defaults to `['self']`.
- **P0-3 `memoryEnabled=false` policy enforcement missing:**
  - No test verifies sync/ingest write path skips structured memory writes for disabled agent.
- **P0-3 `searchEnabled=false` policy enforcement missing:**
  - No test verifies search returns empty for an agent with search disabled.
- **P0-3 `searchableStores=['self','shared']` mixed retrieval missing:**
  - No test verifies one query can return both private agent memory hits and shared document hits under explicit policy.
- **P0-4 Shared-doc independence is only partially covered:**
  - Existing tests prove writes to shared DB, but no test asserts agent-private DB(s) remain untouched after `docs_ingest`.
- **P0-1 Legacy migration backup artifact verification missing in e2e hybrid path:**
  - No test in hybrid workflow asserts backup file creation/location during automatic legacy migration entrypoint.

## P1 Requirements

### ✅ Covered
- **Backward compatibility (partial):**
  - `test_backward_compat_agentname.py` verifies legacy nullable `owner_agent_id` remains accessible under restricted mode.
  - Same test also guards presence of legacy `agentName` field in scope model.
- **Negative case (partial):**
  - `test_access_control.py::test_null_owner_agent_id_treated_as_public` covers legacy NULL-owner handling.

### ❌ Missing
- **P1-1 Multi-agent fanout + RRF merge stability missing:**
  - No test drives federated fanout across multiple target stores and asserts RRF ranking order determinism.
- **P1-1 De-dup behavior after fanout missing:**
  - No test verifies duplicate snippets from multiple stores are deduplicated before top-k truncation.
- **P1-2 Backward-compat warning assertions missing:**
  - No test asserts warning/ignore behavior when legacy params (e.g., non-hybrid `storageMode`, old retrieval flags) are provided.
- **P1-2 Restart persistence of policies missing:**
  - No test validates watch/sync restart still honors `MEMU_AGENT_SETTINGS` policy state.
- **P1-3 Invalid `searchableStores` values missing:**
  - No negative test for malformed store names and expected fallback/error handling.
- **P1-3 Nonexistent agent target missing:**
  - No test verifies behavior when requested store/agent does not exist in memory root.
- **P1-3 Shared-empty retrieval behavior missing:**
  - No test verifies explicit `shared` search when shared DB has no document rows returns stable empty result (not error).

## Priority Gap Backlog (recommended first additions)

1. Default `self` resolution test at `python/scripts/search.py` boundary.
2. `memoryEnabled=false` write-skip test in sync/ingest pipeline.
3. `searchEnabled=false` empty-search test.
4. `searchableStores=['self','shared']` mixed-hit test.
5. Fanout + RRF deterministic ranking + dedup test.
6. Legacy-parameter warning assertion test.
7. Invalid/nonexistent store negative-case tests.

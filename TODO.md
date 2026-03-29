# TODO

## Session lifecycle alignment with OpenClaw / lossless-claw

Goal: make `memu-engine` behave predictably in installations that also use
OpenClaw session policies and `lossless-claw`.

### Problems to solve

- `memu-engine` does not currently read OpenClaw session-policy semantics such as:
  - stateless subagent sessions
  - ignored cron sessions
  - heartbeat-style system sessions
- ingestion policy and session-retention policy are currently separate concerns
- native OpenClaw can rotate/archive sessions, but `memu-engine` does not expose a
  first-class "safe to ingest, then safe to delete" policy layer

### Planned work

1. Add `sessionPolicy` config to `memu-engine`
   - `ignorePatterns`
   - `ignoreKinds`
   - `ignoreHeartbeat`
   - `ignoreSubagents`
   - `ignoreCron`
   - `deleteOnlyAfterIngest`
   - `retentionDays`

2. Teach `convert_sessions.py` to skip non-user-facing sessions by policy
   - `*:heartbeat`
   - `agent:*:subagent:*`
   - `agent:*:cron:*`

3. Align with OpenClaw session metadata
   - consume tracked session keys/patterns more explicitly
   - preserve salvage behavior for reset/archived sessions

4. Add optional cleanup phase
   - finalize staged tail
   - ingest conversation parts
   - mark resource as safely ingested
   - delete/archive old transcript files only after successful ingestion

5. Add tests
   - mixed `lossless-claw + memu-engine` setup
   - heartbeat/subagent/cron filtering
   - retention after successful ingest
   - reset/archive salvage path

### Notes

- OpenClaw native `session.reset.*` and `agents.defaults.subagents.archiveAfterMinutes`
  are still the preferred first-line controls for session churn.
- `memu-engine` should complement those policies, not fight them.

## Memory metadata enrichment for channel and topic-aware retrieval

Goal: preserve a small, high-signal set of OpenClaw session metadata so memU can
filter and rank memory by channel/thread context without polluting the memory text.

### Problems to solve

- topic-aware transcripts are now ingested, but topic identity mostly survives only
  in filenames like `<session_id>-topic-1.part000.json`
- useful OpenClaw session metadata exists in `sessions.json` and session headers, but
  is not normalized into memU resource or memory-item metadata
- retrieval currently cannot prefer "this Telegram topic first, then this group, then global"
- debugging incorrect memory hits is harder because channel/thread provenance is mostly lost

### Planned work

1. Add minimal structured metadata capture during conversation conversion/ingest
   - `session_id`
   - `channel`
   - `group_id` / `chat_id`
   - `topic_id`
   - `spawned_by`
   - `provider`
   - `model`
   - `workspace_dir` / `cwd`

2. Keep metadata separate from memory text
   - do not inject operational fields into the summarized conversation body
   - store metadata for filtering, ranking, and diagnostics only

3. Use metadata for retrieval policy later
   - prefer same `topic_id` when present
   - then same `group_id` / `chat_id`
   - then same `channel`
   - then global agent/shared search

4. Preserve backward compatibility
   - metadata fields should be optional
   - older resources and DB rows should continue to work without migration blockers
   - no hard dependency on Telegram-only concepts for non-Telegram channels

5. Add tests
   - Telegram topic transcript keeps `topic_id`
   - non-topic Telegram chat keeps `channel` + `group_id`
   - non-Telegram sessions ingest without channel-specific fields
   - retrieval/ranking can prefer same-topic resources when metadata is present

### Nice-to-have later

- `session_started_at`
- `part_index` / `part_count`
- coarse tool usage metadata such as `tool_names_seen`
- explicit session kind markers such as `subagent`, `cron`, `heartbeat`

### Do not store by default

- raw tool-call ids
- full runtime event logs
- every message id
- low-level transport noise that is not useful for retrieval

### Notes

- This should stay a metadata-layer improvement, not a prompt-bloat change.
- The target is better retrieval precision and debuggability, not richer summaries.

## Sealos-assistant style integration niceties

Goal: borrow the useful application-pattern details from MemU's `examples/sealos-assistant/main.py`
without turning `memu-engine` itself into a full web app.

### What Already Exists

- plugin-level memory runtime registration
- `memory_search` and `memory_get`
- per-agent scoped retrieval config
- background memorize/sync pipeline

### Useful Gaps to Close

1. Add explicit fail-soft memory UX
- if memory backend is unavailable, expose clear runtime status/errors instead of opaque failures
- preserve normal agent behavior when memory is degraded

2. Add a lightweight backend-status/probe surface
- embedding readiness
- extraction readiness
- current DB path
- pending queue size
- active backoff state

3. Add a reusable recall-context formatter
- return a bounded top-N context pack for agent prompting
- avoid dumping raw DB rows or oversized snippets

4. Add a lightweight explicit memorize/retrieve compatibility layer
- make it easier for external surfaces or future apps to call into `memorize` / `retrieve`
  through stable runtime helpers, not only tool invocation patterns

5. Add scoping and UX guidance
- document the equivalent of `user_id`/tenant scoping for OpenClaw agent/session environments
- document bounded-memory-context recommendations for upstream app integrators

### Notes

- This section is about integration ergonomics, not proactive autonomy.
- The goal is to make memu-engine easier to embed into apps, channels, and future OpenClaw surfaces.

## Cross-cutting roadmap

The three planning tracks in this repository should stay explicitly connected:

1. Foundations in this file (`TODO.md`)
- session lifecycle alignment
- metadata enrichment
- app/integration niceties

2. Proactive execution in [TODO_PROACTIVITY.md](./TODO_PROACTIVITY.md)
- derive pending follow-up work from memory
- expose it safely
- let OpenClaw heartbeat/cron consume it

3. Skill generation in [TODO_SKILL_GENERATION.md](./TODO_SKILL_GENERATION.md)
- derive reusable skill evidence from memory
- synthesize staged `SKILL.md` drafts
- hand off to eval/promotion

## Shared design rule

All three tracks should share the same normalized evidence foundation:

- one ingestion path
- one metadata model
- one traceability model for source session/resource ids
- separate operational outputs on top of that foundation:
  - proactive items
  - skill candidates
  - recall context packs

## Release plan

Use a compact 3-stage rollout:

1. Current release
- land the foundation from this file
- session lifecycle alignment
- metadata enrichment
- Sealos-assistant style integration niceties
- unified normalized evidence + traceability layer

2. Next release
- land proactivity from [TODO_PROACTIVITY.md](./TODO_PROACTIVITY.md)
- action-item extraction
- proactive item store
- todo retrieval surface
- heartbeat/cron integration with loop safety

3. Following release
- land skill generation from [TODO_SKILL_GENERATION.md](./TODO_SKILL_GENERATION.md)
- candidate grouping
- staged `SKILL.md` synthesis
- eval / test / promote workflow


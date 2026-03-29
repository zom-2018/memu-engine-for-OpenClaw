# TODO: Proactivity for OpenClaw memu-engine

## Relationship to Other TODOs

This document depends on and should evolve together with:

- [TODO.md](./TODO.md)
  - session lifecycle alignment
  - memory metadata enrichment for channel/topic-aware retrieval
  - Sealos-assistant style integration niceties
- [TODO_SKILL_GENERATION.md](./TODO_SKILL_GENERATION.md)
  - shared evidence grouping, traceability, and staged artifact generation

Proactivity should consume the same normalized memory/resource metadata that skill generation uses.
It should not invent a separate ingestion model.
## Goal

Bring `memu-engine` closer to the proactive loop shown in MemU's `examples/proactive/proactive.py`,
but in a way that fits OpenClaw's agent, cron, heartbeat, and plugin runtime model.


## Release target

This track is intended for the next release after the current foundation work lands.

Prerequisites from [TODO.md](./TODO.md):
- session lifecycle alignment
- metadata enrichment
- integration/status niceties
- unified evidence + traceability foundation

## What Already Exists

- Background sync already ingests OpenClaw sessions and selected workspace/docs into memU.
- The plugin already registers OpenClaw memory runtime hooks and memory prompt guidance.
- Agents can already use `memory_search` and `memory_get` through the active memory slot.
- Retrieval already supports per-agent scoping plus shared-store access.
- Session conversion already has some awareness of scheduled/system payloads and cron-like envelopes.

## What Is Missing Compared with the MemU Proactive Example

- No explicit todo or follow-up extraction layer.
- No `memu_todos`-style tool or API for returning pending action items.
- No proactive execution loop that re-injects pending todos into future agent turns.
- No queue semantics for proactive work: pending, claimed, done, failed, deferred.
- No safety policy to prevent runaway follow-up loops.
- No priority/cooldown/dedup layer for repeated proactive suggestions.
- No bridge from memory-derived action items into OpenClaw cron or heartbeat jobs.

## Design Direction

1. Add a structured proactive-item model
- Support a new internal record type for memory-derived follow-up work.
- Minimum fields:
  - `id`
  - `agent_id`
  - `session_id`
  - `source_resource_id`
  - `title`
  - `instructions`
  - `priority`
  - `status`
  - `created_at`
  - `next_attempt_at`
  - `retry_count`
  - `dedupe_key`

2. Add extraction of todos/action items from newly ingested memory
- Start with conservative extraction from:
  - explicit user todo requests
  - deferred action items
  - follow-up commitments
  - unfinished troubleshooting tasks
- Keep this separate from normal memory text and category summaries.

3. Add a retrieval surface for proactive items
- Add a runtime method and compatibility tool such as `memory_todos` / `memu_todos`.
- Allow filters by:
  - `agentId`
  - `status`
  - `limit`
  - optional scope such as same topic/group/session later

4. Integrate with OpenClaw execution surfaces
- Heartbeat: lightweight review of pending proactive items.
- Cron: bounded execution of queued proactive work.
- Manual tool path: allow explicit inspection and flushing from chat.

5. Add execution-state transitions
- `pending`
- `claimed`
- `done`
- `failed`
- `deferred`
- `ignored`

6. Add loop-safety controls
- max retries
- cooldown after failure
- dedupe repeated items
- optional approval requirement for risky proactive actions
- per-run cap on how many proactive items can be surfaced/executed

## Recommended MVP

1. Add extraction of explicit action items into a small `pending_actions` store.
2. Add `memu_todos` read-only tool/API.
3. Add a cron-friendly helper that returns top N pending items for one agent.
4. Do not auto-execute actions inside the plugin yet.
5. Let OpenClaw cron or heartbeat consume the todo list first.

## Open Questions

- Should proactive items live in the same SQLite DB or a separate sidecar DB/table?
- Should completed proactive items remain searchable as memory, or stay operational only?
- Should heartbeat only surface todos, while cron performs execution?
- How should cross-agent proactive ownership be resolved when `shared` memory produced the item?

## Testing

- explicit user follow-up request becomes one pending proactive item
- repeated ingestion of the same transcript does not duplicate the item
- failed proactive item enters cooldown instead of immediate retry storm
- cron/heartbeat consumers can fetch a bounded pending set
- old installations work with no proactive store configured

## Dependency Order

1. Land session lifecycle and metadata foundations from [TODO.md](./TODO.md).
2. Add proactive-item extraction and storage.
3. Add retrieval/API surface for pending proactive items.
4. Integrate with cron/heartbeat/manual tool flows.
5. Reuse the same source-trace conventions that skill generation will need.

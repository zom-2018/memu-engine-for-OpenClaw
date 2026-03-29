# TODO: Skill Generation for OpenClaw memu-engine

## Relationship to Other TODOs

This document depends on and should evolve together with:

- [TODO.md](./TODO.md)
  - session lifecycle alignment
  - memory metadata enrichment for channel/topic-aware retrieval
  - Sealos-assistant style integration niceties
- [TODO_PROACTIVITY.md](./TODO_PROACTIVITY.md)
  - shared follow-up/action extraction and evidence handling

Skill generation should reuse the same normalized evidence graph used by proactivity:
raw memory items -> grouped evidence -> staged artifacts -> reviewed promotion.
## Goal

Bring `memu-engine` closer to the `examples/example_2_skill_extraction.py` pattern from upstream MemU:
extract reusable skill knowledge from logs and sessions, synthesize candidate `SKILL.md` documents,
and support a safe evaluation/promote workflow for OpenClaw skills.


## Release target

This track is intended for the release after proactivity.

Prerequisites from [TODO.md](./TODO.md):
- session lifecycle alignment
- metadata enrichment
- integration/status niceties
- unified evidence + traceability foundation

Prerequisites from [TODO_PROACTIVITY.md](./TODO_PROACTIVITY.md):
- stable grouped evidence patterns
- operational source traceability conventions
- confidence that memory-derived artifacts can be managed without runaway loops

## What Already Exists

- The ingestion pipeline already extracts `skill` memory items.
- `skill` has a rich prompt and expects comprehensive reusable skill profiles.
- Background sync already memorizes session-derived conversation parts.
- Docs ingestion also includes `skill` as one of the extracted memory types.
- The plugin already exposes recall tools that can retrieve skill-like memory later.

## What Is Missing Compared with the MemU Skill Example

- No dedicated skill-candidate generation pipeline.
- No grouping of multiple related `skill` memory items into one evolving skill draft.
- No synthesis stage that emits real `SKILL.md` artifacts.
- No skill workspace/eval integration in the plugin itself.
- No promotion gate for updating an existing OpenClaw skill from memory-derived evidence.
- No source-trace bundle showing which memory items justified the generated skill.

## Design Direction

1. Introduce a `skill_candidate` pipeline stage
- Treat raw `skill` memory items as evidence, not publishable skill files.
- Group evidence by dedupe key or topic cluster.
- Preserve source resource ids and citations for each candidate.

2. Add skill synthesis
- Add a synthesizer that can generate a draft OpenClaw `SKILL.md` from:
  - grouped `skill` memory items
  - category summaries
  - source snippets/examples
- Emit drafts to a staging area, not directly into a live skills directory.

3. Add skill candidate storage
- Persist per-candidate metadata such as:
  - `candidate_id`
  - `title`
  - `slug`
  - `status`
  - `source_memory_item_ids`
  - `source_resource_ids`
  - `draft_path`
  - `last_updated_at`
  - `evaluation_status`

4. Add compare/promote workflow
- New skill
- Update existing skill
- Reject candidate
- Merge candidate into existing candidate cluster

5. Add evaluation handoff hooks
- Export candidates in a way that an OpenClaw eval workspace or external skill benchmark can consume.
- Support a future human-in-the-loop approval step before promotion.

## Recommended MVP

1. Generate grouped skill candidates from memory evidence.
2. Synthesize draft `SKILL.md` files into a staging directory.
3. Persist source references for traceability.
4. Stop there; do not auto-promote into live skills yet.

## Future Workflow

1. memory ingestion produces raw `skill` items
2. grouper builds/updates `skill_candidate`
3. synthesizer emits draft `SKILL.md`
4. external eval workspace runs comparisons
5. human approves promotion into a real skill

## Open Questions

- Should the staging format be raw markdown files, JSON descriptors, or both?
- Should grouping happen by category, semantic similarity, or explicit dedupe keys?
- Should skill synthesis reuse the current `skill` prompt, or use a separate finalizer prompt?
- Where should promoted skills be written in OpenClaw-aware environments?

## Testing

- multiple related `skill` memory items become one candidate, not many duplicates
- candidate updates remain traceable to source memory/resource ids
- synthesized drafts are stable enough for repeated runs
- old installations work with no skill-generation stage enabled
- malformed or low-confidence skill evidence does not auto-promote

## Dependency Order

1. Land session lifecycle and metadata foundations from [TODO.md](./TODO.md).
2. Reuse stable source/resource metadata so candidates stay traceable.
3. Group raw `skill` memory items into reusable evidence clusters.
4. Synthesize staged `SKILL.md` drafts.
5. Hand off to eval/promotion workflow.
6. Keep promotion separate from proactive execution loops, but compatible with them.

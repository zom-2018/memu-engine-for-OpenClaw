<!-- markdownlint-disable MD013 MD031 MD032 MD033 MD034 MD040 MD004 MD030 MD022 MD007 MD012 MD009 MD025 -->
# memU Engine for OpenClaw

`memu-engine` is a memory plugin for OpenClaw that vendors the MemU Python runtime and exposes it through the OpenClaw memory slot.

This fork is updated for:

- `memU v1.5.1`
- Python `3.13+`
- the current OpenClaw plugin SDK entrypoint style
- the current OpenClaw memory-plugin runtime contract

Project references:

- OpenClaw: https://github.com/openclaw/openclaw
- MemU upstream: https://github.com/NevaMind-AI/MemU

> [!IMPORTANT]
> This is not a cosmetic fork.
>
> This fork rebases the plugin onto `memU v1.5.1`, upgrades the runtime to Python `3.13+`, aligns the plugin with the modern OpenClaw memory-plugin APIs, and adds verified compatibility coverage for older SQLite and storage-layout upgrades.

## Why This Fork Is Better

### Built for current OpenClaw, not older plugin assumptions

- modern `definePluginEntry(...)` plugin entrypoint
- registered as a real OpenClaw `memory` runtime, not just a legacy tool bundle
- strict manifest/schema aligned with the current plugin contract

### Updated vendored MemU baseline

- vendored upstream rebased to `memU v1.5.1`
- Python runtime moved to `3.13+`
- dependency floor updated for the newer MemU runtime

### Safer upgrades for existing users

- verified compatibility path for older SQLite memory databases
- verified migration path for older storage layouts
- backup-first migration helpers included in-repo

### Better operational behavior

- cleaner plugin/runtime separation
- current SecretRef-aware config surface
- modernized packaging and local release flow
- packed and installable as a local `.tgz` release artifact

## What This Plugin Does

`memu-engine` turns OpenClaw session logs and selected workspace Markdown into structured, searchable memory.

- It extracts profiles, events, knowledge, skills, and behavioral signals from conversations.
- It stores memory locally in SQLite-backed MemU databases.
- It supports per-agent memory isolation plus a shared document store.
- It registers as an OpenClaw `memory` plugin, so agents can use `memory_search` and `memory_get`.
- It keeps compatibility aliases for existing users: `memu_search`, `memu_get`, `memu_flush`.

## What's New in This Fork

Current release baseline: `v0.4.0`

- Vendored MemU runtime rebased to `v1.5.1`
- Python baseline raised to `3.13+`
- plugin entry moved to `definePluginEntry(...)`
- memory runtime registered through the modern OpenClaw memory-plugin API
- strict manifest/schema in [openclaw.plugin.json](openclaw.plugin.json)
- updated config surface for:
  - `dataDir`
  - `userId`
  - `flushOnCompaction`
  - `debugTiming`
  - SecretRef-compatible `embedding.apiKey` and `extraction.apiKey`
- safer vendoring workflow in [update_from_upstream.sh](update_from_upstream.sh)
- Telegram topic transcript support for OpenClaw sessions stored as `<session_id>-topic-*.jsonl`
- more robust runtime bootstrap in mixed shell/systemd environments via logger compatibility fallback plus safer `pythonRoot` and `uv` resolution

## Requirements

- OpenClaw with plugin support
- `uv` in `PATH` or exposed via `MEMU_UV_BIN`
- Python `3.13+`
- Node `22.14.0+`

On first use, the plugin bootstraps its isolated Python environment under `python/.venv` using the locked dependencies in `python/uv.lock`.

## Important OpenClaw Setting

If you use `memu-engine` as the memory backend, keep native OpenClaw memory search disabled.

Do this:

```jsonc
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "enabled": false
      }
    }
  },
  "plugins": {
    "slots": {
      "memory": "memu-engine"
    }
  }
}
```

Do not enable both at the same time:

- OpenClaw native memory: `agents.defaults.memorySearch.enabled = true`
- `memu-engine` memory slot: `plugins.slots.memory = "memu-engine"`

Running both together gives you two competing memory systems and confusing retrieval behavior.

## Install

If you publish this fork under a different package name, replace `memu-engine` in the commands below with your published package name.

### From a published package

```bash
openclaw plugins install memu-engine
```

### From a local checkout

```bash
openclaw plugins install -l /abs/path/to/memu-engine-for-OpenClaw
```

### From a locally packed tarball

After running `npm pack`, install the generated `.tgz`:

```bash
openclaw plugins install /abs/path/to/memu-engine-0.4.0.tgz
```

### Restart OpenClaw

```bash
openclaw gateway restart
```

## Quick Start Configuration

Minimal single-agent setup:

```jsonc
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "enabled": false
      }
    }
  },
  "plugins": {
    "slots": {
      "memory": "memu-engine"
    },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "embedding": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "${OPENAI_API_KEY}",
            "model": "text-embedding-3-small"
          },
          "extraction": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "${OPENAI_API_KEY}",
            "model": "gpt-4o-mini"
          },
          "language": "en"
        }
      }
    }
  }
}
```

Minimal multi-agent setup:

```jsonc
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "enabled": false
      }
    }
  },
  "plugins": {
    "slots": {
      "memory": "memu-engine"
    },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "agentSettings": {
            "main": {
              "memoryEnabled": true,
              "searchEnabled": true,
              "searchableStores": ["self", "shared", "research"]
            },
            "research": {
              "memoryEnabled": true,
              "searchEnabled": true,
              "searchableStores": ["self", "shared"]
            }
          }
        }
      }
    }
  }
}
```

Behavior in the example above:

- Telegram topics are supported: session transcripts named like `<session_id>-topic-1.jsonl` are discovered and ingested under the owning session ID
- `main` can search its own memory, the shared store, and `research`
- `research` can search only itself plus the shared store
- both agents still write to separate DBs

## Storage Layout

The current runtime layout is:

```text
~/.openclaw/memUdata/
├── conversations/
├── resources/
├── memory/
│   ├── shared/memu.db
│   ├── main/memu.db
│   └── <agent>/memu.db
└── state/
```

This layout is easier to back up, inspect, and migrate than the older mixed layouts.

## Upgrade and Compatibility

This fork includes compatibility work for older `memu-engine` deployments.

### Verified compatibility

The local regression checks validate:

- legacy single SQLite DB migration from `memu.db` into `memory/<agent>/memu.db`
- preservation of old compatibility columns such as `agentName`
- creation and population of new `agent_id` columns
- migration of old flat storage-layout files:
  - legacy conversations
  - sync state files
  - shared resources
  - older memory root DBs

### Upgrade recommendation

Before replacing an older install:

1. Back up `~/.openclaw/memUdata`
2. If you are upgrading from an older layout, run the storage migration helper once
3. Install the new plugin version
4. Restart OpenClaw
5. Smoke-test with `memory_search`

### Storage migration helper

```bash
cd /abs/path/to/memu-engine-for-OpenClaw
UV_CACHE_DIR=/tmp/memu-engine-uv-cache uv run --project python \
  python python/scripts/migrate_storage_layout.py --backup
```

### Vendored upstream update helper

The repository includes a fail-fast vendoring helper:

```bash
./update_from_upstream.sh
```

It is pinned to the current upstream baseline and is intended to keep OpenClaw-specific overlays intact while refreshing the vendored MemU runtime.

## Configuration Reference

The full parameter reference lives here:

- [MEMU_PARAMETERS.md](MEMU_PARAMETERS.md)

Highlights:

- `embedding`
- `extraction`
- `language`
- `dataDir`
- `userId`
- `memoryRoot`
- `ingest`
- `retrieval`
- `network.proxy`
- `flushOnCompaction`
- `debugTiming`
- `chunkSize`
- `chunkOverlap`
- `agentSettings`

Compatibility-only legacy keys still accepted during upgrades:

- `flushIdleSeconds`
- `maxMessagesPerPart`

These two legacy keys are accepted so older configs continue to validate during upgrade, but the current runtime no longer relies on them.

## API Key Setup

Recommended:

```jsonc
"embedding": {
  "provider": "openai",
  "baseUrl": "https://api.openai.com/v1",
  "apiKey": "${OPENAI_API_KEY}",
  "model": "text-embedding-3-small"
}
```

You can also use the same pattern for `extraction.apiKey`.

Supported forms:

1. `${OPENAI_API_KEY}`
2. full env-backed SecretRef
3. plain text string

Plain text works, but it is not recommended.

## Retrieval Modes

`memory_search` supports two retrieval modes:

- `fast`
  - lower latency
  - vector-focused retrieval
  - no LLM sufficiency checks
- `full`
  - passes recent session context
  - enables MemU route-intention and sufficiency checks
  - higher latency, richer retrieval behavior

Default tool output mode is `compact`.

## Local / OpenAI-Compatible Providers

If your provider exposes an OpenAI-compatible `/v1` interface, you can use it for both embedding and extraction.

Example:

```jsonc
{
  "embedding": {
    "provider": "openai",
    "baseUrl": "http://127.0.0.1:8000/v1",
    "apiKey": "${LOCAL_LLM_API_KEY}",
    "model": "text-embedding-3-small"
  },
  "extraction": {
    "provider": "openai",
    "baseUrl": "http://127.0.0.1:8000/v1",
    "apiKey": "${LOCAL_LLM_API_KEY}",
    "model": "gpt-4o-mini"
  }
}
```

## Troubleshooting

### `uv` not found

Install `uv` first:

https://docs.astral.sh/uv/

### Python bootstrap fails

Check:

- Python `3.13+` is available
- your network/proxy settings allow dependency downloads
- `network.proxy` is configured correctly if you need custom proxy behavior

### Memory conflict in OpenClaw

If memory results look duplicated or inconsistent, confirm that native OpenClaw memory is still disabled:

```jsonc
"agents": {
  "defaults": {
    "memorySearch": {
      "enabled": false
    }
  }
}
```

### Migration caution

If you are upgrading from an older layout, do not rely only on changing the plugin files in place.

Run the migration helper first if you have any of these:

- old flat conversations
- old `last_sync_ts` / `pending_ingest.json`
- old memory DBs outside `~/.openclaw/memUdata/memory`

## Development Notes

- vendored upstream baseline tracking: [python/UPSTREAM.md](python/UPSTREAM.md)
- plugin manifest: [openclaw.plugin.json](openclaw.plugin.json)
- local parameter reference: [MEMU_PARAMETERS.md](MEMU_PARAMETERS.md)

## License

Apache License 2.0

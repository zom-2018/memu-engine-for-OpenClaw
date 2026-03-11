<!-- markdownlint-disable MD013 MD031 MD024 MD009 MD012 MD032 MD040 MD034 -->

# memu-engine Parameters & Defaults

This document summarizes all configurable parameters currently supported by the `memu-engine` plugin, including:

- `openclaw.json` plugin config fields
- tool call parameters (`memory_search`, `memory_get`)
- default values
- whether each field is optional
- precedence/override rules

> After editing `~/.openclaw/openclaw.json`, run:
>
> `openclaw gateway restart`

---

## 1) `openclaw.json` configuration (plugin-level)

Location:

```json
{
  "plugins": {
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "...": "..."
        }
      }
    }
  }
}
```

---

### 1.1 `config.language`

- **Type**: `string`
- **Optional**: Yes
- **Default**: `"auto"`
- **Meaning**: output language for memory summaries (e.g. `"zh"`, `"en"`, or `"auto"`).

---

### 1.2 `config.embedding`

- **Type**: `object`
- **Optional**: Yes (but required in practice for working retrieval)
- **Default behavior** if fields missing:
  - `provider`: `"openai"`
  - `baseUrl`: `"https://api.openai.com/v1"`
  - `model`: `"text-embedding-3-small"`
  - `apiKey`: `""` (empty; usually causes failure if not set elsewhere)

Fields:

- `embedding.provider` (string)
- `embedding.apiKey` (string | SecretRef) - **Supports SecretRef since v0.2.6**
- `embedding.baseUrl` (string)
- `embedding.model` (string)

Used for vector embedding/retrieval.

#### API Key Configuration (v0.2.6+)

The `apiKey` field supports three formats:

1. **Environment Variable Template (Recommended)**:
   ```json
   "apiKey": "${OPENAI_API_KEY}"
   ```
   - Automatically resolves to environment variable
   - Safe to commit to git (only contains variable name)
   - Most convenient and secure method

2. **Full SecretRef Object**:
   ```json
   "apiKey": {
     "source": "env",
     "provider": "default",
     "id": "OPENAI_API_KEY"
   }
   ```
   - Equivalent to `${VAR}` syntax
   - Currently only `env` source is supported

3. **Plain Text** (not recommended):
   ```json
   "apiKey": "sk-..."
   ```
   - Shows security warning on startup
   - Risk of leaking if committed to git

**Environment Variable Fallback**: If `apiKey` is not configured, automatically falls back to `MEMU_EMBED_API_KEY` environment variable.

---

### 1.3 `config.extraction`

- **Type**: `object`
- **Optional**: Yes (but required in practice for LLM-based extraction/full mode)
- **Default behavior** if fields missing:
  - `provider`: `"openai"`
  - `baseUrl`: `"https://api.openai.com/v1"`
  - `model`: `"gpt-4o-mini"`
  - `apiKey`: `""` (empty; usually causes failure if not set elsewhere)

Fields:

- `extraction.provider` (string)
- `extraction.apiKey` (string | SecretRef) - **Supports SecretRef since v0.2.6**
- `extraction.baseUrl` (string)
- `extraction.model` (string)

Used by memory extraction and full-mode decision checks.

#### API Key Configuration (v0.2.6+)

Same as `embedding.apiKey` - supports environment variable template syntax, full SecretRef objects, and plain text. See section 1.2 for details.

**Environment Variable Fallback**: If `apiKey` is not configured, automatically falls back to `MEMU_CHAT_API_KEY` environment variable.

---

### 1.4 `config.ingest`

- **Type**: `object`
- **Optional**: Yes

#### `ingest.includeDefaultPaths`

- **Type**: `boolean`
- **Optional**: Yes
- **Default**: `true`
- **Meaning**: include default workspace markdown paths for ingestion.

#### `ingest.extraPaths`

- **Type**: `string[]`
- **Optional**: Yes
- **Default**: `[]`
- **Meaning**: additional directories/files to ingest.

#### `ingest.filterScheduledSystemMessages`

- **Type**: `boolean`
- **Optional**: Yes
- **Default**: `true`
- **Meaning**: when enabled, filters large system-injected scheduled payloads (for example cron-delivered long reports) before converting session logs into memU conversation parts.

#### `ingest.scheduledSystemMode`

- **Type**: `"event" | "drop" | "keep"`
- **Optional**: Yes
- **Default**: `"event"`
- **Meaning**:
  - `event`: replace matched payload with a compact event marker (recommended default)
  - `drop`: remove matched payload entirely
  - `keep`: keep original behavior (no conversion-time reduction for these payloads)

#### `ingest.scheduledSystemMinChars`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: `500`
- **Minimum**: `64`
- **Meaning**: minimum payload length for classifying a system envelope as a scheduled payload candidate.

> Design note: this filtering is intentionally generic (structure/size based), not tied to any specific keyword list, so it can be safely used in public/general-purpose plugin deployments.

---

### 1.5 `config.retrieval`

- **Type**: `object`
- **Optional**: Yes

#### `retrieval.mode`

- **Type**: `"fast" | "full"`
- **Optional**: Yes
- **Default**: `"fast"`
- **Meaning**:
  - `fast`: vector-focused retrieval (no route-intention/sufficiency checks)
  - `full`: memU progressive retrieval with route-intention and sufficiency checks

#### `retrieval.contextMessages`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: `3`
- **Valid range**: clamped to `0..20`
- **Meaning**: in `full` mode, number of recent session messages injected as retrieval context.

#### `retrieval.defaultCategoryQuota`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: not set (`null` internally)
- **Meaning**: default number of category results for `memory_search` when call does not pass `categoryQuota`.

#### `retrieval.defaultItemQuota`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: not set (`null` internally)
- **Meaning**: default number of item results for `memory_search` when call does not pass `itemQuota`.

#### `retrieval.outputMode`

- **Type**: `"compact" | "full"`
- **Optional**: Yes
- **Default**: `"compact"`
- **Meaning**:
  - `compact`: tool `content` only returns minimal `results[{path,snippet,agentName}]` for lower model token usage.
  - `full`: tool `content` returns full JSON envelope with `score/provider/model/fallback/citations`.

Note: debug metadata is still available in tool `details`.

---

### 1.6 Additional supported config keys (runtime-supported)

These are supported by plugin runtime logic, even if not strictly listed in `openclaw.plugin.json` schema.

#### `config.network.proxy`

- **Type**: `object`
- **Optional**: Yes
- **Default**:

  ```json
  {
    "mode": "inherit"
  }
  ```

- **Meaning**: controls which proxy environment variables are passed into `uv sync`, the background sync service, and one-shot Python helper scripts.

##### `network.proxy.mode`

- **Type**: `"inherit" | "plugin" | "none"`
- **Optional**: Yes
- **Default**: `"inherit"`
- **Meaning**:
  - `inherit`: keep the host/OpenClaw proxy env as-is (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, including lowercase variants)
  - `plugin`: ignore host proxy env and use only the explicit proxy fields below
  - `none`: clear standard proxy env vars before launching Python/uv child processes

In `plugin` mode, memu-engine injects both uppercase and lowercase proxy env names (for example `HTTP_PROXY` and `http_proxy`) for better compatibility across Python/network tooling. If `plugin` mode is selected without any proxy values, runtime logs a warning because the effective behavior becomes “launch without proxy env”.

##### `network.proxy.httpProxy`

- **Type**: `string`
- **Optional**: Yes
- **Used when**: `network.proxy.mode = "plugin"`
- **Meaning**: value injected as `HTTP_PROXY`

##### `network.proxy.httpsProxy`

- **Type**: `string`
- **Optional**: Yes
- **Used when**: `network.proxy.mode = "plugin"`
- **Meaning**: value injected as `HTTPS_PROXY`

##### `network.proxy.allProxy`

- **Type**: `string`
- **Optional**: Yes
- **Used when**: `network.proxy.mode = "plugin"`
- **Meaning**: value injected as `ALL_PROXY`; use this for SOCKS proxy URLs such as `socks5://127.0.0.1:7891`

##### `network.proxy.noProxy`

- **Type**: `string`
- **Optional**: Yes
- **Used when**: `network.proxy.mode = "plugin"`
- **Meaning**: value injected as `NO_PROXY`

Example:

```json
{
  "plugins": {
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "network": {
            "proxy": {
              "mode": "plugin",
              "httpProxy": "http://127.0.0.1:7890",
              "httpsProxy": "http://127.0.0.1:7890",
              "allProxy": "socks5://127.0.0.1:7891",
              "noProxy": "127.0.0.1,localhost"
            }
          }
        }
      }
    }
  }
}
```

> Design note: `mode="plugin"` is the safest choice when you want deterministic plugin behavior regardless of shell/global proxy env. `mode="none"` is useful when OpenClaw itself runs behind a proxy but you do not want memu-engine child processes to inherit it.

#### `config.userId`

- **Type**: `string`
- **Optional**: Yes
- **Default**: `"default"` (or env fallback)
- **Meaning**: user namespace for memory isolation.

#### `config.memoryRoot`

- **Type**: `string`
- **Optional**: Yes
- **Default**: `~/.openclaw/memUdata/memory`
- **Meaning**: root of the enforced hybrid storage layout (per-agent memory stores plus a shared document/chunk database).

The runtime resolves `config.memoryRoot` first, then the `MEMU_MEMORY_ROOT` environment variable, and finally defaults to `~/.openclaw/memUdata/memory`. Within this directory each agent writes its own `memu.db` (for example `{memoryRoot}/main/memu.db`) while a shared document store lives at `{memoryRoot}/shared/memu.db` with tables for shared documents and chunks that every agent can read.

High-level structure (names are illustrative, not enforced):
```
{memoryRoot}/
├── shared/            # shared document + chunk database
│   └── memu.db
├── main/              # default agent memory database
│   └── memu.db
└── <agentName>/       # additional agent memories follow the same pattern
    └── memu.db
```

When the legacy single-DB layout (`~/.openclaw/memUdata/memu.db` or similar) still exists, the sync scripts (`watch_sync.py` / `auto_sync.py`) invoke `migrate_legacy_single_db_to_agent_db` to move data into the hybrid layout (`memoryRoot/<agent>/memu.db`, defaulting to `main`) while keeping a timestamped backup of the original file.

Manual migration script (recommended once before first restart after upgrading storage layout):

```bash
uv run --project python python python/scripts/migrate_storage_layout.py --dry-run
uv run --project python python python/scripts/migrate_storage_layout.py --backup
```

The script consolidates legacy data/state/resources into the unified `~/.openclaw/memUdata` layout and writes a migration marker at `~/.openclaw/memUdata/state/layout_migration.json`.

#### `config.chunkSize`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: `512`
- **Valid range**: `1..2048`
- **Meaning**: Maximum characters per document chunk before creating a new part for embedding and retrieval.

#### `config.chunkOverlap`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: `50`
- **Valid range**: `>= 0` and `< chunkSize`
- **Meaning**: Characters shared between consecutive chunks to preserve cross-boundary context.

**Validation**: `chunkOverlap >= chunkSize` will cause startup validation to reject the configuration, ensuring each chunk remains bounded.

#### `config.flushOnCompaction`

- **Type**: `boolean`
- **Optional**: Yes
- **Default**: `false`
- **Meaning**: if true, registers compaction hook to run `memory_flush` behavior.

---

### 1.7 Multi-agent memory config

#### `config.enabledAgents`

- **Type**: `string[]`
- **Optional**: Yes
- **Default**: `['main']`
- **Meaning**: agent list participating in ingestion/retrieval.
- **Validation**: each name should match `^[a-z][a-z0-9_-]*$`.
- **Runtime normalization**: if `main` is missing, runtime automatically prepends `main`.

#### `config.allowCrossAgentRetrieval`

- `allowCrossAgentRetrieval` is deprecated and kept only for backward compatibility.

- **Type**: `boolean`
- **Optional**: Yes
- **Default**: `false`
- **Status**: **Deprecated** (kept for backward compatibility)
- **Meaning**:
  - `false`: runtime maps legacy behavior to `agentSettings.<agent>.searchableStores = ['self']`
  - `true`: runtime maps legacy behavior to `agentSettings.<agent>.searchableStores = ['self', 'shared']`

> Migration guide: prefer `agentSettings.<agent>.searchableStores` for explicit per-agent retrieval scope.
>
> - `allowCrossAgentRetrieval=false` →
>   ```json
>   "agentSettings": {
>     "main": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self"] }
>   }
>   ```
> - `allowCrossAgentRetrieval=true` →
>   ```json
>   "agentSettings": {
>     "main": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self", "shared"] }
>   }
>   ```
>
> If both old and new config are present, `agentSettings` takes precedence.

#### `config.agentConfigs`

- **Type**: `Record<string, object>`
- **Optional**: Yes
- **Default**: `{}`
- **Meaning**: reserved per-agent extension container (forward-compatible; currently not required for basic multi-agent behavior).

---
 
### 1.8 `config.agentSettings`

- **Type**: `Record<string, AgentConfig>`
- **Optional**: Yes
- **Default**: `{}` (runtime applies default policy for every agent)
- **Meaning**: per-agent memory/search policy overrides keyed by agent name.

Each entry in `agentSettings` follows the `AgentConfig` shape defined below. Runtime normalization always ensures `main` is present even if the config omits it, and any missing fields fall back to the defaults listed here.

#### `agentSettings.<agentName>.memoryEnabled`

- **Type**: `boolean`
- **Optional**: Yes
- **Default**: `true`
- **Meaning**: controls whether the plugin writes/updates structured memory for this agent's sessions. When `false`, ingestion is skipped for that agent.

#### `agentSettings.<agentName>.searchEnabled`

- **Type**: `boolean`
- **Optional**: Yes
- **Default**: `true`
- **Meaning**: allows the agent to issue `memory_search` calls. Setting it to `false` blocks the agent from searching even if a tool call is made.

#### `agentSettings.<agentName>.searchableStores`

- **Type**: `string[]`
- **Optional**: Yes
- **Default**: `['self']`
- **Meaning**: list of storage targets that `memory_search` queries when this agent runs searches.
- **Accepted values**: `self`, `shared`, or the literal name of another configured agent. `self` is resolved at runtime to the requesting agent's store, so the same config works regardless of which agent triggered the search.

Adding `shared` lets an agent reach the shared document store, and naming other agents enables cross-agent retrieval directly via per-agent policy.


## 2) Tool parameters (call-level)

## 2.1 `memory_search` / `memu_search`

Input:

- `query` (**required**, `string`)
- `maxResults` (optional, `integer`, default: `10`)
- `minScore` (optional, `number`, default: `0.0`)
- `categoryQuota` (optional, `integer`)
- `itemQuota` (optional, `integer`)
- `agentName` (optional, `string`, default: `main`). Determines which agent identity and `agentSettings` policy the call runs under; `self` in the requested stores list is resolved to this agent.
- `crossAgent` (optional, `boolean`, legacy compatibility flag)

Output: JSON envelope

```json
{
  "results": [
    {
      "path": "memu://...",
      "startLine": 1,
      "endLine": 1,
      "score": 0.73,
      "snippet": "...",
      "source": "memory",
      "agentName": "main"
    },
    {
      "path": "memu://shared/document/123",
      "startLine": 1,
      "endLine": 1,
      "score": 0.62,
      "snippet": "shared doc",
      "source": "document",
      "agentName": "shared"
    }
  ],
  "provider": "...",
  "model": "...",
  "fallback": null,
  "citations": "off"
}
```

Agent Scoping: Each search runs using the specified `agentName` (default `main`) and honors that agent's `agentSettings` (searchable stores, searchEnabled, etc.). The runtime replaces `self` in the requested stores with the resolved agent name and tags every result with the `agentName` that produced it so you can tell whether the memory came from the requested agent, another agent, or the shared store (`"shared"`, for example).

Execution details also include debug fields (`mode`, `contextCount`, etc.).

`retrieval.outputMode` controls what is placed in tool `content`:

- `compact` => `{ "results": [{"path","snippet","agentName"}, ...] }`
- `full` => full envelope with score/model/provider metadata

---

## 2.2 `memory_get` / `memu_get`

Input:

- `path` (**required**, `string`)
- `from` (optional, `integer`, **1-based**, default: `1`)
- `lines` (optional, `integer`, default: all remaining lines)

Output:

```json
{
  "path": "...",
  "text": "..."
}
```

Back-compat in script still accepts `--offset/--limit` internally.

---

## 3) Quota precedence rules

For category/item counts in `memory_search`, precedence is:

1. **Call-level args**: `categoryQuota`, `itemQuota`
2. **Plugin defaults**: `retrieval.defaultCategoryQuota`, `retrieval.defaultItemQuota`
3. **Auto strategy** (built-in fallback):
   - `maxResults >= 10`: category ~3 (or 4 when very large), rest item
   - smaller result sets: proportionally fewer categories

If explicit quotas exceed `maxResults`, quotas are scaled down to fit.

---

## 4) Fast vs Full mode behavior

- `fast`: lower latency, retrieval-focused.
- `full`: richer reasoning path (more LLM decision steps), usually slower.

`categoryQuota/itemQuota` apply in **both** modes, because they are output-assembly controls after retrieval.

---

## 5) Recommended starter config

```json
{
  "plugins": {
    "slots": {
      "memory": "memu-engine"
    },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "language": "zh",
          "embedding": {
            "provider": "openai",
            "apiKey": "${OPENAI_API_KEY}",
            "baseUrl": "https://api.siliconflow.cn/v1",
            "model": "BAAI/bge-m3"
          },
          "extraction": {
            "provider": "openai",
            "apiKey": "${OPENAI_API_KEY}",
            "baseUrl": "https://your-chat-endpoint/v1",
            "model": "your-chat-model"
          },
          "ingest": {
            "includeDefaultPaths": true,
            "extraPaths": [],
            "filterScheduledSystemMessages": true,
            "scheduledSystemMode": "event",
            "scheduledSystemMinChars": 500
          },
          "retrieval": {
            "mode": "fast",
            "contextMessages": 3,
            "defaultCategoryQuota": 3,
            "defaultItemQuota": 7,
            "outputMode": "compact"
          },
          "chunkSize": 512,
          "chunkOverlap": 50,
          "enabledAgents": ["main", "prometheus", "librarian"],
          "agentSettings": {
            "main": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self"] },
            "prometheus": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self"] },
            "librarian": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self"] }
          }
        }
      }
    }
  }
}
```

### 5.1 `openclaw.json` (ULW) multi-agent templates

Single-agent compatibility mode (default behavior):

```json
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
          "config": {
            "enabledAgents": ["main"],
            "agentSettings": {
              "main": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self"] }
            },
            "chunkSize": 512,
            "chunkOverlap": 50
          }
      }
    }
  }
}
```

Multi-agent isolated retrieval mode (recommended first step):

```json
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
          "config": {
            "enabledAgents": ["main", "prometheus", "librarian"],
            "agentSettings": {
              "main": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self"] },
              "prometheus": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self"] },
              "librarian": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self"] }
            },
            "chunkSize": 512,
            "chunkOverlap": 50
          }
      }
    }
  }
}
```

Multi-agent shared retrieval mode:

```json
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
          "config": {
            "enabledAgents": ["main", "prometheus", "librarian"],
            "agentSettings": {
              "main": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self", "shared"] },
              "prometheus": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self", "shared"] },
              "librarian": { "memoryEnabled": true, "searchEnabled": true, "searchableStores": ["self", "shared"] }
            },
            "chunkSize": 512,
            "chunkOverlap": 50
          }
      }
    }
  }
}
```

**Setup (one-time):**
```bash
# Set environment variable permanently
echo 'export OPENAI_API_KEY="sk-your-actual-key"' >> ~/.bashrc
source ~/.bashrc

# Verify
echo $OPENAI_API_KEY
```

**Why use `${VAR}` syntax?**
- ✅ Config file only contains variable name - safe to commit to git
- ✅ Actual API key stays in environment variable - never exposed
- ✅ Different environments can use different keys
- ✅ Follows 12-Factor App best practices

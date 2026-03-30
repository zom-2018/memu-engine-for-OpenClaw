import { definePluginEntry, type OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { execFileSync } from "node:child_process";

interface NormalizedConfig {
  enabledAgents: string[];
  agentSettings: Record<
    string,
    {
      memoryEnabled: boolean;
      searchEnabled: boolean;
      searchableStores: string[];
    }
  >;
  chunkSize: number;
  chunkOverlap: number;
  network: {
    proxy: {
      mode: "inherit" | "plugin" | "none";
      httpProxy?: string;
      httpsProxy?: string;
      allProxy?: string;
      noProxy?: string;
    };
  };
  [key: string]: any;
}

let warnedAllowCrossAgentRetrievalDeprecation = false;

const PROXY_ENV_KEYS = [
  "HTTP_PROXY",
  "HTTPS_PROXY",
  "ALL_PROXY",
  "NO_PROXY",
  "http_proxy",
  "https_proxy",
  "all_proxy",
  "no_proxy",
] as const;

type ProxyMode = "inherit" | "plugin" | "none";

type ProxyConfig = {
  mode: ProxyMode;
  httpProxy?: string;
  httpsProxy?: string;
  allProxy?: string;
  noProxy?: string;
};

function normalizeOptionalString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function normalizeProxyConfig(config: any): ProxyConfig {
  const rawProxy = config?.network?.proxy;
  const rawMode = typeof rawProxy?.mode === "string" ? rawProxy.mode.trim().toLowerCase() : "inherit";
  const mode: ProxyMode = rawMode === "plugin" || rawMode === "none" ? rawMode : "inherit";

  if (rawProxy?.mode && mode !== rawMode) {
    console.warn(
      `[memu-engine] Invalid network.proxy.mode='${String(rawProxy.mode)}'. ` +
        "Expected 'inherit', 'plugin', or 'none'. Falling back to 'inherit'."
    );
  }

  return {
    mode,
    httpProxy: normalizeOptionalString(rawProxy?.httpProxy),
    httpsProxy: normalizeOptionalString(rawProxy?.httpsProxy),
    allProxy: normalizeOptionalString(rawProxy?.allProxy),
    noProxy: normalizeOptionalString(rawProxy?.noProxy),
  };
}

function withoutProxyEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const sanitized: NodeJS.ProcessEnv = { ...env };
  for (const key of PROXY_ENV_KEYS) {
    delete sanitized[key];
  }
  return sanitized;
}

function applyProxyEnv(baseEnv: NodeJS.ProcessEnv, proxyConfig: ProxyConfig): NodeJS.ProcessEnv {
  if (proxyConfig.mode === "inherit") {
    return { ...baseEnv };
  }

  const env = withoutProxyEnv(baseEnv);
  if (proxyConfig.mode === "none") {
    return env;
  }

  const configuredProxyCount = [
    proxyConfig.httpProxy,
    proxyConfig.httpsProxy,
    proxyConfig.allProxy,
    proxyConfig.noProxy,
  ].filter((value) => typeof value === "string" && value.length > 0).length;

  if (configuredProxyCount === 0) {
    console.warn(
      "[memu-engine] network.proxy.mode='plugin' is set but no proxy values were provided. " +
        "Python child processes will run without standard proxy env vars. " +
        "Use mode='none' for explicit no-proxy behavior, or configure network.proxy.httpProxy/httpsProxy/allProxy/noProxy."
    );
  }

  if (proxyConfig.httpProxy) {
    env.HTTP_PROXY = proxyConfig.httpProxy;
    env.http_proxy = proxyConfig.httpProxy;
  }
  if (proxyConfig.httpsProxy) {
    env.HTTPS_PROXY = proxyConfig.httpsProxy;
    env.https_proxy = proxyConfig.httpsProxy;
  }
  if (proxyConfig.allProxy) {
    env.ALL_PROXY = proxyConfig.allProxy;
    env.all_proxy = proxyConfig.allProxy;
  }
  if (proxyConfig.noProxy) {
    env.NO_PROXY = proxyConfig.noProxy;
    env.no_proxy = proxyConfig.noProxy;
  }
  return env;
}

function buildPythonProcessEnv(
  normalizedConfig: NormalizedConfig,
  extraEnv: Record<string, string>
): NodeJS.ProcessEnv {
  return {
    ...applyProxyEnv(process.env, normalizedConfig.network.proxy),
    ...extraEnv,
  };
}

function normalizeAgentSettings(config: any): Record<string, { memoryEnabled: boolean; searchEnabled: boolean; searchableStores: string[] }> {
  const raw = config?.agentSettings;
  const out: Record<string, { memoryEnabled: boolean; searchEnabled: boolean; searchableStores: string[] }> = {};
  if (raw && typeof raw === "object") {
    for (const [name, value] of Object.entries(raw)) {
      if (typeof name !== "string" || !name.trim()) continue;
      const cfg = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
      const stores = Array.isArray(cfg.searchableStores)
        ? cfg.searchableStores.filter((v): v is string => typeof v === "string" && v.trim().length > 0)
        : ["self"];
      out[name] = {
        memoryEnabled: cfg.memoryEnabled !== false,
        searchEnabled: cfg.searchEnabled !== false,
        searchableStores: stores.length > 0 ? stores : ["self"],
      };
    }
  }
  return out;
}

function normalizeConfig(config: any): NormalizedConfig {
  const validAgentPattern = /^[a-z][a-z0-9_-]*$/;
  const oldEnabledAgents = Array.isArray(config.enabledAgents)
    ? config.enabledAgents.filter((a: unknown): a is string => typeof a === "string" && a.trim().length > 0)
    : [];
  const agentSettings = normalizeAgentSettings(config);
  const enabledAgents = Array.from(new Set(["main", ...Object.keys(agentSettings), ...oldEnabledAgents]));

  if (!enabledAgents.includes("main")) {
    enabledAgents.unshift("main");
  }
  const invalidAgents = enabledAgents.filter((a: string) => !validAgentPattern.test(a));
  if (invalidAgents.length > 0) {
    console.warn(`[memu-engine] Invalid agent names: ${invalidAgents.join(", ")}`);
  }

  const rawStorageMode = typeof config.storageMode === "string" ? config.storageMode.trim().toLowerCase() : "hybrid";
  if (rawStorageMode && rawStorageMode !== "hybrid") {
    console.warn(
      `[memu-engine] storageMode='${rawStorageMode}' is deprecated and ignored. ` +
        "memu-engine now always runs in hybrid mode."
    );
  }

  const hasLegacyAllowCrossAgentRetrieval = typeof config.allowCrossAgentRetrieval === "boolean";
  if (hasLegacyAllowCrossAgentRetrieval && !warnedAllowCrossAgentRetrievalDeprecation) {
    const legacyValue = config.allowCrossAgentRetrieval === true;
    console.warn(
      `[memu-engine] 'allowCrossAgentRetrieval' is deprecated and will be removed in a future release. ` +
        `Migration guide: set per-agent 'agentSettings.<agent>.searchableStores' instead. ` +
        `Equivalent mapping: allowCrossAgentRetrieval=${legacyValue} -> searchableStores=${
          legacyValue ? "['self','shared']" : "['self']"
        }. ` +
        `When both old and new config are present, agentSettings takes precedence.`
    );
    warnedAllowCrossAgentRetrievalDeprecation = true;
  }

  const normalizedAgentSettings: Record<
    string,
    {
      memoryEnabled: boolean;
      searchEnabled: boolean;
      searchableStores: string[];
    }
  > = { ...agentSettings };

  if (hasLegacyAllowCrossAgentRetrieval) {
    const legacyStores = config.allowCrossAgentRetrieval === true ? ["self", "shared"] : ["self"];
    for (const agentName of enabledAgents) {
      if (!normalizedAgentSettings[agentName]) {
        normalizedAgentSettings[agentName] = {
          memoryEnabled: true,
          searchEnabled: true,
          searchableStores: legacyStores,
        };
      }
    }
  }

  const officialMemorySearchEnabled = config?.agents?.defaults?.memorySearch?.enabled === true;
  const memuMemorySlotActive = config?.plugins?.slots?.memory === "memu-engine";
  if (officialMemorySearchEnabled && memuMemorySlotActive) {
    console.warn(
      `[memu-engine] Official Memory System Conflict detected: both OpenClaw official memory (` +
        `agents.defaults.memorySearch.enabled=true) and memu-engine memory slot (` +
        `plugins.slots.memory="memu-engine") are active at the same time. ` +
        `This enables two memory systems simultaneously and can produce confusing retrieval behavior. ` +
        `Recommended fix: disable the official memory system and keep memu-engine as the only memory backend. ` +
        `Exact change in openclaw.json: set "agents.defaults.memorySearch.enabled": false.`
    );
  }

  const rawChunkSize = config.chunkSize ?? 512;
  const chunkSizeNum = Number(rawChunkSize);
  if (!Number.isFinite(chunkSizeNum) || !Number.isInteger(chunkSizeNum) || chunkSizeNum <= 0 || chunkSizeNum > 2048) {
    throw new Error(
      `[memu-engine] Invalid chunkSize '${String(rawChunkSize)}'. Expected integer in range 1..2048.`
    );
  }

  const rawChunkOverlap = config.chunkOverlap ?? 50;
  const chunkOverlapNum = Number(rawChunkOverlap);
  if (!Number.isFinite(chunkOverlapNum) || !Number.isInteger(chunkOverlapNum) || chunkOverlapNum < 0) {
    throw new Error(
      `[memu-engine] Invalid chunkOverlap '${String(rawChunkOverlap)}'. Expected integer >= 0.`
    );
  }
  if (chunkOverlapNum >= chunkSizeNum) {
    throw new Error(
      `[memu-engine] Invalid chunkOverlap '${chunkOverlapNum}'. chunkOverlap must be less than chunkSize (${chunkSizeNum}).`
    );
  }

  return {
    ...config,
    enabledAgents,
    agentSettings: normalizedAgentSettings,
    storageMode: "hybrid",
    chunkSize: chunkSizeNum,
    chunkOverlap: chunkOverlapNum,
    network: {
      proxy: normalizeProxyConfig(config),
    },
  };
}

// ============================================================================
// SecretRef Types (aligned with OpenClaw SDK)
// ============================================================================
type SecretRefSource = "env" | "file" | "exec";

type SecretRef = {
  source: SecretRefSource;
  provider: string;
  id: string;
};

type SecretInput = string | SecretRef;

function isSecretRef(value: unknown): value is SecretRef {
  return (
    typeof value === "object" &&
    value !== null &&
    "source" in value &&
    "provider" in value &&
    "id" in value &&
    typeof (value as any).source === "string" &&
    typeof (value as any).provider === "string" &&
    typeof (value as any).id === "string"
  );
}

// ============================================================================
// SecretRef Resolution Helpers
// ============================================================================

// Track warnings to avoid duplicate messages (only warn once per session)
const warnedSecretRefFailures = new Set<string>();

// Regex to match OpenClaw's env template syntax: ${VAR_NAME}
// Matches uppercase letters, digits, and underscores (1-128 chars)
const ENV_SECRET_TEMPLATE_RE = /^\$\{([A-Z][A-Z0-9_]{0,127})\}$/;

/**
 * Parse OpenClaw's simplified env template syntax: "${VAR_NAME}"
 * Returns a SecretRef object if the string matches the pattern, null otherwise.
 * 
 * Example: "${OPENAI_API_KEY}" -> {source: "env", provider: "default", id: "OPENAI_API_KEY"}
 */
function parseEnvTemplateSecretRef(value: unknown): SecretRef | null {
  if (typeof value !== "string") return null;
  const match = ENV_SECRET_TEMPLATE_RE.exec(value.trim());
  if (!match) return null;
  return {
    source: "env",
    provider: "default",
    id: match[1],
  };
}

/**
 * Resolve a SecretInput (string or SecretRef) to a plain string.
 * For SecretRef, attempts basic resolution based on source type.
 * Returns undefined if input is undefined or resolution fails.
 * 
 * Supports three input formats:
 * 1. Plain string: "sk-..." (backward compatible)
 * 2. Env template: "${OPENAI_API_KEY}" (OpenClaw simplified syntax)
 * 3. Full SecretRef: {source: "env", provider: "default", id: "OPENAI_API_KEY"}
 */
async function resolveMaybeSecretString(
  input: SecretInput | undefined,
  context: { keyName: string; envFallback?: string }
): Promise<{
  value: string;
  source: "plaintext" | "secretref" | "env-template" | "env-fallback" | "runtime-env";
  matchedEnvVarName?: string;
} | undefined> {
  if (!input) {
    return undefined;
  }

  // Case 1: Check for env template syntax first (before treating as plain string)
  if (typeof input === "string") {
    const envTemplate = parseEnvTemplateSecretRef(input);
    if (envTemplate) {
      // Treat as SecretRef
      try {
        const resolved = await resolveSecretRef(envTemplate);
        if (resolved) {
          return { value: resolved, source: "env-template" };
        }
      } catch (error) {
        const refKey = `${envTemplate.source}:${envTemplate.provider}:${envTemplate.id}`;
        if (!warnedSecretRefFailures.has(refKey)) {
          console.warn(
            `[memu-engine] Failed to resolve env template for ${context.keyName}: ${input}. ` +
            `Error: ${error instanceof Error ? error.message : String(error)}. ` +
            (context.envFallback ? `Falling back to environment variable ${context.envFallback}.` : "No fallback available.")
          );
          warnedSecretRefFailures.add(refKey);
        }
        // Fall through to return undefined (will trigger fallback)
      }
      return undefined;
    }
    
    const matchedEnvVarName = findMatchingEnvVarName(input);
    if (matchedEnvVarName) {
      return {
        value: input,
        source: "runtime-env",
        matchedEnvVarName,
      };
    }

    // Not an env template, treat as plain string
    return { value: input, source: "plaintext" };
  }

  // Case 2: Full SecretRef object - attempt resolution
  if (isSecretRef(input)) {
    try {
      const resolved = await resolveSecretRef(input);
      if (resolved) {
        return { value: resolved, source: "secretref" };
      }
    } catch (error) {
      const refKey = `${input.source}:${input.provider}:${input.id}`;
      if (!warnedSecretRefFailures.has(refKey)) {
        console.warn(
          `[memu-engine] Failed to resolve SecretRef for ${context.keyName}: ${refKey}. ` +
          `Error: ${error instanceof Error ? error.message : String(error)}. ` +
          (context.envFallback ? `Falling back to environment variable ${context.envFallback}.` : "No fallback available.")
        );
        warnedSecretRefFailures.add(refKey);
      }
    }
  }

  return undefined;
}

/**
 * Basic SecretRef resolver (simplified version without full OpenClaw config dependency).
 * Supports env source only for now. File and exec sources would require full SDK integration.
 */
async function resolveSecretRef(ref: SecretRef): Promise<string | undefined> {
  if (ref.source === "env") {
    // For env source, the 'id' field contains the environment variable name
    const value = process.env[ref.id];
    if (value) {
      return value;
    }
    throw new Error(`Environment variable ${ref.id} not found`);
  }

  // File and exec sources require full OpenClaw SDK integration
  // For now, we don't support them in this simplified implementation
  throw new Error(
    `SecretRef source '${ref.source}' is not supported yet. ` +
    `Only 'env' source is currently supported. ` +
    `Please use environment variables or plain text API keys.`
  );
}

function findMatchingEnvVarName(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;

  for (const [envName, envValue] of Object.entries(process.env)) {
    if (typeof envValue !== "string") continue;
    if (!envValue) continue;
    if (envValue === trimmed) {
      return envName;
    }
  }

  return undefined;
}

/**
 * Resolve API key with fallback priority:
 * 1. Config value (SecretRef or plaintext)
 * 2. Environment variable (if provided)
 * 3. Empty string (will cause Python script to fail with clear error)
 */
async function resolveApiKeyWithFallback(
  configValue: SecretInput | undefined,
  envVarName: string,
  keyName: string
): Promise<string> {
  // Try to resolve from config (SecretRef or plaintext)
  const resolved = await resolveMaybeSecretString(configValue, {
    keyName,
    envFallback: envVarName,
  });

  if (resolved) {
    // Do not warn on direct string values here. By the time plugin config reaches the
    // runtime, OpenClaw may already have resolved env-template/SecretRef values into
    // ordinary strings, so warning here would produce false positives.
    return resolved.value;
  }

  // Fallback to environment variable
  const envValue = process.env[envVarName];
  if (envValue) {
    return envValue;
  }

  // No value available - return empty string (Python will handle the error)
  return "";
}

// ============================================================================
// Other Types
// ============================================================================
type PythonBootstrapResult = {
  ok: boolean;
  reason?: string;
};

type MemorySearchResult = {
  path: string;
  startLine: number;
  endLine: number;
  score: number;
  snippet: string;
  source: "memory" | "sessions";
  citation?: string;
};

type MemoryEmbeddingProbeResult = {
  ok: boolean;
  error?: string;
};

type MemoryProviderStatus = {
  backend: "builtin" | "qmd";
  provider: string;
  model?: string;
  requestedProvider?: string;
  workspaceDir?: string;
  dbPath?: string;
  extraPaths?: string[];
  sources?: Array<"memory" | "sessions">;
  custom?: Record<string, unknown>;
};

type MemorySyncProgressUpdate = {
  completed: number;
  total: number;
  label?: string;
};

type RegisteredMemorySearchManager = {
  search(
    query: string,
    opts?: { maxResults?: number; minScore?: number; sessionKey?: string }
  ): Promise<MemorySearchResult[]>;
  readFile(params: { relPath: string; from?: number; lines?: number }): Promise<{ text: string; path: string }>;
  status(): MemoryProviderStatus;
  sync?(params?: {
    reason?: string;
    force?: boolean;
    sessionFiles?: string[];
    progress?: (update: MemorySyncProgressUpdate) => void;
  }): Promise<void>;
  probeEmbeddingAvailability(): Promise<MemoryEmbeddingProbeResult>;
  probeVectorAvailability(): Promise<boolean>;
  close?(): Promise<void>;
};

const memuEnginePlugin = definePluginEntry({
  id: "memu-engine",
  name: "memU Agentic Engine",
  description: "memU agentic memory layer (SQLModel + Vector)",
  kind: "memory",

  register(api: OpenClawPluginApi) {
    const apiAny = api as any;
    const logger = apiAny.logger || console;
    const logInfo = (message: string): void => {
      if (typeof (logger as { info?: unknown }).info === "function") {
        (logger as { info: (msg: string) => void }).info(message);
        return;
      }
      if (typeof (logger as { log?: unknown }).log === "function") {
        (logger as { log: (msg: string) => void }).log(message);
        return;
      }
      console.log(message);
    };
    const resolvePythonRoot = (): string => {
      const candidates = [
        typeof apiAny.resolvePath === "function" ? apiAny.resolvePath("python") : undefined,
        path.join(__dirname, "python"),
        path.join(os.homedir(), ".openclaw", "extensions", "memu-engine", "python"),
      ].filter((value): value is string => typeof value === "string" && value.trim().length > 0);

      for (const candidate of candidates) {
        try {
          if (fs.existsSync(candidate) && fs.existsSync(path.join(candidate, "pyproject.toml"))) {
            return candidate;
          }
        } catch {
          // try next candidate
        }
      }

      return candidates[0] ?? path.join(os.homedir(), ".openclaw", "extensions", "memu-engine", "python");
    };
    const pythonRoot = resolvePythonRoot();
    const resolveUvBinary = (): string => {
      const candidates = [
        process.env.MEMU_UV_BIN,
        process.env.UV_BIN,
        path.join(os.homedir(), ".local", "bin", "uv"),
        "uv",
      ].filter((value): value is string => typeof value === "string" && value.trim().length > 0);

      for (const candidate of candidates) {
        if (candidate === "uv") return candidate;
        try {
          fs.accessSync(candidate, fs.constants.X_OK);
          return candidate;
        } catch {
          // try next candidate
        }
      }

      return "uv";
    };
    const uvBinary = resolveUvBinary();
    let pythonBootstrapResult: PythonBootstrapResult | null = null;
    const memoryManagerCache = new Map<string, RegisteredMemorySearchManager>();

    const ensurePythonRuntime = (pluginConfig?: any): PythonBootstrapResult => {
      if (pythonBootstrapResult) return pythonBootstrapResult;

      const normalizedConfig = normalizeConfig(pluginConfig || api.pluginConfig || {});

      try {
        execFileSync(uvBinary, ["--version"], { stdio: "ignore" });
      } catch {
        pythonBootstrapResult = {
          ok: false,
          reason:
            `\`uv\` is required but not available (resolved path: ${uvBinary}). ` +
            "Install uv first or set MEMU_UV_BIN to the absolute binary path: https://docs.astral.sh/uv/",
        };
        return pythonBootstrapResult;
      }

      try {
        // Ensure an isolated runtime and dependency set for this plugin.
        // This avoids relying on system python (often 3.10) and prevents ABI mismatches.
        execFileSync(uvBinary, ["sync", "--project", pythonRoot, "--frozen"], {
          cwd: pythonRoot,
          env: buildPythonProcessEnv(normalizedConfig, {
            UV_LINK_MODE: process.env.UV_LINK_MODE || "copy",
          }),
          stdio: "ignore",
        });

        // Validate runtime compatibility up front (MemU requires Python >= 3.13).
        execFileSync(
          uvBinary,
          [
            "run",
            "--project",
            pythonRoot,
            "python",
            "-c",
            "import sys; assert sys.version_info >= (3, 13), sys.version; import memu",
          ],
          {
            cwd: pythonRoot,
            env: buildPythonProcessEnv(normalizedConfig, {}),
            stdio: "ignore",
          }
        );

        pythonBootstrapResult = { ok: true };
        return pythonBootstrapResult;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        pythonBootstrapResult = {
          ok: false,
          reason:
            "Failed to bootstrap isolated Python runtime via `uv sync --project python --frozen`. " +
            `pythonRoot=${pythonRoot}. uv=${uvBinary}. Detail: ${msg}`,
        };
        return pythonBootstrapResult;
      }
    };

    const computeExtraPaths = (pluginConfig: any, workspaceDir: string): string[] => {
      const ingestConfig = pluginConfig?.ingest || {};
      const includeDefaultPaths = ingestConfig.includeDefaultPaths !== false;

      const defaultPaths = [
        path.join(workspaceDir, "AGENTS.md"),
        path.join(workspaceDir, "SOUL.md"),
        path.join(workspaceDir, "TOOLS.md"),
        path.join(workspaceDir, "MEMORY.md"),
        path.join(workspaceDir, "HEARTBEAT.md"),
        path.join(workspaceDir, "BOOTSTRAP.md"),
        // OpenClaw canonical durable memory folder
        path.join(workspaceDir, "memory"),
      ];

      const extraPaths = Array.isArray(ingestConfig.extraPaths)
        ? ingestConfig.extraPaths.filter((p: unknown): p is string => typeof p === "string")
        : [];

      const combined = includeDefaultPaths ? [...defaultPaths, ...extraPaths] : extraPaths;
      // Dedupe while keeping order
      const out: string[] = [];
      const seen = new Set<string>();
      for (const p of combined) {
        const key = p.trim();
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.push(key);
      }
      return out;
    };

    const getPluginConfig = (toolCtx?: { config?: any; runtimeConfig?: any }) => {
      // Prefer plugin-scoped config (what users edit under plugins.entries["memu-engine"].config)
      if (api.pluginConfig && typeof api.pluginConfig === "object") {
        return api.pluginConfig as Record<string, unknown>;
      }

      // Fallback: derive from full OpenClaw config if present
      const fullCfg = toolCtx?.runtimeConfig || toolCtx?.config || apiAny.config;
      const cfgFromFull = fullCfg?.plugins?.entries?.[api.id]?.config;
      if (cfgFromFull && typeof cfgFromFull === "object") {
        return cfgFromFull as Record<string, unknown>;
      }

      return {};
    };
    
    // ---------------------------------------------------------
    // 1. Cross-Platform Background Service
    // ---------------------------------------------------------
    let syncProcess: ChildProcess | null = null;
    let isShuttingDown = false;

    const getUserId = (pluginConfig: any): string => {
      const fromConfig = pluginConfig?.userId;
      if (typeof fromConfig === "string" && fromConfig.trim()) return fromConfig.trim();
      const fromEnv = process.env.MEMU_USER_ID;
      if (typeof fromEnv === "string" && fromEnv.trim()) return fromEnv.trim();
      return "default";
    };

    const getSessionDirs = (enabledAgents: string[]): Map<string, string> => {
      const agentDirs = new Map<string, string>();
      const baseDir = path.join(os.homedir(), ".openclaw", "agents");

      for (const agentName of enabledAgents) {
        const sessionDir = path.join(baseDir, agentName, "sessions");

        if (fs.existsSync(sessionDir)) {
          agentDirs.set(agentName, sessionDir);
        } else {
          logger.warn(
            `[memu-engine] Agent '${agentName}' sessions directory does not exist: ${sessionDir}`
          );
        }
      }

      if (process.env.OPENCLAW_SESSIONS_DIR) {
        agentDirs.set("main", process.env.OPENCLAW_SESSIONS_DIR);
      }

      return agentDirs;
    };

    const getSessionDir = (agentName = "main"): string => {
      const dirs = getSessionDirs([agentName]);
      const directDir = dirs.get(agentName);
      if (directDir && fs.existsSync(directDir)) {
        return directDir;
      }

      const home = os.homedir();
      const candidates = [
        path.join(home, ".openclaw", "agents", agentName, "sessions"),
        path.join(home, ".openclaw", "agents", "main", "sessions"),
        path.join(home, ".openclaw", "sessions"),
      ];

      for (const candidate of candidates) {
        if (candidate && fs.existsSync(candidate)) {
          return candidate;
        }
      }

      return candidates[0];
    };

    const getRetrievalConfig = (
      pluginConfig: any
    ): {
      mode: "fast" | "full";
      contextMessages: number;
      defaultCategoryQuota: number | null;
      defaultItemQuota: number | null;
      outputMode: "compact" | "full";
    } => {
      const retrieval = pluginConfig?.retrieval || {};
      const rawMode = typeof retrieval.mode === "string" ? retrieval.mode.toLowerCase() : "fast";
      const mode: "fast" | "full" = rawMode === "full" ? "full" : "fast";
      const rawOutputMode = typeof retrieval.outputMode === "string" ? retrieval.outputMode.toLowerCase() : "compact";
      const outputMode: "compact" | "full" = rawOutputMode === "full" ? "full" : "compact";
      const rawContext = Number(retrieval.contextMessages);
      const contextMessages = Number.isFinite(rawContext) ? Math.max(0, Math.min(20, Math.trunc(rawContext))) : 3;
      const rawDefaultCategory = Number(retrieval.defaultCategoryQuota);
      const rawDefaultItem = Number(retrieval.defaultItemQuota);
      const defaultCategoryQuota = Number.isFinite(rawDefaultCategory)
        ? Math.max(0, Math.trunc(rawDefaultCategory))
        : null;
      const defaultItemQuota = Number.isFinite(rawDefaultItem)
        ? Math.max(0, Math.trunc(rawDefaultItem))
        : null;
      return { mode, contextMessages, defaultCategoryQuota, defaultItemQuota, outputMode };
    };

    const extractTextContent = (content: unknown): string => {
      if (typeof content === "string") return content;
      if (!Array.isArray(content)) return "";
      const parts: string[] = [];
      for (const item of content as Array<{ type?: string; text?: string }>) {
        if (item && item.type === "text" && typeof item.text === "string" && item.text.trim()) {
          parts.push(item.text);
        }
      }
      return parts.join("\n").trim();
    };

    const getRecentSessionMessages = (sessionDir: string, maxMessages: number): Array<{ role: "user" | "assistant"; content: string }> => {
      if (maxMessages <= 0) return [];
      try {
        let sessionId: string | undefined;
        const sessionsMetaPath = path.join(sessionDir, "sessions.json");
        if (fs.existsSync(sessionsMetaPath)) {
          try {
            const raw = fs.readFileSync(sessionsMetaPath, "utf-8");
            const parsed = JSON.parse(raw) as Record<string, { sessionId?: string }>;
            sessionId = parsed?.["agent:main:main"]?.sessionId;
            if (!sessionId) {
              const first = Object.values(parsed || {}).find((v) => typeof v?.sessionId === "string" && v.sessionId);
              sessionId = first?.sessionId;
            }
          } catch {
            sessionId = undefined;
          }
        }

        let sessionFile = sessionId ? path.join(sessionDir, `${sessionId}.jsonl`) : "";
        if (!sessionFile || !fs.existsSync(sessionFile)) {
          const candidates = fs
            .readdirSync(sessionDir)
            .filter((f) => f.endsWith(".jsonl"))
            .map((f) => {
              const full = path.join(sessionDir, f);
              const st = fs.statSync(full);
              return { full, mtimeMs: st.mtimeMs };
            })
            .sort((a, b) => b.mtimeMs - a.mtimeMs);
          sessionFile = candidates[0]?.full || "";
        }

        if (!sessionFile || !fs.existsSync(sessionFile)) return [];
        const lines = fs.readFileSync(sessionFile, "utf-8").split("\n").filter(Boolean);
        const out: Array<{ role: "user" | "assistant"; content: string }> = [];
        for (const line of lines) {
          try {
            const evt = JSON.parse(line) as {
              type?: string;
              message?: { role?: string; content?: unknown };
            };
            if (evt?.type !== "message") continue;
            const role = evt?.message?.role;
            if (role !== "user" && role !== "assistant") continue;
            const text = extractTextContent(evt?.message?.content);
            if (!text) continue;
            out.push({ role, content: text });
          } catch {
            continue;
          }
        }

        return out.slice(-maxMessages);
      } catch {
        return [];
      }
    };

    const getMemuDataDir = (pluginConfig: any): string => {
      // Priority: pluginConfig.dataDir > env > default
      const fromConfig = pluginConfig?.dataDir;
      if (typeof fromConfig === "string" && fromConfig.trim()) {
        const resolved = fromConfig.startsWith("~")
          ? path.join(process.env.HOME || "", fromConfig.slice(1))
          : fromConfig;
        return resolved;
      }
      const fromEnv = process.env.MEMU_DATA_DIR;
      if (fromEnv && fromEnv.trim()) return fromEnv;
      // Default: ~/.openclaw/memUdata
      const home = process.env.HOME || "";
      return path.join(home, ".openclaw", "memUdata");
    };

    const getMemoryRoot = (pluginConfig: any): string => {
      const fromConfig = pluginConfig?.memoryRoot;
      if (typeof fromConfig === "string" && fromConfig.trim()) {
        return fromConfig.startsWith("~")
          ? path.join(process.env.HOME || "", fromConfig.slice(1))
          : fromConfig;
      }
      const fromEnv = process.env.MEMU_MEMORY_ROOT;
      if (typeof fromEnv === "string" && fromEnv.trim()) return fromEnv;
      return path.join(process.env.HOME || "", ".openclaw", "memUdata", "memory");
    };

    const resolveWorkspaceDir = (toolCtx?: { workspaceDir?: string; agentId?: string }): string => {
      const runtimeAgent = apiAny.runtime?.agent;
      if (runtimeAgent && typeof runtimeAgent.resolveAgentWorkspaceDir === "function") {
        try {
          const resolved = runtimeAgent.resolveAgentWorkspaceDir(apiAny.config, toolCtx?.agentId || "main");
          if (typeof resolved === "string" && resolved.trim()) {
            return resolved;
          }
        } catch {
          // Fall back to legacy discovery below.
        }
      }

      if (toolCtx?.workspaceDir) {
        return toolCtx.workspaceDir;
      }

      const home = os.homedir();
      const workspaceCandidates = [
        process.env.OPENCLAW_WORKSPACE_DIR,
        path.join(home, ".openclaw", "workspace"),
        process.cwd(),
      ].filter(Boolean) as string[];

      for (const candidate of workspaceCandidates) {
        if (fs.existsSync(candidate)) {
          return candidate;
        }
      }

      return workspaceCandidates[0] || process.cwd();
    };

    const resolveAgentId = (toolCtx?: { agentId?: string; sessionKey?: string }): string => {
      if (typeof toolCtx?.agentId === "string" && toolCtx.agentId.trim()) {
        return toolCtx.agentId.trim();
      }

      if (typeof toolCtx?.sessionKey === "string") {
        const parts = toolCtx.sessionKey.split(":");
        if (parts.length >= 2 && parts[0] === "agent" && parts[1]?.trim()) {
          return parts[1].trim();
        }
      }

      return "main";
    };

    const agentDbPath = (pluginConfig: any, agentId: string): string =>
      path.join(getMemoryRoot(pluginConfig), agentId, "memu.db");

    const buildMemoryPromptSection = ({
      availableTools,
      citationsMode,
    }: {
      availableTools?: Set<string>;
      citationsMode?: "on" | "off";
    } = {}): string[] => {
      const hasMemorySearch = availableTools?.has("memory_search") ?? true;
      const hasMemoryGet = availableTools?.has("memory_get") ?? true;
      if (!hasMemorySearch && !hasMemoryGet) {
        return [];
      }

      let toolGuidance = "";
      if (hasMemorySearch && hasMemoryGet) {
        toolGuidance =
          "Before answering about prior work, decisions, dates, preferences, or todos, run memory_search first and then use memory_get only for the lines you actually need.";
      } else if (hasMemorySearch) {
        toolGuidance =
          "Before answering about prior work, decisions, dates, preferences, or todos, run memory_search and answer from the returned memory snippets.";
      } else {
        toolGuidance =
          "When the user already points to a specific memory file, run memory_get to load only the needed lines before answering.";
      }

      const lines = ["## Memory Recall", toolGuidance];
      if (citationsMode === "off") {
        lines.push("Citations are disabled, so do not mention file paths or line numbers unless the user explicitly asks.");
      } else {
        lines.push("When it helps the user verify results, include Source references from returned memory snippets.");
      }
      lines.push("");
      return lines;
    };

    const getMemorySearchManager = (toolCtx?: {
      config?: any;
      runtimeConfig?: any;
      workspaceDir?: string;
      sessionKey?: string;
      agentId?: string;
    }): RegisteredMemorySearchManager => {
      const pluginConfig = getPluginConfig(toolCtx);
      const workspaceDir = resolveWorkspaceDir(toolCtx);
      const agentId = resolveAgentId(toolCtx);
      const cacheKey = `${workspaceDir}::${agentId}`;
      const cached = memoryManagerCache.get(cacheKey);
      if (cached) {
        return cached;
      }

      const normalizedConfig = normalizeConfig(pluginConfig);
      const retrievalCfg = getRetrievalConfig(normalizedConfig);
      const defaultPolicy = { memoryEnabled: true, searchEnabled: true, searchableStores: ["self"] as string[] };
      const agentPolicy = normalizedConfig.agentSettings?.[agentId] || defaultPolicy;
      const resolvedStores = Array.from(
        new Set(
          (Array.isArray(agentPolicy.searchableStores) ? agentPolicy.searchableStores : ["self"]).map((store) =>
            store === "self" ? agentId : store
          )
        )
      );

      const manager: RegisteredMemorySearchManager = {
        async search(query, opts) {
          const args: string[] = [query, "--mode", retrievalCfg.mode, "--requesting-agent", agentId];
          args.push("--search-stores", resolvedStores.join(","));

          if (typeof opts?.maxResults === "number" && Number.isFinite(opts.maxResults)) {
            args.push("--max-results", String(Math.trunc(opts.maxResults)));
          }
          if (typeof opts?.minScore === "number" && Number.isFinite(opts.minScore)) {
            args.push("--min-score", String(opts.minScore));
          }
          if (retrievalCfg.defaultCategoryQuota !== null) {
            args.push("--category-quota", String(retrievalCfg.defaultCategoryQuota));
          }
          if (retrievalCfg.defaultItemQuota !== null) {
            args.push("--item-quota", String(retrievalCfg.defaultItemQuota));
          }
          if (retrievalCfg.mode === "full") {
            const history = getRecentSessionMessages(getSessionDir(agentId), retrievalCfg.contextMessages);
            args.push("--queries-json", JSON.stringify([...history, { role: "user", content: query }]));
          }

          const result = await runPython("search.py", args, normalizedConfig, workspaceDir);
          const parsed = JSON.parse(result) as { results?: Array<Record<string, unknown>> };
          return Array.isArray(parsed.results)
            ? parsed.results.map((row) => {
                const pathValue = typeof row.path === "string" ? row.path : "";
                const startLine = Number.isFinite(Number(row.startLine)) ? Math.trunc(Number(row.startLine)) : 1;
                const endLine = Number.isFinite(Number(row.endLine)) ? Math.trunc(Number(row.endLine)) : startLine;
                const source = row.source === "sessions" ? "sessions" : "memory";
                return {
                  path: pathValue,
                  startLine,
                  endLine,
                  score: Number.isFinite(Number(row.score)) ? Number(row.score) : 0,
                  snippet: typeof row.snippet === "string" ? row.snippet : "",
                  source,
                  citation:
                    typeof row.citation === "string"
                      ? row.citation
                      : pathValue
                        ? `${pathValue}:${startLine}`
                        : undefined,
                };
              })
            : [];
        },
        async readFile(params) {
          const args: string[] = [params.relPath];
          if (typeof params.from === "number" && Number.isFinite(params.from)) {
            args.push("--from", String(Math.trunc(params.from)));
          }
          if (typeof params.lines === "number" && Number.isFinite(params.lines)) {
            args.push("--lines", String(Math.trunc(params.lines)));
          }
          const result = await runPython("get.py", args, normalizedConfig, workspaceDir);
          const parsed = JSON.parse(result) as { path?: string; text?: string };
          return {
            path: typeof parsed.path === "string" ? parsed.path : params.relPath,
            text: typeof parsed.text === "string" ? parsed.text : "",
          };
        },
        status() {
          const embeddingConfig = normalizedConfig.embedding || {};
          return {
            backend: "builtin",
            provider: typeof embeddingConfig.provider === "string" ? embeddingConfig.provider : "openai",
            requestedProvider: typeof embeddingConfig.provider === "string" ? embeddingConfig.provider : "openai",
            model: typeof embeddingConfig.model === "string" ? embeddingConfig.model : "text-embedding-3-small",
            workspaceDir,
            dbPath: agentDbPath(normalizedConfig, agentId),
            extraPaths: computeExtraPaths(normalizedConfig, workspaceDir),
            sources: ["memory", "sessions"],
            custom: {
              pluginId: api.id,
              agentId,
              retrievalMode: retrievalCfg.mode,
            },
          };
        },
        async sync(params) {
          params?.progress?.({ completed: 0, total: 1, label: "Flushing memU state" });
          await runPython("flush.py", [], normalizedConfig, workspaceDir);
          params?.progress?.({ completed: 1, total: 1, label: "memU flush complete" });
        },
        async probeEmbeddingAvailability() {
          const pyReady = ensurePythonRuntime(normalizedConfig);
          if (!pyReady.ok) {
            return { ok: false, error: pyReady.reason || "Python bootstrap failed" };
          }

          const embeddingConfig = normalizedConfig.embedding || {};
          const apiKey = await resolveApiKeyWithFallback(
            embeddingConfig.apiKey,
            "MEMU_EMBED_API_KEY",
            "embedding.apiKey"
          );
          if (!apiKey) {
            return { ok: false, error: "Embedding API key is not configured." };
          }
          return { ok: true };
        },
        async probeVectorAvailability() {
          return ensurePythonRuntime(normalizedConfig).ok;
        },
        async close() {
          memoryManagerCache.delete(cacheKey);
        },
      };

      memoryManagerCache.set(cacheKey, manager);
      return manager;
    };

    const memoryRuntime = {
      async getMemorySearchManager(params: any) {
        try {
          return { manager: getMemorySearchManager(params), error: null };
        } catch (error) {
          return {
            manager: null,
            error: error instanceof Error ? error.message : String(error),
          };
        }
      },
      resolveMemoryBackendConfig(params: any) {
        const pluginConfig = getPluginConfig(params);
        const workspaceDir = resolveWorkspaceDir(params);
        const agentId = resolveAgentId(params);
        const status = getMemorySearchManager(params).status();
        return {
          ...status,
          workspaceDir,
          dbPath: agentDbPath(pluginConfig, agentId),
        };
      },
      async closeAllMemorySearchManagers() {
        for (const manager of memoryManagerCache.values()) {
          await manager.close?.();
        }
        memoryManagerCache.clear();
      },
    };

    const serializeAgentDirs = (enabledAgents: string[]): string => {
      const dirs = getSessionDirs(enabledAgents);
      const payload: Record<string, string> = {};
      for (const [name, dir] of dirs.entries()) {
        payload[name] = dir;
      }
      return JSON.stringify(payload);
    };

    const pidFilePath = (dataDir: string) =>
      path.join(dataDir, "state", "watch_sync.pid");

    let stopInProgressUntil = 0;

    const killSyncPid = (pid: number) => {
      if (!Number.isFinite(pid) || pid <= 1) return;
      if (process.platform === "win32") {
        try {
          execFileSync("taskkill", ["/PID", String(pid), "/F", "/T"], {
            stdio: "ignore",
          });
        } catch {
          // ignore
        }
        return;
      }

      try {
        process.kill(-pid, "SIGTERM");
      } catch {
        try {
          process.kill(pid, "SIGTERM");
        } catch {
          // ignore
        }
      }
    };

    const stopSyncService = (dataDir: string) => {
      isShuttingDown = true;
      stopInProgressUntil = Date.now() + 8000;

      if (syncProcess && syncProcess.pid) {
        killSyncPid(syncProcess.pid);
        syncProcess = null;
      }

      try {
        const pidPath = pidFilePath(dataDir);
        if (fs.existsSync(pidPath)) {
          const pidStr = fs.readFileSync(pidPath, "utf-8").trim();
          const pid = Number(pidStr);
          killSyncPid(pid);
          fs.unlinkSync(pidPath);
        }
      } catch {
        // ignore
      }

      const scriptPath = path.join(pythonRoot, "watch_sync.py");
      if (process.platform !== "win32") {
        try {
          execFileSync("pkill", ["-f", scriptPath], { stdio: "ignore" });
        } catch {
          // ignore
        }
      }

      try {
        const lockPath = path.join(os.tmpdir(), "memu_sync.lock_watch_sync");
        if (fs.existsSync(lockPath)) {
          const pid = Number(fs.readFileSync(lockPath, "utf-8").trim());
          killSyncPid(pid);
        }
      } catch {
        // ignore
      }

      isShuttingDown = false;
    };

    let lastDataDirForCleanup: string | null = null;
    let shutdownHooksInstalled = false;
    const installShutdownHooksOnce = () => {
      if (shutdownHooksInstalled) return;
      shutdownHooksInstalled = true;

      const cleanup = () => {
        if (!lastDataDirForCleanup) return;
        try {
          stopSyncService(lastDataDirForCleanup);
        } catch {
          // ignore
        }
      };

      process.once("exit", cleanup);
      process.once("SIGINT", () => {
        cleanup();
        process.exit(0);
      });
      process.once("SIGTERM", () => {
        cleanup();
        process.exit(0);
      });
    };

    const startSyncService = async (pluginConfig: any, workspaceDir: string) => {
      if (syncProcess) return; // Already running

      const normalizedConfig = normalizeConfig(pluginConfig || {});

      const pyReady = ensurePythonRuntime(normalizedConfig);
      if (!pyReady.ok) {
        logger.error(`[memU] Python bootstrap failed: ${pyReady.reason}`);
        return;
      }

      const dataDir = getMemuDataDir(pluginConfig);
      lastDataDirForCleanup = dataDir;
      installShutdownHooksOnce();

      const embeddingConfig = normalizedConfig.embedding || {};
      const extractionConfig = normalizedConfig.extraction || {};
      const extraPaths = computeExtraPaths(normalizedConfig, workspaceDir);
      const userId = getUserId(normalizedConfig);
      const sessionDir = getSessionDir();
      
      // Resolve API keys with SecretRef support
      const embedApiKey = await resolveApiKeyWithFallback(
        embeddingConfig.apiKey,
        "MEMU_EMBED_API_KEY",
        "embedding.apiKey"
      );
      const chatApiKey = await resolveApiKeyWithFallback(
        extractionConfig.apiKey,
        "NVIDIA_API_KEY",
        "extraction.apiKey"
      );
      
      const ingestConfig = normalizedConfig.ingest || {};
      const env = buildPythonProcessEnv(normalizedConfig, {
        PYTHONIOENCODING: "utf-8",
        MEMU_USER_ID: userId,
        MEMU_EMBED_PROVIDER: embeddingConfig.provider || "openai",
        MEMU_EMBED_API_KEY: embedApiKey,
        MEMU_EMBED_BASE_URL: embeddingConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_EMBED_MODEL: embeddingConfig.model || "text-embedding-3-small",

        MEMU_CHAT_PROVIDER: extractionConfig.provider || "openai",
        MEMU_CHAT_API_KEY: chatApiKey,
        MEMU_CHAT_BASE_URL: extractionConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_CHAT_MODEL: extractionConfig.model || "gpt-4o-mini",

        MEMU_DATA_DIR: dataDir,
        MEMU_MEMORY_ROOT: getMemoryRoot(normalizedConfig),
        MEMU_AGENT_DIRS: serializeAgentDirs(normalizedConfig.enabledAgents || ["main"]),
        MEMU_AGENT_SETTINGS: JSON.stringify(normalizedConfig.agentSettings || {}),
        MEMU_WORKSPACE_DIR: workspaceDir,
        MEMU_EXTRA_PATHS: JSON.stringify(extraPaths),
        MEMU_OUTPUT_LANG: normalizedConfig.language || "auto",
        MEMU_CHUNK_SIZE: String(normalizedConfig.chunkSize),
        MEMU_CHUNK_OVERLAP: String(normalizedConfig.chunkOverlap),
        OPENCLAW_SESSIONS_DIR: sessionDir,
        MEMU_FILTER_SCHEDULED_SYSTEM_MESSAGES:
          ingestConfig.filterScheduledSystemMessages === false ? "false" : "true",
        MEMU_IGNORE_SESSION_ID_PATTERNS: Array.isArray(ingestConfig.ignoreSessionIdPatterns)
          ? JSON.stringify(
              ingestConfig.ignoreSessionIdPatterns.filter(
                (value: unknown): value is string =>
                  typeof value === "string" && value.trim().length > 0
              )
            )
          : "[]",
        MEMU_SCHEDULED_SYSTEM_MODE:
          typeof ingestConfig.scheduledSystemMode === "string"
            ? ingestConfig.scheduledSystemMode
            : "event",
        MEMU_SCHEDULED_SYSTEM_MIN_CHARS:
          Number.isFinite(Number(ingestConfig.scheduledSystemMinChars))
            ? String(Math.max(64, Math.trunc(Number(ingestConfig.scheduledSystemMinChars))))
            : "500",
      });

      const scriptPath = path.join(pythonRoot, "watch_sync.py");
      logInfo(`[memU] Starting background sync service: ${scriptPath}`);
      
      // Launch using uv run
      const proc = spawn(uvBinary, ["run", "--project", pythonRoot, "python", scriptPath], {
        cwd: pythonRoot,
        env,
        stdio: "pipe",
      });

      syncProcess = proc;

      isShuttingDown = false;

      // Write PID file for orphan cleanup
      try {
        const pidPath = pidFilePath(dataDir);
        fs.mkdirSync(path.dirname(pidPath), { recursive: true });
        if (syncProcess.pid) fs.writeFileSync(pidPath, String(syncProcess.pid), "utf-8");
      } catch {
        // ignore
      }

      // Redirect logs to Gateway console (with prefix)
      proc.stdout?.on("data", (d) => {
        const lines = d.toString().trim().split("\n");
        lines.forEach((l: string) => logInfo(`[memU Sync] ${l}`));
      });
      proc.stderr?.on("data", (d) => logger.error(`[memU Sync Error] ${d}`));

      proc.on("close", (code, signal) => {
        // Ignore stale close events from an old process instance.
        if (syncProcess !== proc) return;
        syncProcess = null;
        try {
          const pidPath = pidFilePath(dataDir);
          if (fs.existsSync(pidPath)) fs.unlinkSync(pidPath);
        } catch {
          // ignore
        }
        if (isShuttingDown || Date.now() < stopInProgressUntil) return;

        if (code === 0 || signal === "SIGTERM" || signal === "SIGINT" || signal === "SIGKILL") {
          logInfo(
            `[memU] Sync service exited normally (code ${code ?? "null"}, signal ${signal ?? "none"}).`
          );
          return;
        }

        if (code === null && !signal) {
          logInfo("[memU] Sync service exited without code/signal; skip restart.");
          return;
        }

        if (!isShuttingDown) {
          logger.warn(`[memU] Sync service crashed (code ${code}). Restarting in 5s...`);
          setTimeout(() => startSyncService(pluginConfig, workspaceDir), 5000);
        }
      });
    };

    // ---------------------------------------------------------
    // 2. Auto-start Sync Service on Gateway Init
    // ---------------------------------------------------------
    // OpenClaw doesn't expose explicit onStart hook to plugins yet.
    // We use setImmediate to start the service after registration completes.
    // This ensures sync starts immediately when gateway loads, not on first tool call.
    
    const getGatewayManagementCommand = (): "stop" | "restart" | "status" | "health" | null => {
      const argv = process.argv.slice(2).map((v) => String(v).toLowerCase());
      if (!argv.includes("gateway")) return null;
      const mgmt: Array<"stop" | "restart" | "status" | "health"> = ["stop", "restart", "status", "health"];
      for (const c of mgmt) {
        if (argv.includes(c)) return c;
      }
      return null;
    };

    const isGatewayContext = (): boolean => {
      const argv = process.argv.slice(2).map((v) => String(v).toLowerCase());
      const subcommands = argv.filter((a) => !a.startsWith("-"));
      if (subcommands.length === 0) return true; // bare `openclaw`
      return subcommands[0] === "gateway";
    };

    let autoStartTriggered = false;
    const triggerAutoStart = () => {
      if (autoStartTriggered) return;
      autoStartTriggered = true;

      if (!isGatewayContext()) {
        return;
      }

      const mgmtCmd = getGatewayManagementCommand();
      if (mgmtCmd) {
        if (mgmtCmd === "stop" || mgmtCmd === "restart") {
          try {
            const pluginConfig = api.pluginConfig || {};
            stopSyncService(getMemuDataDir(pluginConfig));
          } catch {
            // ignore
          }
        }
        logInfo("[memU] Skipping auto-start for gateway management command.");
        return;
      }
      
      // Defer to next tick to ensure plugin is fully registered
      setImmediate(() => {
        try {
          const pluginConfig = api.pluginConfig || {};
          // Determine workspace dir from common locations
          const home = os.homedir();
          const workspaceCandidates = [
            process.env.OPENCLAW_WORKSPACE_DIR,
            path.join(home, ".openclaw", "workspace"),
            process.cwd(),
          ].filter(Boolean) as string[];
          
          let workspaceDir = workspaceCandidates[0];
          for (const c of workspaceCandidates) {
            if (fs.existsSync(c)) {
              workspaceDir = c;
              break;
            }
          }
          
          logInfo(`[memU] Auto-starting sync service for workspace: ${workspaceDir}`);
          startSyncService(pluginConfig, workspaceDir);
        } catch (e) {
          logger.error(`[memU] Auto-start failed: ${e}`);
        }
      });
    };
    
    // Trigger auto-start immediately
    triggerAutoStart();

    // ---------------------------------------------------------
    // 2.1 Optional: auto flush memU on compaction
    // ---------------------------------------------------------
    // OpenClaw has an official "memory flush" turn near auto-compaction.
    // We can optionally finalize our staged tail + ingest into memU after compaction.
    const registerCompactionFlushHook = () => {
      const pluginConfig = api.pluginConfig || {};
      const enabled = (pluginConfig as any)?.flushOnCompaction === true;
      if (!enabled) return;

      const apiAny = api as any;
      const hookName = "after_compaction";
      const handler = async (_event: unknown, ctx: any) => {
        try {
          const workspaceDir = ctx?.workspaceDir || process.env.OPENCLAW_WORKSPACE_DIR || process.cwd();
          await runPython("flush.py", [], pluginConfig, workspaceDir);
        } catch (e) {
          logger.error(`[memU] after_compaction flush failed: ${e}`);
        }
      };

      if (typeof apiAny.on === "function") {
        apiAny.on(hookName, handler, { priority: -10 });
        logInfo(`[memU] Registered hook: ${hookName} (flushOnCompaction=true)`);
        return;
      }

      if (typeof apiAny.registerHook === "function") {
        apiAny.registerHook(hookName, handler, { name: "memu-engine:after_compaction_flush" });
        logInfo(`[memU] Registered hook via registerHook: ${hookName} (flushOnCompaction=true)`);
        return;
      }

      logger.warn("[memU] Hook API not available; cannot enable flushOnCompaction");
    };
    
    // ---------------------------------------------------------
    // 3. Register Tools
    // ---------------------------------------------------------
    
    const runPython = async (
      scriptName: string,
      args: string[],
      pluginConfig: any,
      workspaceDir: string,
    ): Promise<string> => {
      const pyReady = ensurePythonRuntime(pluginConfig);
      if (!pyReady.ok) {
        return `Error: memU Python bootstrap failed. ${pyReady.reason || "unknown reason"}`;
      }

      const normalizedConfig = normalizeConfig(pluginConfig || {});

      // Key point: Trigger background service here (lazy singleton)
      await startSyncService(normalizedConfig, workspaceDir);

      const embeddingConfig = normalizedConfig.embedding || {};
      const extractionConfig = normalizedConfig.extraction || {};
      const extraPaths = computeExtraPaths(normalizedConfig, workspaceDir);
      const sessionDir = getSessionDir();
      const userId = getUserId(normalizedConfig);
      
      // Resolve API keys with SecretRef support (no env fallback in runPython)
      const embedApiKey = await resolveApiKeyWithFallback(
        embeddingConfig.apiKey,
        "MEMU_EMBED_API_KEY",
        "embedding.apiKey"
      );
      const chatApiKey = await resolveApiKeyWithFallback(
        extractionConfig.apiKey,
        "NVIDIA_API_KEY",
        "extraction.apiKey"
      );
      
      const ingestConfig = normalizedConfig.ingest || {};
      const env = buildPythonProcessEnv(normalizedConfig, {
        PYTHONIOENCODING: "utf-8",
        MEMU_USER_ID: userId,
        
        MEMU_EMBED_PROVIDER: embeddingConfig.provider || "openai",
        MEMU_EMBED_API_KEY: embedApiKey,
        MEMU_EMBED_BASE_URL: embeddingConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_EMBED_MODEL: embeddingConfig.model || "text-embedding-3-small",
        
        MEMU_CHAT_PROVIDER: extractionConfig.provider || "openai",
        MEMU_CHAT_API_KEY: chatApiKey,
        MEMU_CHAT_BASE_URL: extractionConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_CHAT_MODEL: extractionConfig.model || "gpt-4o-mini",

        MEMU_DATA_DIR: getMemuDataDir(normalizedConfig),
        MEMU_MEMORY_ROOT: getMemoryRoot(normalizedConfig),
        MEMU_AGENT_DIRS: serializeAgentDirs(normalizedConfig.enabledAgents || ["main"]),
        MEMU_AGENT_SETTINGS: JSON.stringify(normalizedConfig.agentSettings || {}),
        MEMU_WORKSPACE_DIR: workspaceDir,
        MEMU_EXTRA_PATHS: JSON.stringify(extraPaths),
        OPENCLAW_SESSIONS_DIR: sessionDir,
        MEMU_OUTPUT_LANG: normalizedConfig.language || "auto",
        MEMU_DEBUG_TIMING: (normalizedConfig as any)?.debugTiming === true ? "true" : "false",
        MEMU_CHUNK_SIZE: String(normalizedConfig.chunkSize),
        MEMU_CHUNK_OVERLAP: String(normalizedConfig.chunkOverlap),
        MEMU_FILTER_SCHEDULED_SYSTEM_MESSAGES:
          ingestConfig.filterScheduledSystemMessages === false ? "false" : "true",
        MEMU_IGNORE_SESSION_ID_PATTERNS: Array.isArray(ingestConfig.ignoreSessionIdPatterns)
          ? JSON.stringify(
              ingestConfig.ignoreSessionIdPatterns.filter(
                (value: unknown): value is string =>
                  typeof value === "string" && value.trim().length > 0
              )
            )
          : "[]",
        MEMU_SCHEDULED_SYSTEM_MODE:
          typeof ingestConfig.scheduledSystemMode === "string"
            ? ingestConfig.scheduledSystemMode
            : "event",
        MEMU_SCHEDULED_SYSTEM_MIN_CHARS:
          Number.isFinite(Number(ingestConfig.scheduledSystemMinChars))
            ? String(Math.max(64, Math.trunc(Number(ingestConfig.scheduledSystemMinChars))))
            : "500",
      });

      return new Promise((resolve) => {
        const proc = spawn(uvBinary, ["run", "--project", pythonRoot, "python", path.join(pythonRoot, "scripts", scriptName), ...args], {
          cwd: pythonRoot,
          env
        });

        let stdout = "";
        let stderr = "";
        proc.stdout.on("data", (data) => { stdout += data.toString(); });
        proc.stderr.on("data", (data) => { stderr += data.toString(); });

        proc.on("close", (code) => {
          if (code !== 0) resolve(`Error (code ${code}): ${stderr}`);
          else resolve(stdout.trim() || "No content found.");
        });
      });
    };

    // Register hooks after helpers are available.
    registerCompactionFlushHook();

    if (typeof apiAny.registerMemoryPromptSection === "function") {
      apiAny.registerMemoryPromptSection(buildMemoryPromptSection);
    } else {
      logger.warn("[memU] Memory prompt section API is not available; continuing without prompt-section registration.");
    }

    if (typeof apiAny.registerMemoryRuntime === "function") {
      apiAny.registerMemoryRuntime(memoryRuntime);
    } else {
      logger.warn("[memU] Memory runtime API is not available; continuing with tool-only compatibility mode.");
    }

    const searchSchema = {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        maxResults: { type: "integer", description: "Maximum number of results to return." },
        minScore: { type: "number", description: "Minimum relevance score (0.0 to 1.0)." },
        categoryQuota: { type: "integer", description: "Preferred number of category results." },
        itemQuota: { type: "integer", description: "Preferred number of item results." },
        agentName: { type: "string", description: "Agent name (default: main)" },
      },
      required: ["query"],
    };

    const flushSchema = {
      type: "object",
      properties: {},
      required: [],
    };

    const getSchema = {
      type: "object",
      properties: {
        path: { type: "string", description: "Path to the memory file or memU resource URL." },
        from: { type: "integer", description: "Start line (1-based)." },
        lines: { type: "integer", description: "Number of lines to read." },
      },
      required: ["path"],
    };

    api.registerTool(
      (ctx) => {
        const pluginConfig = getPluginConfig(ctx);
        const workspaceDir = resolveWorkspaceDir(ctx);
        const defaultAgentId = resolveAgentId(ctx);

        const searchTool = (name: string, description: string) => ({
          name,
          description,
          parameters: searchSchema,
          async execute(_toolCallId: string, params: unknown) {
            const { query, maxResults, minScore, categoryQuota, itemQuota, agentName } = params as {
              query?: string;
              maxResults?: number;
              minScore?: number;
              categoryQuota?: number;
              itemQuota?: number;
              agentName?: string;
            };
            if (!query) {
              return {
                content: [{ type: "text", text: "Missing required parameter: query" }],
                details: { error: "missing_query" },
              };
            }

            const retrievalCfg = getRetrievalConfig(pluginConfig);
            let contextCount = 0;
            const args: string[] = [query, "--mode", retrievalCfg.mode];
            if (typeof maxResults === "number" && Number.isFinite(maxResults)) {
              args.push("--max-results", String(Math.trunc(maxResults)));
            }
            if (typeof minScore === "number" && Number.isFinite(minScore)) {
              args.push("--min-score", String(minScore));
            }
            if (typeof categoryQuota === "number" && Number.isFinite(categoryQuota)) {
              args.push("--category-quota", String(Math.trunc(categoryQuota)));
            } else if (retrievalCfg.defaultCategoryQuota !== null) {
              args.push("--category-quota", String(retrievalCfg.defaultCategoryQuota));
            }
            if (typeof itemQuota === "number" && Number.isFinite(itemQuota)) {
              args.push("--item-quota", String(Math.trunc(itemQuota)));
            } else if (retrievalCfg.defaultItemQuota !== null) {
              args.push("--item-quota", String(retrievalCfg.defaultItemQuota));
            }
            const config = normalizeConfig(pluginConfig);
            const requestingAgent =
              typeof agentName === "string" && agentName.trim() ? agentName.trim() : defaultAgentId;
            const defaultPolicy = { memoryEnabled: true, searchEnabled: true, searchableStores: ["self"] as string[] };
            const agentPolicy = config.agentSettings?.[requestingAgent] || defaultPolicy;

            if (!agentPolicy.searchEnabled) {
              const payload = JSON.stringify({
                results: [],
                provider: "openai",
                model: "unknown",
                fallback: null,
                citations: "off",
              });
              return {
                content: [{ type: "text", text: payload }],
                details: { error: "search_disabled", requestingAgent },
              };
            }

            const resolvedStores = Array.from(
              new Set(
                (Array.isArray(agentPolicy.searchableStores) ? agentPolicy.searchableStores : ["self"]).map((store) =>
                  store === "self" ? requestingAgent : store
                )
              )
            );
            args.push("--requesting-agent", requestingAgent);
            args.push("--search-stores", resolvedStores.join(","));
            if (retrievalCfg.mode === "full") {
              const sessionDir = getSessionDir(requestingAgent);
              const history = getRecentSessionMessages(sessionDir, retrievalCfg.contextMessages);
              contextCount = history.length;
              const queries = [...history, { role: "user" as const, content: query }];
              args.push("--queries-json", JSON.stringify(queries));
            }

            const result = await runPython("search.py", args, pluginConfig, workspaceDir);
            let payload: string;
            let parsedForDetails: any = null;
            try {
              const parsed = JSON.parse(result);
              parsedForDetails = parsed;
              if (retrievalCfg.outputMode === "full") {
                payload = JSON.stringify(parsed);
              } else {
                const compactResults = Array.isArray(parsed?.results)
                  ? parsed.results.map((r: any) => ({
                      path: r?.path,
                      snippet: r?.snippet,
                      agentName: r?.agentName,
                    }))
                  : [];
                payload = JSON.stringify({ results: compactResults });
              }
            } catch {
              payload = JSON.stringify({
                results: [],
                provider: "openai",
                model: "unknown",
                fallback: null,
                citations: "off",
                error: result,
              });
            }
            return {
              content: [{ type: "text", text: payload }],
              details: {
                query,
                maxResults,
                minScore,
                categoryQuota,
                itemQuota,
                defaultCategoryQuota: retrievalCfg.defaultCategoryQuota,
                defaultItemQuota: retrievalCfg.defaultItemQuota,
                mode: retrievalCfg.mode,
                outputMode: retrievalCfg.outputMode,
                contextCount,
                contextMessages: retrievalCfg.contextMessages,
                resultCount: Array.isArray(parsedForDetails?.results)
                  ? parsedForDetails.results.length
                  : undefined,
                provider: parsedForDetails?.provider,
                model: parsedForDetails?.model,
              },
            };
          },
        });

        const getTool = (name: string, description: string) => ({
          name,
          description,
          parameters: getSchema,
          async execute(_toolCallId: string, params: unknown) {
            const { path: memoryPath, from, lines } = params as {
              path?: string;
              from?: number;
              lines?: number;
            };
            if (!memoryPath) {
              return {
                content: [{ type: "text", text: "Missing required parameter: path" }],
                details: { error: "missing_path" },
              };
            }

            const args: string[] = [memoryPath];
            if (typeof from === "number" && Number.isFinite(from)) {
              args.push("--from", String(Math.trunc(from)));
            }
            if (typeof lines === "number" && Number.isFinite(lines)) {
              args.push("--lines", String(Math.trunc(lines)));
            }

            const result = await runPython("get.py", args, pluginConfig, workspaceDir);
            let payload: string;
            try {
              const parsed = JSON.parse(result);
              payload = JSON.stringify(parsed);
            } catch {
              payload = JSON.stringify({
                path: memoryPath,
                text: "",
                error: result,
              });
            }
            return {
              content: [{ type: "text", text: payload }],
              details: { path: memoryPath },
            };
          },
        });

        return [
          searchTool("memu_search", "Agentic semantic search on the memU long-term database."),
          searchTool("memory_search", "Mandatory recall step: semantically search the memory system."),
          getTool("memu_get", "Retrieve content from memU database or workspace disk."),
          getTool("memory_get", "Read a specific memory Markdown file."),
          {
            name: "memory_flush",
            description: "Force-finalize (freeze) the staged conversation tail and trigger memU ingestion immediately.",
            parameters: flushSchema,
            async execute(_toolCallId: string) {
              const result = await runPython("flush.py", [], pluginConfig, workspaceDir);
              return {
                content: [{ type: "text", text: result }],
                details: { action: "flush" },
              };
            },
          },
          {
            name: "memu_flush",
            description: "Alias of memory_flush.",
            parameters: flushSchema,
            async execute(_toolCallId: string) {
              const result = await runPython("flush.py", [], pluginConfig, workspaceDir);
              return {
                content: [{ type: "text", text: result }],
                details: { action: "flush" },
              };
            },
          },
        ];
      },
      { names: ["memu_search", "memory_search", "memu_get", "memory_get", "memory_flush", "memu_flush"] },
    );
  }
});

export default memuEnginePlugin;

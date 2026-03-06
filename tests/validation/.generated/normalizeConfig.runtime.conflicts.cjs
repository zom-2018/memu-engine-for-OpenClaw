function normalizeAgentSettings(config) {
    const raw = config?.agentSettings;
    const out = {};
    if (raw && typeof raw === "object") {
        for (const [name, value] of Object.entries(raw)) {
            if (typeof name !== "string" || !name.trim())
                continue;
            const cfg = value && typeof value === "object" ? value : {};
            const stores = Array.isArray(cfg.searchableStores)
                ? cfg.searchableStores.filter((v) => typeof v === "string" && v.trim().length > 0)
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
function normalizeConfig(config) {
    const validAgentPattern = /^[a-z][a-z0-9_-]*$/;
    const oldEnabledAgents = Array.isArray(config.enabledAgents)
        ? config.enabledAgents.filter((a) => typeof a === "string" && a.trim().length > 0)
        : [];
    const agentSettings = normalizeAgentSettings(config);
    const enabledAgents = Array.from(new Set(["main", ...Object.keys(agentSettings), ...oldEnabledAgents]));
    if (!enabledAgents.includes("main")) {
        enabledAgents.unshift("main");
    }
    const invalidAgents = enabledAgents.filter((a) => !validAgentPattern.test(a));
    if (invalidAgents.length > 0) {
        console.warn(`[memu-engine] Invalid agent names: ${invalidAgents.join(", ")}`);
    }
    const rawStorageMode = typeof config.storageMode === "string" ? config.storageMode.trim().toLowerCase() : "hybrid";
    if (rawStorageMode && rawStorageMode !== "hybrid") {
        console.warn(`[memu-engine] storageMode='${rawStorageMode}' is deprecated and ignored. ` +
            "memu-engine now always runs in hybrid mode.");
    }
    const hasLegacyAllowCrossAgentRetrieval = typeof config.allowCrossAgentRetrieval === "boolean";
    if (hasLegacyAllowCrossAgentRetrieval && !warnedAllowCrossAgentRetrievalDeprecation) {
        const legacyValue = config.allowCrossAgentRetrieval === true;
        console.warn(`[memu-engine] 'allowCrossAgentRetrieval' is deprecated and will be removed in a future release. ` +
            `Migration guide: set per-agent 'agentSettings.<agent>.searchableStores' instead. ` +
            `Equivalent mapping: allowCrossAgentRetrieval=${legacyValue} -> searchableStores=${legacyValue ? "['self','shared']" : "['self']"}. ` +
            `When both old and new config are present, agentSettings takes precedence.`);
        warnedAllowCrossAgentRetrievalDeprecation = true;
    }
    const normalizedAgentSettings = { ...agentSettings };
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
        console.warn(`[memu-engine] Official Memory System Conflict detected: both OpenClaw official memory (` +
            `agents.defaults.memorySearch.enabled=true) and memu-engine memory slot (` +
            `plugins.slots.memory="memu-engine") are active at the same time. ` +
            `This enables two memory systems simultaneously and can produce confusing retrieval behavior. ` +
            `Recommended fix: disable the official memory system and keep memu-engine as the only memory backend. ` +
            `Exact change in openclaw.json: set "agents.defaults.memorySearch.enabled": false.`);
    }
    const rawChunkSize = config.chunkSize ?? 512;
    const chunkSizeNum = Number(rawChunkSize);
    if (!Number.isFinite(chunkSizeNum) || !Number.isInteger(chunkSizeNum) || chunkSizeNum <= 0 || chunkSizeNum > 2048) {
        throw new Error(`[memu-engine] Invalid chunkSize '${String(rawChunkSize)}'. Expected integer in range 1..2048.`);
    }
    const rawChunkOverlap = config.chunkOverlap ?? 50;
    const chunkOverlapNum = Number(rawChunkOverlap);
    if (!Number.isFinite(chunkOverlapNum) || !Number.isInteger(chunkOverlapNum) || chunkOverlapNum < 0) {
        throw new Error(`[memu-engine] Invalid chunkOverlap '${String(rawChunkOverlap)}'. Expected integer >= 0.`);
    }
    if (chunkOverlapNum >= chunkSizeNum) {
        throw new Error(`[memu-engine] Invalid chunkOverlap '${chunkOverlapNum}'. chunkOverlap must be less than chunkSize (${chunkSizeNum}).`);
    }
    return {
        ...config,
        enabledAgents,
        agentSettings: normalizedAgentSettings,
        storageMode: "hybrid",
        chunkSize: chunkSizeNum,
        chunkOverlap: chunkOverlapNum,
    };
}
module.exports = { normalizeConfig };

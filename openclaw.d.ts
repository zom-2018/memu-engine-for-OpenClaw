declare module "openclaw/plugin-sdk/plugin-entry" {
  export interface ToolContext {
    config?: {
      plugins?: {
        entries?: Record<string, { config?: Record<string, unknown> }>;
      };
    };
    runtimeConfig?: {
      plugins?: {
        entries?: Record<string, { config?: Record<string, unknown> }>;
      };
    };
    workspaceDir?: string;
    sessionKey?: string;
    agentId?: string;
  }

  export interface ToolDefinition {
    name: string;
    description: string;
    parameters: object;
    execute(toolCallId: string, params: unknown): Promise<ToolResult>;
  }

  export interface ToolResult {
    content: Array<{ type: string; text: string }>;
    details?: Record<string, unknown>;
  }

  export interface OpenClawPluginApi {
    id: string;
    pluginConfig?: Record<string, unknown>;
    config?: Record<string, unknown>;
    logger?: {
      log(...args: unknown[]): void;
      warn(...args: unknown[]): void;
      error(...args: unknown[]): void;
    };
    runtime?: {
      agent?: {
        resolveAgentWorkspaceDir?(config: unknown, agentId: string): string | undefined;
      };
    };
    resolvePath?(relativePath: string): string;
    registerTool(factory: (ctx: ToolContext) => ToolDefinition[], options?: { names?: string[] }): void;
    registerMemoryPromptSection?(builder: (params?: {
      availableTools?: Set<string>;
      citationsMode?: "on" | "off";
    }) => string[]): void;
    registerMemoryRuntime?(runtime: {
      getMemorySearchManager?(params: ToolContext): Promise<{ manager: unknown; error?: string | null }>;
      resolveMemoryBackendConfig?(params: ToolContext): unknown;
      closeAllMemorySearchManagers?(): Promise<void>;
    }): void;
    on?(
      hookName: string,
      handler: (event: unknown, ctx: ToolContext) => void | Promise<void>,
      opts?: { priority?: number }
    ): void;
    registerHook?(
      events: string | string[],
      handler: (event: unknown, ctx: ToolContext) => void | Promise<void>,
      opts?: unknown
    ): void;
  }

  export type PluginEntry = {
    id: string;
    name: string;
    description?: string;
    kind?: string;
    register(api: OpenClawPluginApi): void;
  };

  export function definePluginEntry<T extends PluginEntry>(entry: T): T;
}

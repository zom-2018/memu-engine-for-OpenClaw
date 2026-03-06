import { describe, expect, it, vi, beforeAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import ts from 'typescript';

type AgentSettings = Record<
  string,
  {
    memoryEnabled: boolean;
    searchEnabled: boolean;
    searchableStores: string[];
  }
>;

type NormalizedConfig = {
  enabledAgents: string[];
  agentSettings: AgentSettings;
  chunkSize: number;
  chunkOverlap: number;
  storageMode: string;
  [key: string]: unknown;
};

let normalizeConfig: (config: any) => NormalizedConfig;

function extractFunction(source: string, functionName: string): string {
  const sourceFile = ts.createSourceFile('index.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  for (const statement of sourceFile.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name?.text === functionName) {
      return statement.getFullText(sourceFile);
    }
  }
  throw new Error(`Unable to find function ${functionName} in index.ts`);
}

beforeAll(() => {
  const indexPath = path.resolve(__dirname, '../../index.ts');
  const source = fs.readFileSync(indexPath, 'utf-8');
  const normalizeAgentSettingsSource = extractFunction(source, 'normalizeAgentSettings');
  const normalizeConfigSource = extractFunction(source, 'normalizeConfig');
  const runtimeTs = `${normalizeAgentSettingsSource}\n${normalizeConfigSource}\nmodule.exports = { normalizeConfig };`;
  const transpiled = ts.transpileModule(runtimeTs, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.CommonJS,
      esModuleInterop: true,
    },
    fileName: 'normalizeConfig.runtime.ts',
  }).outputText;

  const generatedDir = path.resolve(__dirname, './.generated');
  const generatedFile = path.join(generatedDir, 'normalizeConfig.runtime.cjs');
  fs.mkdirSync(generatedDir, { recursive: true });
  fs.writeFileSync(generatedFile, transpiled, 'utf-8');

  const req = createRequire(import.meta.url);
  normalizeConfig = (req(generatedFile) as { normalizeConfig: typeof normalizeConfig }).normalizeConfig;
});

describe('normalizeConfig', () => {
  it('applies defaults for chunking and storage fields', () => {
    const config = normalizeConfig({});

    expect(config.chunkSize).toBe(512);
    expect(config.chunkOverlap).toBe(50);
    expect(config.storageMode).toBe('hybrid');
    expect(config.enabledAgents).toEqual(['main']);
    expect(config.agentSettings).toEqual({});
  });

  it('preserves valid custom chunk values', () => {
    const config = normalizeConfig({ chunkSize: 1024, chunkOverlap: 128 });

    expect(config.chunkSize).toBe(1024);
    expect(config.chunkOverlap).toBe(128);
  });

  it('normalizes chunk values from numeric strings', () => {
    const config = normalizeConfig({ chunkSize: '256', chunkOverlap: '16' });

    expect(config.chunkSize).toBe(256);
    expect(config.chunkOverlap).toBe(16);
  });

  it('throws when chunkSize is invalid', () => {
    expect(() => normalizeConfig({ chunkSize: 0 })).toThrow(/Invalid chunkSize/);
    expect(() => normalizeConfig({ chunkSize: -1 })).toThrow(/Invalid chunkSize/);
    expect(() => normalizeConfig({ chunkSize: 2049 })).toThrow(/Invalid chunkSize/);
    expect(() => normalizeConfig({ chunkSize: 12.5 })).toThrow(/Invalid chunkSize/);
    expect(() => normalizeConfig({ chunkSize: 'abc' })).toThrow(/Invalid chunkSize/);
  });

  it('throws when chunkOverlap is invalid or conflicts with chunkSize', () => {
    expect(() => normalizeConfig({ chunkOverlap: -1 })).toThrow(/Invalid chunkOverlap/);
    expect(() => normalizeConfig({ chunkOverlap: 12.5 })).toThrow(/Invalid chunkOverlap/);
    expect(() => normalizeConfig({ chunkSize: 256, chunkOverlap: 256 })).toThrow(
      /chunkOverlap must be less than chunkSize/
    );
    expect(() => normalizeConfig({ chunkSize: 256, chunkOverlap: 999 })).toThrow(
      /chunkOverlap must be less than chunkSize/
    );
  });

  it('merges enabledAgents from explicit list and agentSettings keys with dedupe', () => {
    const config = normalizeConfig({
      enabledAgents: ['main', 'alpha', 'beta', 'alpha'],
      agentSettings: {
        beta: {},
        gamma: {},
      },
    });

    expect(config.enabledAgents).toEqual(['main', 'beta', 'gamma', 'alpha']);
  });

  it('normalizes agentSettings defaults and keeps explicit false values', () => {
    const config = normalizeConfig({
      agentSettings: {
        alpha: {},
        beta: { memoryEnabled: false, searchEnabled: false },
      },
    });

    expect(config.agentSettings.alpha).toEqual({
      memoryEnabled: true,
      searchEnabled: true,
      searchableStores: ['self'],
    });
    expect(config.agentSettings.beta).toEqual({
      memoryEnabled: false,
      searchEnabled: false,
      searchableStores: ['self'],
    });
  });

  it('filters invalid searchableStores entries and falls back to self', () => {
    const config = normalizeConfig({
      agentSettings: {
        alpha: { searchableStores: ['shared', '', '   ', 'self', 123] },
        beta: { searchableStores: ['', '   '] },
      },
    });

    expect(config.agentSettings.alpha.searchableStores).toEqual(['shared', 'self']);
    expect(config.agentSettings.beta.searchableStores).toEqual(['self']);
  });

  it('warns for invalid agent names', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    normalizeConfig({ enabledAgents: ['main', 'Valid', 'bad agent', 'ok_name'] });

    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('Invalid agent names'));
    warnSpy.mockRestore();
  });

  it('warns and forces hybrid when deprecated storageMode is set', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const config = normalizeConfig({ storageMode: '  SQLITE  ' });

    expect(config.storageMode).toBe('hybrid');
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("storageMode='sqlite' is deprecated"));
    warnSpy.mockRestore();
  });

  it('does not warn for hybrid storageMode variants', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const config = normalizeConfig({ storageMode: '  HYBRID  ' });

    expect(config.storageMode).toBe('hybrid');
    expect(warnSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it('keeps unrelated config fields unchanged (including SecretRef-like values)', () => {
    const secretRef = { source: 'env', provider: 'default', id: 'OPENAI_API_KEY' };
    const config = normalizeConfig({
      embedding: {
        apiKey: '${OPENAI_API_KEY}',
      },
      extraction: {
        apiKey: secretRef,
      },
      language: 'en',
      customFlag: true,
    });

    expect((config.embedding as any).apiKey).toBe('${OPENAI_API_KEY}');
    expect((config.extraction as any).apiKey).toEqual(secretRef);
    expect(config.language).toBe('en');
    expect(config.customFlag).toBe(true);
  });
});

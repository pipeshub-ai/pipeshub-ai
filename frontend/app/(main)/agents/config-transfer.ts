/**
 * Export and import of an agent's configuration (C4.08; WP-30 mock as
 * approved): GET /api/v1/agents/{agentKey}/export[?version=n|draft] and
 * POST /api/v1/agents/import. The document names everything by name and
 * carries no ids, credentials or sharing; an import always makes a new agent
 * of the importer's, shared with no one, and lists what it could not bind.
 */
import { apiClient } from '@/lib/api';

const AGENTS_BASE_URL = '/api/v1/agents';
export const AGENT_CONFIG_FORMAT = 'pipeshub.agent-config';
/** The document versions this build reads (2 carries the agent's own gates); a newer file is refused before it is sent. */
export const AGENT_CONFIG_SCHEMA_VERSIONS: readonly number[] = [1, 2];
/** An export is a few hundred KB at most; a bigger file is not one. */
export const AGENT_CONFIG_MAX_BYTES = 5 * 1024 * 1024;
const IMPORT_RESULT_KEY = 'pipeshub.agentImport.';

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => value !== null && typeof value === 'object' && !Array.isArray(value);
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const str = (value: unknown): string | null => (typeof value === 'string' && value.trim() !== '' ? value : null);

/** `version`: a published version number, or `draft`. */
export type ExportVersion = number | 'draft';

export type RebindKind = 'model' | 'roleModel' | 'toolset' | 'mcpServer' | 'knowledge' | 'skill';
export type RebindReason = 'not_found' | 'ambiguous' | 'limited' | 'not_reasoning';
export type ExcludedItem = 'modelAccount' | 'sharing' | 'ownerTeam' | 'serviceAccount' | 'approvals' | 'gates';

export const REBIND_KINDS: readonly RebindKind[] = ['model', 'roleModel', 'toolset', 'mcpServer', 'knowledge', 'skill'];
const REBIND_REASONS: readonly RebindReason[] = ['not_found', 'ambiguous', 'limited', 'not_reasoning'];
export const EXCLUDED_ITEMS: readonly ExcludedItem[] = ['modelAccount', 'sharing', 'ownerTeam', 'serviceAccount', 'approvals', 'gates'];

export interface Rebind {
  kind: RebindKind;
  name: string;
  reason: RebindReason;
}

/** What the importer sees before importing: names only, as the file holds them. */
export interface ImportPreview {
  name: string;
  /** The published version it was exported from; null: the draft. */
  sourceVersion: number | null;
  models: ModelRef[];
  actions: string[];
  mcpServers: string[];
  knowledge: string[];
  skills: string[];
  maxTurns: number | null;
  maxCostUsd: number | null;
  /** The agent's own Safety and checks the new agent runs with; null: it sets none (or the file predates them). */
  gates: PreviewGates | null;
}

export type CheckKind = 'file' | 'json_schema' | 'citations' | 'command';
const CHECK_KINDS: readonly CheckKind[] = ['file', 'json_schema', 'citations', 'command'];

export interface PreviewGates {
  planApproval: boolean;
  /** Each result check by kind, in the file's order. */
  checks: CheckKind[];
}

function gatesOf(value: unknown): PreviewGates | null {
  if (!isObject(value)) return null;
  const verify = isObject(value.verify) ? value.verify : {};
  const checks = list(verify.checks)
    .filter(isObject)
    .map((c) => c.kind)
    .filter((kind): kind is CheckKind => (CHECK_KINDS as readonly unknown[]).includes(kind));
  const planApproval = value.planApproval === 'required';
  return planApproval || checks.length > 0 ? { planApproval, checks } : null;
}

/** A model as an export names it: provider and model name, never a deployment's key. */
export interface ModelRef {
  provider: string;
  modelName: string;
}

/** A configured model of the importer's, as GET …/ai-models/available/llm lists it (the fields read here). */
export interface ConfiguredModel {
  provider: string;
  modelName: string;
  modelFriendlyName?: string;
}

/**
 * How a model reads to a person: the display name the importer's own configured model gives it (an export
 * names models only by provider and model name), else "provider / model". Never a key or an id.
 */
export function modelDisplayName(ref: ModelRef, configured: readonly ConfiguredModel[]): string {
  const match = configured.find(
    (m) => m.provider.toLowerCase() === ref.provider.toLowerCase() && m.modelName === ref.modelName,
  );
  const friendly = match?.modelFriendlyName?.trim();
  return friendly ? friendly : `${ref.provider} / ${ref.modelName}`;
}

/**
 * The same for a label the import answers with (`provider/model`, or `role: provider/model` for a sub-agent
 * role); any other label is shown as given.
 */
export function modelLabelDisplayName(label: string, configured: readonly ConfiguredModel[]): string {
  const role = /^([^:/]+): (.+)$/.exec(label);
  const body = role ? role[2]! : label;
  const slash = body.indexOf('/');
  if (slash <= 0 || slash === body.length - 1) return label;
  const name = modelDisplayName(
    { provider: body.slice(0, slash).trim(), modelName: body.slice(slash + 1).trim() },
    configured,
  );
  return role ? `${role[1]}: ${name}` : name;
}

export type ReadExport =
  | { ok: true; document: Json; preview: ImportPreview }
  | { ok: false; problem: 'notExport' | 'newerSchema' | 'tooBig'; schemaVersion?: number };

export interface ImportResult {
  agentKey: string;
  name: string;
  bound: Array<{ kind: string; name: string }>;
  rebind: Rebind[];
  excluded: ExcludedItem[];
  /** Policy sections the file holds that this edition does not offer: the agent was imported without them. */
  notImported: NotImported[];
}

export interface NotImported {
  section: string;
  reason: 'not_offered';
}

const budgetOf = (document: Json, key: string): number | null => {
  const policy = isObject(document.policy) ? document.policy : {};
  const budgets = isObject(policy.budgets) ? policy.budgets : {};
  return typeof budgets[key] === 'number' ? (budgets[key] as number) : null;
};

/** Reads a chosen file: an agent export of a version this build knows, or why not. Nothing is sent. */
export function readAgentExport(text: string): ReadExport {
  if (text.length > AGENT_CONFIG_MAX_BYTES) return { ok: false, problem: 'tooBig' };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, problem: 'notExport' };
  }
  if (!isObject(parsed) || parsed.format !== AGENT_CONFIG_FORMAT || !isObject(parsed.agent)) {
    return { ok: false, problem: 'notExport' };
  }
  if (typeof parsed.schemaVersion !== 'number' || !AGENT_CONFIG_SCHEMA_VERSIONS.includes(parsed.schemaVersion)) {
    return {
      ok: false,
      problem: 'newerSchema',
      ...(typeof parsed.schemaVersion === 'number' ? { schemaVersion: parsed.schemaVersion } : {}),
    };
  }
  const agent = parsed.agent;
  const source = isObject(parsed.source) ? parsed.source : {};
  const names = (value: unknown, pick: (entry: Json) => string | null): string[] =>
    list(value)
      .filter(isObject)
      .map(pick)
      .filter((name): name is string => name !== null);
  return {
    ok: true,
    document: parsed,
    preview: {
      name: str(agent.name) ?? '',
      sourceVersion: typeof source.agentVersion === 'number' ? source.agentVersion : null,
      models: list(agent.models)
        .filter(isObject)
        .map((m) => ({ provider: str(m.provider) ?? '', modelName: str(m.modelName) ?? '' }))
        .filter((m) => m.provider !== '' && m.modelName !== ''),
      actions: names(agent.toolsets, (t) => str(t.instanceName) ?? str(t.displayName) ?? str(t.name)),
      mcpServers: names(agent.mcpServers, (s) => str(s.displayName) ?? str(s.name)),
      knowledge: names(agent.knowledge, (k) => str(k.name)),
      skills: list(agent.skills).filter((s): s is string => typeof s === 'string'),
      maxTurns: budgetOf(parsed, 'maxTurns'),
      maxCostUsd: budgetOf(parsed, 'maxCostUsd'),
      gates: gatesOf(agent.gates),
    },
  };
}

export function toImportResult(value: unknown): ImportResult | null {
  if (!isObject(value)) return null;
  const agentKey = str(value.agentKey);
  if (agentKey === null) return null;
  return {
    agentKey,
    name: str(value.name) ?? '',
    bound: list(value.bound)
      .filter(isObject)
      .map((b) => ({ kind: String(b.kind ?? ''), name: String(b.name ?? '') }))
      .filter((b) => b.name !== ''),
    rebind: list(value.rebind)
      .filter(isObject)
      .map((r) => ({
        kind: (REBIND_KINDS as readonly string[]).includes(String(r.kind)) ? (r.kind as RebindKind) : 'model',
        name: String(r.name ?? ''),
        reason: (REBIND_REASONS as readonly string[]).includes(String(r.reason)) ? (r.reason as RebindReason) : 'not_found',
      }))
      .filter((r) => r.name !== ''),
    excluded: list(value.excluded).filter((e): e is ExcludedItem => (EXCLUDED_ITEMS as readonly unknown[]).includes(e)),
    notImported: list(value.notImported)
      .filter(isObject)
      .map((n) => str(n.section))
      .filter((section): section is string => section !== null)
      .map((section) => ({ section, reason: 'not_offered' as const })),
  };
}

/** The file name the server would give: the agent's name, made safe, `.agent.json`. */
export function exportFileName(name: string): string {
  const safe = name
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
  return `${safe === '' ? 'agent' : safe}.agent.json`;
}

/** Kept for the tab that imported, so the builder's "needs your choice" list survives a reload until it is closed. */
export function saveImportResult(result: ImportResult): void {
  try {
    window.sessionStorage.setItem(IMPORT_RESULT_KEY + result.agentKey, JSON.stringify(result));
  } catch {
    // Storage full or blocked: the builder opens without the list.
  }
}

export function readImportResult(agentKey: string): ImportResult | null {
  try {
    const raw = window.sessionStorage.getItem(IMPORT_RESULT_KEY + agentKey);
    return raw === null ? null : toImportResult(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function clearImportResult(agentKey: string): void {
  try {
    window.sessionStorage.removeItem(IMPORT_RESULT_KEY + agentKey);
  } catch {
    // Nothing to clear.
  }
}

export const AgentConfigApi = {
  /** The export document of the live version by default, a published version, or the draft. */
  async exportAgent(agentKey: string, version: ExportVersion | null): Promise<Json> {
    const { data } = await apiClient.get<Json>(`${AGENTS_BASE_URL}/${encodeURIComponent(agentKey)}/export`, {
      params: version === null ? {} : { version: String(version) },
      suppressErrorToast: true,
    });
    return data;
  },

  /** The importer's configured models, for their display names; an unreadable list names models plainly. */
  async configuredModels(): Promise<ConfiguredModel[]> {
    try {
      const { data } = await apiClient.get<{ models?: unknown }>('/api/v1/configurationManager/ai-models/available/llm', {
        suppressErrorToast: true,
      });
      return list(data?.models)
        .filter(isObject)
        .map((m) => ({
          provider: str(m.provider) ?? '',
          modelName: str(m.modelName) ?? '',
          modelFriendlyName: str(m.modelFriendlyName) ?? undefined,
        }))
        .filter((m) => m.provider !== '' && m.modelName !== '');
    } catch {
      return [];
    }
  },

  async importAgent(document: Json): Promise<ImportResult> {
    const { data } = await apiClient.post<unknown>(`${AGENTS_BASE_URL}/import`, document, { suppressErrorToast: true });
    const result = toImportResult(data);
    if (result === null) throw new Error('The import answered without an agent');
    return result;
  },
};

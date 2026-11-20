/** Non-fatal stream event: the run went on without these tools. */
export const TOOLS_UNAVAILABLE_EVENT = 'tools_unavailable';

export type UnavailableToolKind = 'toolset' | 'mcp';
export type UnavailableToolProblem = 'not_configured' | 'not_authenticated' | 'not_found';

/** A toolset or MCP server the person asking could not use on this run. */
export interface UnavailableTool {
  kind: UnavailableToolKind;
  instanceId: string;
  displayName: string;
  name?: string;
  problem: UnavailableToolProblem;
  /** MCP: none | api_token | oauth | headers. Usually absent for toolsets. */
  authMode?: string;
  /** One credential for everyone, which only an administrator can enter. */
  sharedCredential: boolean;
  /** The person asking can connect it themselves. */
  userCanConnect: boolean;
}

export interface ToolsUnavailableNotice {
  agentId?: string;
  serviceAccount: boolean;
  items: UnavailableTool[];
}

const KINDS: ReadonlySet<string> = new Set<UnavailableToolKind>(['toolset', 'mcp']);
const PROBLEMS: ReadonlySet<string> = new Set<UnavailableToolProblem>(['not_configured', 'not_authenticated', 'not_found']);
const MAX_ITEMS = 50;

function nonEmpty(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

export function parseUnavailableTool(raw: unknown): UnavailableTool | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const instanceId = nonEmpty(r.instanceId);
  if (!instanceId || typeof r.kind !== 'string' || !KINDS.has(r.kind)) return null;
  if (typeof r.problem !== 'string' || !PROBLEMS.has(r.problem)) return null;
  const name = nonEmpty(r.name);
  const authMode = nonEmpty(r.authMode);
  return {
    kind: r.kind as UnavailableToolKind,
    instanceId,
    displayName: nonEmpty(r.displayName) ?? name ?? instanceId,
    ...(name ? { name } : {}),
    problem: r.problem as UnavailableToolProblem,
    ...(authMode ? { authMode } : {}),
    sharedCredential: r.sharedCredential === true,
    userCanConnect: r.userCanConnect === true,
  };
}

/** The well-formed items of `raw`; anything else is dropped. */
export function parseUnavailableTools(raw: unknown): UnavailableTool[] {
  if (!Array.isArray(raw)) return [];
  const items: UnavailableTool[] = [];
  const seen = new Set<string>();
  for (const entry of raw.slice(0, MAX_ITEMS)) {
    const item = parseUnavailableTool(entry);
    if (!item || seen.has(`${item.kind}:${item.instanceId}`)) continue;
    seen.add(`${item.kind}:${item.instanceId}`);
    items.push(item);
  }
  return items;
}

/** The `tools_unavailable` event's value, or null when it lists nothing usable. */
export function toolsUnavailableNotice(value: unknown): ToolsUnavailableNotice | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  const items = parseUnavailableTools(v.items);
  if (items.length === 0) return null;
  return {
    agentId: nonEmpty(v.agentId),
    serviceAccount: v.serviceAccount === true,
    items,
  };
}

/** Whether the person in the chat can connect this item here. */
export function connectableHere(item: UnavailableTool, serviceAccount: boolean): boolean {
  if (serviceAccount || !item.userCanConnect || item.sharedCredential || item.problem === 'not_found') return false;
  if (item.kind === 'toolset') return true;
  return item.authMode === 'oauth' || item.authMode === 'api_token' || item.authMode === 'headers';
}

export type ConnectToolsPresentation = 'card' | 'chip';

/**
 * How an answer that went on without some tools offers to connect them. The full card when the answer names one
 * of them (the model said it needed it); otherwise a small chip, so an answer that never needed them stays quiet.
 */
export function connectToolsPresentation(answer: string, items: readonly UnavailableTool[]): ConnectToolsPresentation {
  const text = answer.toLocaleLowerCase();
  const named = items.some((item) =>
    [item.displayName, item.name].some((label) => {
      const needle = label?.trim().toLocaleLowerCase();
      return Boolean(needle && needle.length >= 2 && text.includes(needle));
    }),
  );
  return named ? 'card' : 'chip';
}

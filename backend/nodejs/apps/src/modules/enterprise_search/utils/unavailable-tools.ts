import { IUnavailableTool } from '../types/conversation.interfaces';

export const MAX_UNAVAILABLE_TOOLS = 50;
const MAX_TEXT = 200;

const KINDS: ReadonlySet<string> = new Set(['toolset', 'mcp']);
const PROBLEMS: ReadonlySet<string> = new Set([
  'not_configured',
  'not_authenticated',
  'not_found',
]);

const text = (value: unknown): string | undefined => {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim().slice(0, MAX_TEXT);
  return trimmed === '' ? undefined : trimmed;
};

const toolOf = (raw: unknown): IUnavailableTool | null => {
  if (typeof raw !== 'object' || raw === null) return null;
  const item = raw as Record<string, unknown>;
  const instanceId = text(item.instanceId);
  if (
    instanceId === undefined ||
    typeof item.kind !== 'string' ||
    !KINDS.has(item.kind) ||
    typeof item.problem !== 'string' ||
    !PROBLEMS.has(item.problem)
  ) {
    return null;
  }
  const name = text(item.name);
  const authMode = text(item.authMode);
  return {
    kind: item.kind as IUnavailableTool['kind'],
    instanceId,
    displayName: text(item.displayName) ?? name ?? instanceId,
    ...(name !== undefined && { name }),
    problem: item.problem as IUnavailableTool['problem'],
    ...(authMode !== undefined && { authMode }),
    sharedCredential: item.sharedCredential === true,
    userCanConnect: item.userCanConnect === true,
  };
};

/**
 * The AI backend's list as it may be stored and shown: known kinds and problems only, plain strings,
 * one entry per tool, bounded. Anything else is dropped rather than trusted.
 */
export function sanitizeUnavailableTools(raw: unknown): IUnavailableTool[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const tools: IUnavailableTool[] = [];
  for (const entry of raw) {
    const tool = toolOf(entry);
    if (tool === null) continue;
    const key = `${tool.kind}:${tool.instanceId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    tools.push(tool);
    if (tools.length >= MAX_UNAVAILABLE_TOOLS) break;
  }
  return tools;
}

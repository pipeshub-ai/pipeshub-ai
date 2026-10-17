import type { ApiToolset } from '../../workspace/api-toolsets/types';

/**
 * An agent's API toolset operations: one `"<toolsetId>:<toolName>"` string each (`apiToolsetTools`).
 * The builder keeps the list on the agent-core node's config, so the canvas dirty check,
 * save and reconstruction treat it like the core's other settings.
 */
const SEPARATOR = ':';

export const attachmentEntry = (toolsetId: string, toolName: string) => `${toolsetId}${SEPARATOR}${toolName}`;

/** The tool names attached from one toolset. */
export function attachedToolNames(entries: string[], toolsetId: string): Set<string> {
  const prefix = `${toolsetId}${SEPARATOR}`;
  return new Set(entries.filter((e) => e.startsWith(prefix)).map((e) => e.slice(prefix.length)));
}

/**
 * The list with one operation attached or detached. Entries for toolsets or operations that no
 * longer exist are dropped too: the backend refuses to save an agent that still names them.
 */
export function toggleAttachment(
  entries: string[],
  toolsets: Pick<ApiToolset, 'id' | 'operations'>[],
  toolsetId: string,
  toolName: string,
  attached: boolean,
): string[] {
  const offered = new Set(toolsets.flatMap((ts) => ts.operations.map((op) => attachmentEntry(ts.id, op.toolName))));
  const target = attachmentEntry(toolsetId, toolName);
  const kept = entries.filter((e) => e !== target && offered.has(e));
  return attached && offered.has(target) ? [...kept, target] : kept;
}

const sameEntries = (a: string[], b: string[]) => a.length === b.length && new Set([...a, ...b]).size === a.length;

/**
 * What to send on save: the list only when it differs from what the agent has stored, so an
 * unrelated save never re-checks (and fails on) operations an admin has since removed.
 */
export function changedAttachments(current: unknown, stored: string[] | undefined): { apiToolsetTools?: string[] } {
  if (!Array.isArray(current)) return {};
  const next = current.filter((e): e is string => typeof e === 'string');
  return sameEntries(next, stored ?? []) ? {} : { apiToolsetTools: next };
}

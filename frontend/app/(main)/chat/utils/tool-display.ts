/**
 * Shared tool name display utilities used by both streaming status messages
 * and the agent activity timeline.
 */

/** Drops any `{app}__` namespace prefix, splits on separators, title-cases. */
export function humanizeToolName(name: string): string {
  const segment = name.includes('__') ? name.slice(name.lastIndexOf('__') + 2) : name;
  const words = segment.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return 'Used a tool';
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
}

/** Extracts the toolset/connector prefix display name. */
export function extractToolsetLabel(toolName: string | undefined): string | undefined {
  if (!toolName || !toolName.includes('__')) return undefined;
  const prefix = toolName.slice(0, toolName.lastIndexOf('__'));
  if (!prefix) return undefined;
  const words = prefix.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return undefined;
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

/** Human-friendly label for a tool call (past tense, for timeline).
 *  Uses backend-provided `displayName` when available, falls back to generic humanization. */
export function toolActivityLabel(toolName: string | undefined, displayName?: string): string {
  if (displayName) return displayName;
  if (!toolName) return 'Used a tool';
  return humanizeToolName(toolName);
}

/** Human-friendly label for a tool call in progress (present tense, for streaming status). */
export function toolStatusLabel(toolName: string, displayName?: string): string {
  if (displayName) return displayName;
  return `Using ${humanizeToolName(toolName)}`;
}

import type { AgentVersioning } from '../types';

/** What a published version does, in the words the versions panel shows; ids and prompts stay out. */
export interface VersionDefinitionSummary {
  models: string[];
  toolsets: string[];
  mcpServers: string[];
  knowledgeCount: number;
  skillsCount: number;
  instructionsLength: number;
}

const asList = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

const textOf = (record: Record<string, unknown>, ...keys: string[]): string => {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
};

/** A stored model is `modelKey_modelName` (the key never has an underscore); older agents may hold objects. */
function modelLabel(entry: unknown): string {
  if (typeof entry === 'string') {
    const cut = entry.indexOf('_');
    return cut >= 0 ? entry.slice(cut + 1) : entry;
  }
  if (entry && typeof entry === 'object') {
    return textOf(entry as Record<string, unknown>, 'modelFriendlyName', 'modelName', 'modelKey');
  }
  return '';
}

function namedEntries(value: unknown, ...keys: string[]): string[] {
  const names = asList(value)
    .map((entry) => (entry && typeof entry === 'object' ? textOf(entry as Record<string, unknown>, ...keys) : ''))
    .filter(Boolean);
  return [...new Set(names)];
}

export function summarizeVersionDefinition(definition: Record<string, unknown> | null | undefined): VersionDefinitionSummary {
  const d = definition ?? {};
  const instructions = typeof d.instructions === 'string' ? d.instructions : '';
  return {
    models: [...new Set(asList(d.models).map(modelLabel).filter(Boolean))],
    toolsets: namedEntries(d.toolsets, 'instanceName', 'displayName', 'name'),
    mcpServers: namedEntries(d.mcpServers, 'instanceName', 'displayName', 'name'),
    knowledgeCount: asList(d.knowledge).length,
    skillsCount: asList(d.skills).length,
    instructionsLength: instructions.trim().length,
  };
}

/**
 * Whether the agent has edits chats do not run yet: saved to the draft (the server's view) or still only in the
 * editor (`localEdits`).
 */
export function hasUnpublishedChanges(versioning: AgentVersioning | null | undefined, localEdits: boolean): boolean {
  return localEdits || Boolean(versioning?.unpublishedChanges);
}

/** The number the next publish or rollback takes; null when the latest version is unknown. */
export function nextVersionNumber(versioning: Pick<AgentVersioning, 'latestVersion'> | null | undefined): number | null {
  const latest = versioning?.latestVersion;
  return typeof latest === 'number' && latest >= 1 ? latest + 1 : null;
}

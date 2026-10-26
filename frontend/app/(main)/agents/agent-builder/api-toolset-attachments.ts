import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { ApiToolset } from '../../workspace/api-toolsets/types';
import type { AgentToolset } from '../types';

/**
 * API toolsets go through the builder's toolset flow (sidebar rows, drag payloads, canvas nodes).
 * They share one toolset type, so the sidebar groups them together, and each node stays one
 * instance through its `instanceId`. Nodes are told apart from other toolsets by `config.category`.
 * On the agent they are a flat list, `apiToolsetTools`: each operation's `"<toolsetId>:<toolName>"`,
 * which is also the tool's `fullName` on the node.
 */
export const API_TOOLSET_TYPE = 'api';
export const API_TOOLSET_CATEGORY = 'api-toolset';

const SEPARATOR = ':';

export const attachmentEntry = (toolsetId: string, toolName: string) => `${toolsetId}${SEPARATOR}${toolName}`;

export type ApiToolsetSidebarRow = BuilderSidebarToolset & { apiToolset: ApiToolset };

export const isApiToolsetRow = (ts: BuilderSidebarToolset): ts is ApiToolsetSidebarRow => 'apiToolset' in ts;

export const isApiToolsetNode = (config: Record<string, unknown> | undefined) =>
  config?.category === API_TOOLSET_CATEGORY;

/** The sidebar row for an API toolset; only a connected one can be dragged, like any action. */
export function apiToolsetSidebarRow(toolset: ApiToolset): ApiToolsetSidebarRow {
  const tools = toolset.operations.map((op) => ({
    name: op.toolName,
    fullName: attachmentEntry(toolset.id, op.toolName),
    description: op.summary,
  }));
  return {
    name: toolset.name,
    normalized_name: API_TOOLSET_TYPE,
    displayName: toolset.name,
    description: toolset.description,
    iconPath: '',
    category: API_TOOLSET_CATEGORY,
    toolCount: tools.length,
    tools,
    isConfigured: true,
    isAuthenticated: toolset.isConnected,
    isFromRegistry: false,
    instanceId: toolset.id,
    instanceName: toolset.name,
    toolsetType: API_TOOLSET_TYPE,
    apiToolset: toolset,
  };
}

/**
 * The agent's entries as toolsets to rebuild nodes from: every operation offered, the attached
 * ones selected. Entries the toolset no longer offers (or a toolset that's gone, named
 * `fallbackName`) stay on the node, as a removed action does, so a save never drops them unseen.
 */
export function apiToolsetsOnAgent(entries: string[], toolsets: ApiToolset[], fallbackName: string): AgentToolset[] {
  const attachedById = new Map<string, string[]>();
  for (const entry of entries) {
    const at = entry.indexOf(SEPARATOR);
    if (at <= 0) continue;
    const id = entry.slice(0, at);
    attachedById.set(id, [...(attachedById.get(id) ?? []), entry.slice(at + 1)]);
  }
  return Array.from(attachedById, ([id, attached]) => {
    const toolset = toolsets.find((ts) => ts.id === id);
    const offered = toolset?.operations.map((op) => ({ name: op.toolName, description: op.summary })) ?? [];
    const stale = attached.filter((name) => !offered.some((op) => op.name === name));
    return {
      _key: id,
      instanceId: id,
      instanceName: toolset?.name,
      name: API_TOOLSET_TYPE,
      displayName: toolset?.name || fallbackName,
      type: API_TOOLSET_CATEGORY,
      selectedTools: attached,
      tools: [...offered, ...stale.map((name) => ({ name, description: '' }))].map((op) => ({
        _key: '',
        name: op.name,
        fullName: attachmentEntry(id, op.name),
        description: op.description,
        toolsetName: API_TOOLSET_TYPE,
      })),
    };
  });
}

const sameEntries = (a: string[], b: string[]) => a.length === b.length && new Set([...a, ...b]).size === a.length;

/**
 * What to send on save: the list only when it differs from what the agent has stored, so an
 * unrelated save never re-checks (and fails on) operations an admin has since removed.
 */
export function changedAttachments(current: string[], stored: string[] | undefined): { apiToolsetTools?: string[] } {
  const next = Array.from(new Set(current));
  return sameEntries(next, stored ?? []) ? {} : { apiToolsetTools: next };
}

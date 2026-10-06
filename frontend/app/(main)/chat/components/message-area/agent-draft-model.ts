import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { ToolsetReference } from '@/app/(main)/agents/agent-builder/types';
import type { WebSearchProviderType } from '@/app/(main)/workspace/web-search/types';
import { toolsetPayloadName } from '@/app/(main)/agents/agent-builder/extract-agent-config';
import type { AgentDraft, DraftKnowledge, DraftTool, DraftToolset } from '../../types';

/** A tool to attach, with the toolset instance that offers it. */
export interface SelectedTool {
  toolset: DraftToolset;
  tool: DraftTool;
}

export const toolTickKey = (instanceId: string, fullName: string): string => `${instanceId}|${fullName}`;

/** `create_issue` and `jira__create_issue` both read as "Create issue". */
export function toolDisplayName(name: string): string {
  const base = name.split(/_{2,}|\./).pop() ?? name;
  const words = base.replace(/[_-]+/g, ' ').trim();
  return words ? `${words.charAt(0).toUpperCase()}${words.slice(1)}` : name;
}

/** The raw name is what is matched and sent; this is only what is read. */
export function legacyToolLabel(name: string): string {
  const [app, ...rest] = name.split(/_{2,}/);
  if (rest.length === 0) return name.replace(/_/g, ' ');
  return `${app.charAt(0).toUpperCase()}${app.slice(1)}: ${rest.join(' ').replace(/_/g, ' ')}`;
}

export function toDraftToolset(toolset: BuilderSidebarToolset): DraftToolset | null {
  if (!toolset.instanceId) return null;
  return {
    instanceId: toolset.instanceId,
    instanceName: toolset.instanceName ?? null,
    name: toolset.normalized_name || toolset.name,
    displayName: toolset.displayName,
    iconPath: toolset.iconPath,
    category: toolset.category,
    tools: toolset.tools.map((tool) => ({ name: tool.name, fullName: tool.fullName, description: tool.description })),
  };
}

/** Folds `incoming` into `groups`, one entry per instance, keeping the tools already there. */
export function mergeToolsets(groups: DraftToolset[], incoming: DraftToolset[]): DraftToolset[] {
  const byInstance = new Map(groups.map((g) => [g.instanceId, g]));
  for (const next of incoming) {
    const have = byInstance.get(next.instanceId);
    if (!have) {
      byInstance.set(next.instanceId, next);
      continue;
    }
    const known = new Set(have.tools.map((t) => t.fullName));
    byInstance.set(next.instanceId, { ...have, tools: [...have.tools, ...next.tools.filter((t) => !known.has(t.fullName))] });
  }
  return [...byInstance.values()];
}

export function toolsetReferences(selected: SelectedTool[]): ToolsetReference[] {
  const byInstance = new Map<string, ToolsetReference>();
  for (const { toolset, tool } of selected) {
    const entry = byInstance.get(toolset.instanceId) ?? {
      id: toolset.instanceId,
      instanceId: toolset.instanceId,
      ...(toolset.instanceName ? { instanceName: toolset.instanceName } : {}),
      name: toolsetPayloadName(toolset.name),
      displayName: toolset.displayName,
      type: toolset.category || 'app',
      tools: [],
    };
    if (!entry.tools?.some((t) => t.fullName === tool.fullName)) {
      entry.tools?.push({ name: tool.name, fullName: tool.fullName, description: tool.description ?? '' });
    }
    byInstance.set(toolset.instanceId, entry);
  }
  return [...byInstance.values()];
}

export function webSearchPayload(webSearch: AgentDraft['webSearch']) {
  if (!webSearch) return undefined;
  return {
    provider: webSearch.provider as WebSearchProviderType,
    providerKey: '',
    providerLabel: webSearch.providerLabel,
  };
}

/** Collections and connectors a draft starts with: the resolved sources, else the bare ids of an older draft. */
export function initialKnowledge(draft: AgentDraft): DraftKnowledge[] {
  if (draft.knowledgeSources && draft.knowledgeSources.length > 0) return draft.knowledgeSources;
  return draft.knowledge.map((id) => ({ id, name: '', kind: 'collection' as const }));
}

export const hasResolvedDraft = (draft: AgentDraft): boolean =>
  Boolean(draft.knowledgeSources?.length || draft.actions?.length || draft.webSearch || draft.unresolved?.length);

/** The model names tools as it sees them, `jira__create_issue`; the toolsets API says `jira.create_issue`. */
export const toolKey = (name: string): string => name.toLowerCase().replace(/_{2,}/, '.');

export interface LegacyToolOption {
  name: string;
  source?: SelectedTool;
}

export function resolveLegacyTools(names: string[], toolsets: BuilderSidebarToolset[] | null): LegacyToolOption[] {
  return names.map((name) => {
    const wanted = toolKey(name);
    for (const raw of toolsets ?? []) {
      if (!raw.instanceId || !raw.isConfigured || !raw.isAuthenticated) continue;
      const tool = raw.tools.find(
        (x) =>
          x.name.toLowerCase() === wanted ||
          toolKey(x.fullName) === wanted ||
          `${(raw.normalized_name || raw.name).toLowerCase()}.${x.name.toLowerCase()}` === wanted,
      );
      const toolset = toDraftToolset(raw);
      if (tool && toolset) {
        return { name, source: { toolset, tool: { name: tool.name, fullName: tool.fullName, description: tool.description } } };
      }
    }
    return { name };
  });
}


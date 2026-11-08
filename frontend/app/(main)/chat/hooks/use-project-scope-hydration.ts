'use client';

import { useEffect } from 'react';
import useSWR, { type SWRResponse } from 'swr';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import { ToolsetsApi, MAX_TOOLSETS_LIST_LIMIT } from '@/app/(main)/toolsets/api';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import { useFeatureFlagsStore, selectMcpEnabled } from '@/lib/store/feature-flags-store';
import { useChatStore, type ProjectChatScope } from '@/chat/store';
import type { ProjectDetail } from '@/chat/project-types';
import {
  bareToolFullName,
  buildCatalogMcpGroups,
  buildCatalogToolGroups,
  restrictGroupsToBareNames,
  type CatalogToolGroupRow,
} from '@/chat/tool-groups';

export interface ToolCatalog {
  toolGroups: CatalogToolGroupRow[];
  mcpGroups: CatalogToolGroupRow[];
}

const EMPTY_CATALOG: ToolCatalog = { toolGroups: [], mcpGroups: [] };

type ToolCatalogKey = readonly [typeof SWR_NS.myToolCatalog, { mcpEnabled: boolean }];

/** The caller's authenticated toolsets + MCP servers. Not cached here: read it through `useToolCatalog`. */
export async function fetchToolCatalog(mcpEnabled: boolean): Promise<ToolCatalog> {
  const [toolsetsRes, mcpRes] = await Promise.all([
    ToolsetsApi.getAllMyToolsets({ limitPerPage: MAX_TOOLSETS_LIST_LIMIT, authStatus: 'authenticated' }),
    mcpEnabled ? McpServersApi.getMyMcpServers(true) : Promise.resolve({ instances: [] as McpMyServerEntry[] }),
  ]);
  const authenticatedMcp = (mcpRes.instances || []).filter((e) => e.isAuthenticated);
  return {
    toolGroups: buildCatalogToolGroups(toolsetsRes.toolsets),
    mcpGroups: buildCatalogMcpGroups(authenticatedMcp),
  };
}

/**
 * The tool catalog shared by the project settings picker and the project composer: one request
 * for both, reused for five minutes, so opening a project and then its chat is one round trip.
 */
export function useToolCatalog(mcpEnabled: boolean, enabled = true): SWRResponse<ToolCatalog> {
  const key: ToolCatalogKey | null = enabled ? [SWR_NS.myToolCatalog, { mcpEnabled }] : null;
  return useSWR(key, ([, params]: ToolCatalogKey) => fetchToolCatalog(params.mcpEnabled), SWR_POLICY.list5m);
}

/** Pure projection of a project's settings onto the composer allow-list. */
export function buildProjectChatScope(project: ProjectDetail, catalog: ToolCatalog): ProjectChatScope {
  const apps = project.knowledgeScope?.apps ?? [];
  const kb = project.knowledgeScope?.kb ?? [];
  const appliedApps = project.appliedFilters?.apps ?? [];
  const appliedKb = project.appliedFilters?.kb ?? [];

  const connectors: ProjectChatScope['connectors'] = apps.map((id) => {
    const node = appliedApps.find((n) => n.id === id);
    return { id, label: node?.name ?? id, connectorKind: node?.connector ?? '' };
  });
  const knowledgeCollectionRows: ProjectChatScope['knowledgeCollectionRows'] = kb.map((id) => {
    const node = appliedKb.find((n) => n.id === id);
    return { id, name: node?.name ?? id, sourceType: node?.connector || undefined };
  });
  // A record group, folder or record the project lists is a row of its own,
  // beside the connectors or the collections it belongs to.
  const belowApps = { recordGroups: 'recordGroup', records: 'folder' } as const;
  for (const key of ['recordGroups', 'records'] as const) {
    for (const id of project.knowledgeScope?.[key] ?? []) {
      const node = project.appliedFilters?.[key]?.find((n) => n.id === id);
      const name = node?.name ?? id;
      const nodeType = node?.nodeType || belowApps[key];
      const kind = (node?.connector ?? '').trim();
      // A project stores the connector type of each node, not the connector it came from.
      const source = kind ? { id: `kind:${kind.toUpperCase()}`, label: '', kind } : undefined;
      if (kind.toUpperCase() === 'KB') {
        knowledgeCollectionRows.push({ id, name, sourceType: 'KB', nodeType, source });
      } else {
        connectors.push({ id, label: name, connectorKind: kind, nodeType, source });
      }
    }
  }

  const allowedBare = new Set((project.tools ?? []).map(bareToolFullName));
  const toolGroups = restrictGroupsToBareNames(catalog.toolGroups, allowedBare);
  const mcpGroups = restrictGroupsToBareNames(catalog.mcpGroups, allowedBare);

  return {
    projectId: project._id,
    connectors,
    knowledgeCollectionRows,
    knowledgeDefaults: {
      apps,
      kb,
      ...(project.knowledgeScope?.recordGroups?.length ? { recordGroups: project.knowledgeScope.recordGroups } : {}),
      ...(project.knowledgeScope?.records?.length ? { records: project.knowledgeScope.records } : {}),
    },
    linkedKnowledgeBaseId: project.linkedKnowledgeBaseId ?? null,
    toolGroups,
    mcpGroups,
    toolCatalogFullNames: [...toolGroups, ...mcpGroups].flatMap((g) => g.fullNames),
  };
}

/**
 * Keeps `useChatStore().projectScope` in sync with `project`. Re-runs whenever the project
 * object changes (settings edits, refetch), so the composer reflects new connectors/tools
 * without a reload. Clears the scope when `project` is null or on unmount.
 */
export function useProjectScopeHydration(project: ProjectDetail | null): void {
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  // Plain chat (no project) has no tool allow-list to build, so it asks for nothing.
  const { data: catalog } = useToolCatalog(mcpEnabled, project != null);
  const setProjectScope = useChatStore((s) => s.setProjectScope);

  useEffect(() => {
    // Connectors/collections need no fetch, so they show at once; tools fill in when the
    // catalog arrives and stay empty if it fails.
    setProjectScope(project ? buildProjectChatScope(project, catalog ?? EMPTY_CATALOG) : null);
  }, [project, catalog, setProjectScope]);

  useEffect(() => () => setProjectScope(null), [setProjectScope]);
}

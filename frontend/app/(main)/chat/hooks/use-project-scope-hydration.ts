'use client';

import { useEffect } from 'react';
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

const CATALOG_TTL_MS = 5 * 60 * 1000;
let catalogCache: { mcpEnabled: boolean; fetchedAt: number; promise: Promise<ToolCatalog> } | null = null;

/**
 * The caller's authenticated toolsets + MCP servers, shared by the project settings picker and
 * the project composer. Cached briefly so opening a project and then its chat is one round trip.
 */
export function fetchToolCatalog(mcpEnabled: boolean): Promise<ToolCatalog> {
  const now = Date.now();
  if (catalogCache && catalogCache.mcpEnabled === mcpEnabled && now - catalogCache.fetchedAt < CATALOG_TTL_MS) {
    return catalogCache.promise;
  }
  const promise = (async (): Promise<ToolCatalog> => {
    const [toolsetsRes, mcpRes] = await Promise.all([
      ToolsetsApi.getAllMyToolsets({ limitPerPage: MAX_TOOLSETS_LIST_LIMIT, authStatus: 'authenticated' }),
      mcpEnabled ? McpServersApi.getMyMcpServers(true) : Promise.resolve({ instances: [] as McpMyServerEntry[] }),
    ]);
    const authenticatedMcp = (mcpRes.instances || []).filter((e) => e.isAuthenticated);
    return {
      toolGroups: buildCatalogToolGroups(toolsetsRes.toolsets),
      mcpGroups: buildCatalogMcpGroups(authenticatedMcp),
    };
  })();
  catalogCache = { mcpEnabled, fetchedAt: now, promise };
  // A failed fetch must not be served from cache for five minutes.
  promise.catch(() => {
    if (catalogCache?.promise === promise) catalogCache = null;
  });
  return promise;
}

/** Pure projection of a project's settings onto the composer allow-list. */
export function buildProjectChatScope(project: ProjectDetail, catalog: ToolCatalog): ProjectChatScope {
  const apps = project.knowledgeScope?.apps ?? [];
  const kb = project.knowledgeScope?.kb ?? [];
  const appliedApps = project.appliedFilters?.apps ?? [];
  const appliedKb = project.appliedFilters?.kb ?? [];

  const connectors = apps.map((id) => {
    const node = appliedApps.find((n) => n.id === id);
    return { id, label: node?.name ?? id, connectorKind: node?.connector ?? '' };
  });
  const knowledgeCollectionRows = kb.map((id) => {
    const node = appliedKb.find((n) => n.id === id);
    return { id, name: node?.name ?? id, sourceType: node?.connector || undefined };
  });

  const allowedBare = new Set((project.tools ?? []).map(bareToolFullName));
  const toolGroups = restrictGroupsToBareNames(catalog.toolGroups, allowedBare);
  const mcpGroups = restrictGroupsToBareNames(catalog.mcpGroups, allowedBare);

  return {
    projectId: project._id,
    connectors,
    knowledgeCollectionRows,
    knowledgeDefaults: { apps, kb },
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
  const setProjectScope = useChatStore((s) => s.setProjectScope);

  useEffect(() => {
    if (!project) {
      setProjectScope(null);
      return;
    }
    let cancelled = false;
    // Connectors/collections need no fetch; publish them immediately and fill tools in after.
    setProjectScope(buildProjectChatScope(project, { toolGroups: [], mcpGroups: [] }));
    fetchToolCatalog(mcpEnabled)
      .then((catalog) => {
        if (!cancelled) setProjectScope(buildProjectChatScope(project, catalog));
      })
      .catch(() => {
        // Knowledge scope already published; tools stay empty for this turn.
      });
    return () => {
      cancelled = true;
    };
  }, [project, mcpEnabled, setProjectScope]);

  useEffect(() => () => setProjectScope(null), [setProjectScope]);
}

'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AgentsApi,
  buildToolsCatalogFromToolsets,
  mergeToolsFromAgentDetail,
} from '../../api';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import { getConnectorIconConfig } from '@/app/components/ui/ConnectorIcon';
import { ASSISTANT_CTX } from '@/chat/store';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import type { AvailableLlmModel } from '@/chat/types';
import type { AgentDetail, KnowledgeHubAppNode } from '../../types';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { usePagedCatalog, type PagedCatalog } from './use-paged-catalog';
import type { CatalogPage } from './paged-catalog';
import type { AgentToolsListRow, KnowledgeBaseForBuilder, SkillForBuilder } from '../../types';
import { SkillsApi } from '../../../workspace/skills/personal/api';
import { McpServersApi } from '../../../workspace/mcp-servers/api';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import { ApiToolsetsApi } from '../../../workspace/api-toolsets/api';
import type { ApiToolset } from '../../../workspace/api-toolsets/types';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectActionsEnabled,
  selectSkillsEnabled,
  selectApiToolsetsEnabled,
  selectFeatureFlagsLoaded,
} from '@/lib/store/feature-flags-store';

/** Actions per page of the palette's catalog: the builder's load reads one page whatever the org holds. */
const TOOLSETS_PAGE = 100;

/** Non-hook read for use inside plain async functions (not React components). */
function isMcpEnabledNow(): boolean {
  return selectMcpEnabled(useFeatureFlagsStore.getState());
}

/** Non-hook read for use inside plain async functions (not React components). */
function isActionsEnabledNow(): boolean {
  return selectActionsEnabled(useFeatureFlagsStore.getState());
}

/** Non-hook read for use inside plain async functions (not React components). */
function isSkillsEnabledNow(): boolean {
  return selectSkillsEnabled(useFeatureFlagsStore.getState());
}

async function ensureFeatureFlagsLoaded(): Promise<void> {
  if (selectFeatureFlagsLoaded(useFeatureFlagsStore.getState())) return;
  await useFeatureFlagsStore.getState().fetchFlags();
}

/** Members can list API toolsets too; an error (e.g. the labs flag is off server-side) means none. */
async function loadApiToolsets(): Promise<ApiToolset[]> {
  await ensureFeatureFlagsLoaded();
  if (!selectApiToolsetsEnabled(useFeatureFlagsStore.getState())) return [];
  return ApiToolsetsApi.listInstances().catch(() => []);
}

async function loadSkillsCatalog(): Promise<
  Awaited<ReturnType<typeof SkillsApi.listAssignableSkills>>
> {
  await ensureFeatureFlagsLoaded();
  if (!isSkillsEnabledNow()) {
    return [];
  }
  try {
    return await SkillsApi.listAssignableSkills();
  } catch (err) {
    console.error('Failed to fetch skills catalog:', err);
    return [];
  }
}

/**
 * Map KnowledgeHubAppNode items to Connector[] for downstream compatibility.
 * The agent builder sidebar, node templates, and canvas drop handler all
 * consume Connector[]; this mapping avoids a widespread type refactor.
 */
function mapNodesToConnectors(nodes: KnowledgeHubAppNode[]): Connector[] {
  return nodes.map((node) => {
    const connectorType = node.connector || 'generic';
    const iconConfig = getConnectorIconConfig(connectorType);
    return {
      _key: node.id,
      name: node.name,
      type: connectorType,
      appGroup: connectorType,
      appDescription: '',
      appCategories: [],
      iconPath: iconConfig.svg || '',
      supportedAuthTypes: [],
      supportsRealtime: false,
      supportsSync: false,
      supportsAgent: true,
      scope: node.sharingStatus === 'personal' ? 'personal' : 'team',
      isActive: true,
      isAgentActive: true,
      isConfigured: true,
      isAuthenticated: true,
    };
  });
}

/** Models, the skills catalog and API toolsets: fetched once per hook mount (route remount resets the ref). */
async function fetchStaticBuilderResources() {
  const [models, skills, apiToolsets] = await Promise.all([
    fetchModelsForContext(ASSISTANT_CTX),
    loadSkillsCatalog(),
    loadApiToolsets(),
  ]);

  return {
    models: models ?? [],
    skills: skills.map<SkillForBuilder>((s) => ({
      name: s.name,
      description: s.description,
      category: s.category,
      isBuiltin: s.source === 'builtin',
    })),
    apiToolsets,
  };
}

/** Whose actions the palette lists: a service-account agent's own credentials, otherwise the person's. */
interface ToolsetScope {
  agentKey: string | null;
  isServiceAccount: boolean;
}

function toolsetScopeOf(agentDetails: AgentDetail | null, editingAgentKey: string | null): ToolsetScope {
  const agentKey = agentDetails?._key || editingAgentKey || null;
  return { agentKey, isServiceAccount: agentDetails?.isServiceAccount === true && agentKey !== null };
}

async function readToolsetPage(
  scope: ToolsetScope,
  page: number | null,
  query: string,
): Promise<CatalogPage<BuilderSidebarToolset, number>> {
  if (!isActionsEnabledNow()) return { items: [], next: null };
  const at = page ?? 1;
  const params = { page: at, limit: TOOLSETS_PAGE, search: query || undefined, includeRegistry: true };
  const { toolsets, hasNext, pagination } =
    scope.isServiceAccount && scope.agentKey
      ? await ToolsetsApi.getAgentToolsets(scope.agentKey, params)
      : await ToolsetsApi.getMyToolsets(params);
  return { items: toolsets, next: hasNext ? at + 1 : null, total: pagination?.total ?? null };
}

const kbKey = (kb: KnowledgeBaseForBuilder) => kb.id;
const appKey = (node: KnowledgeHubAppNode) => node.id;
const toolsetKey = (ts: BuilderSidebarToolset) => ts.instanceId || `type:${ts.toolsetType || ts.name}`;

/** One palette catalog's paging, as the sidebar offers it. */
export interface BuilderCatalogPaging {
  hasMore: boolean;
  total: number | null;
  loadingMore: boolean;
  more: () => Promise<void>;
}

const pagingOf = <T,>(catalog: PagedCatalog<T>): BuilderCatalogPaging => ({
  hasMore: catalog.hasMore,
  total: catalog.total,
  loadingMore: catalog.loadingMore,
  more: catalog.more,
});

async function loadMcpServersForAgentContext(
  agentDetails: AgentDetail | null,
  editingAgentKey: string | null
): Promise<McpMyServerEntry[]> {
  if (!isMcpEnabledNow()) return [];
  const isSvc = agentDetails?.isServiceAccount === true;
  const keyForMcp = agentDetails?._key || editingAgentKey || undefined;
  const { instances } =
    isSvc && keyForMcp
      ? await McpServersApi.getAgentMcpServers(keyForMcp, true)
      : await McpServersApi.getMyMcpServers(true);
  return instances;
}

export function useAgentBuilderData(editingAgentKey: string | null) {
  const [availableTools, setAvailableTools] = useState<AgentToolsListRow[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableLlmModel[]>([]);
  const [availableSkills, setAvailableSkills] = useState<SkillForBuilder[]>([]);
  const [apiToolsets, setApiToolsets] = useState<ApiToolset[]>([]);
  const [mcpServers, setMcpServers] = useState<McpMyServerEntry[]>([]);
  // False until a list arrives, and after a failed load: an empty list isn't "no servers" then.
  const [mcpServersLoaded, setMcpServersLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadedAgent, setLoadedAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  // R30-LAT-FU-1: the org's catalogs a page at a time; the agent's own grants come named in its answer.
  const knowledgeBases = usePagedCatalog(AgentsApi.knowledgeBasePage, kbKey, 'collections');
  const apps = usePagedCatalog(AgentsApi.knowledgeHubAppPage, appKey, 'connected apps');
  const toolsetScopeRef = useRef<ToolsetScope>({ agentKey: null, isServiceAccount: false });
  const readToolsets = useCallback(
    (page: number | null, query: string) => readToolsetPage(toolsetScopeRef.current, page, query),
    []
  );
  const toolsetCatalog = usePagedCatalog(readToolsets, toolsetKey, 'actions');
  const availableKnowledgeBases = knowledgeBases.items;
  const configuredConnectors = useMemo(() => mapNodesToConnectors(apps.items), [apps.items]);
  const toolsets = toolsetCatalog.items;

  const toolsetsSearchRef = useRef('');
  const staticResourcesLoadedRef = useRef(false);

  // A row's connect shortcut opens Your actions in another tab: pick up what was connected
  // there when the builder gets focus back. A failed request keeps the list it has.
  useEffect(() => {
    const refresh = () => {
      if (!selectApiToolsetsEnabled(useFeatureFlagsStore.getState())) return;
      ApiToolsetsApi.listInstances().then(setApiToolsets, () => {});
    };
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, []);

  const { first: firstToolsets, clear: clearToolsets } = toolsetCatalog;
  /** The first page of the actions for this agent, narrowed to `search` when given. */
  const refreshToolsets = useCallback(
    async (agentKey?: string | null, isServiceAccount?: boolean, search?: string) => {
      toolsetsSearchRef.current = search ?? '';
      if (!isActionsEnabledNow()) {
        clearToolsets();
        return;
      }
      const key = agentKey ?? null;
      toolsetScopeRef.current = { agentKey: key, isServiceAccount: Boolean(isServiceAccount) && key !== null };
      await firstToolsets(toolsetsSearchRef.current);
    },
    [clearToolsets, firstToolsets]
  );

  const { search: searchKnowledgeBases, rest: restKnowledgeBases } = knowledgeBases;
  const { search: searchApps, rest: restApps } = apps;
  const { search: searchToolsets } = toolsetCatalog;
  /** A palette query: each catalog's first page of matches from the server, beside what is read. */
  const searchCatalogs = useCallback(
    async (query: string) => {
      await Promise.all([searchKnowledgeBases(query), searchApps(query), searchToolsets(query)]);
    },
    [searchApps, searchKnowledgeBases, searchToolsets]
  );
  /** Every collection and app, for a whole group dropped on the canvas. */
  const loadAllKnowledge = useCallback(async () => {
    await Promise.all([restKnowledgeBases(), restApps()]);
  }, [restApps, restKnowledgeBases]);

  const refreshMcpServers = useCallback(
    async (agentKey?: string | null, isServiceAccount?: boolean) => {
      if (!isMcpEnabledNow()) {
        setMcpServers([]);
        return;
      }
      const svc = Boolean(isServiceAccount) && Boolean(agentKey);
      try {
        const { instances } = svc
          ? await McpServersApi.getAgentMcpServers(agentKey!, true)
          : await McpServersApi.getMyMcpServers(true);
        setMcpServers(instances);
        setMcpServersLoaded(true);
      } catch (err) {
        // Matches loadMcpServersForAgentContext's callers: this is awaited inside
        // Promise.all in refreshAgent (a rejection here would otherwise also discard the
        // sibling toolset refresh) and fired as `void refreshMcpServers()` from the OAuth
        // message listener, where a rejection would be silently unhandled either way.
        console.error('Failed to refresh MCP servers:', err);
        setMcpServers([]);
        setMcpServersLoaded(false);
      }
    },
    []
  );

  const refreshAgent = useCallback(
    async (agentKey: string, opts?: { knownAgent?: AgentDetail }) => {
      const agent = opts?.knownAgent ?? (await AgentsApi.getAgent(agentKey)).agent;
      if (agent) setLoadedAgent(agent);
      await Promise.all([
        refreshToolsets(agentKey, agent?.isServiceAccount, toolsetsSearchRef.current),
        refreshMcpServers(agentKey, agent?.isServiceAccount),
      ]);
    },
    [refreshToolsets, refreshMcpServers]
  );

  // Bumped by `retryLoad` to run the load again after an agent failed to load.
  const [loadAttempt, setLoadAttempt] = useState(0);
  const retryLoad = useCallback(() => setLoadAttempt((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;

    /** The agent's actions and MCP servers, scoped to whose credentials it runs with. */
    const loadAgentContext = async (agentDetails: AgentDetail | null) => {
      const scope = toolsetScopeOf(agentDetails, editingAgentKey);
      toolsetsSearchRef.current = '';
      const [, mcpServerEntries] = await Promise.all([
        refreshToolsets(scope.agentKey, scope.isServiceAccount, ''),
        loadMcpServersForAgentContext(agentDetails, editingAgentKey).catch((err) => {
          console.error('Failed to fetch MCP servers:', err);
          return null;
        }),
      ]);
      if (cancelled) return;
      setLoadedAgent(agentDetails);
      setMcpServers(mcpServerEntries ?? []);
      setMcpServersLoaded(mcpServerEntries !== null);
    };

    const run = async () => {
      try {
        setLoading(true);
        setError(null);

        const agentRead = editingAgentKey
          ? AgentsApi.getAgent(editingAgentKey, { quiet: true }).then((r) => r.agent).catch(() => null)
          : Promise.resolve(null);
        if (!staticResourcesLoadedRef.current) {
          const [staticRes, agentDetails] = await Promise.all([
            fetchStaticBuilderResources(),
            agentRead,
            knowledgeBases.first(),
            apps.first().catch((err) => {
              console.error('Failed to fetch knowledge hub app nodes:', err);
            }),
          ]);

          if (cancelled) return;

          setAvailableModels(staticRes.models);
          setAvailableSkills(staticRes.skills);
          setApiToolsets(staticRes.apiToolsets);
          await loadAgentContext(agentDetails);
          if (cancelled) return;
          staticResourcesLoadedRef.current = true;
        } else {
          const agentDetails = await agentRead;
          if (cancelled) return;
          await loadAgentContext(agentDetails);
        }
      } catch (e) {
        if (!cancelled) {
          console.error(e);
          setError('Failed to load builder resources');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void run();

    return () => {
      cancelled = true;
    };
  }, [editingAgentKey, loadAttempt]);

  useEffect(() => {
    setAvailableTools(mergeToolsFromAgentDetail(loadedAgent, buildToolsCatalogFromToolsets(toolsets)));
  }, [loadedAgent, toolsets]);

  return {
    availableTools,
    availableModels,
    availableKnowledgeBases,
    availableSkills,
    activeAgentConnectors: configuredConnectors,
    configuredConnectors,
    catalogs: {
      knowledgeBases: pagingOf(knowledgeBases),
      connectors: pagingOf(apps),
      toolsets: pagingOf(toolsetCatalog),
    },
    searchCatalogs,
    loadAllKnowledge,
    toolsets,
    apiToolsets,
    mcpServers,
    mcpServersLoaded,
    loading,
    loadedAgent,
    retryLoad,
    error,
    setError,
    refreshToolsets,
    refreshMcpServers,
    refreshAgent,
  };
}

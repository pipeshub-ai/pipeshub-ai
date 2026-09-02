'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AgentsApi,
  buildToolsCatalogFromToolsets,
  mergeToolsFromAgentDetail,
} from '../../api';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import { getConnectorIconConfig } from '@/app/components/ui/ConnectorIcon';
import { ChatApi } from '@/chat/api';
import type { AvailableLlmModel } from '@/chat/types';
import type { AgentDetail, KnowledgeHubAppNode } from '../../types';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { AgentToolsListRow, KnowledgeBaseForBuilder, SkillForBuilder } from '../../types';
import { SkillsApi } from '../../../workspace/skills/personal/api';
import { McpServersApi } from '../../../workspace/mcp-servers/api';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectActionsEnabled,
} from '@/lib/store/feature-flags-store';

const TOOLSETS_PAGE = 20;

/** Non-hook read for use inside plain async functions (not React components). */
function isMcpEnabledNow(): boolean {
  return selectMcpEnabled(useFeatureFlagsStore.getState());
}

/** Non-hook read for use inside plain async functions (not React components). */
function isActionsEnabledNow(): boolean {
  return selectActionsEnabled(useFeatureFlagsStore.getState());
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

/** Models, KB, knowledge-hub nodes, and the skills catalog — fetched once per hook mount (route remount resets the ref). */
async function fetchStaticBuilderResources() {
  const [models, kbResult, appNodes, skills] = await Promise.all([
    ChatApi.fetchAvailableLlms(),
    AgentsApi.getAllKnowledgeBasesForBuilder(),
    AgentsApi.getAllKnowledgeHubAppNodes().catch((err) => {
      console.error('Failed to fetch knowledge hub app nodes:', err);
      return [] as KnowledgeHubAppNode[];
    }),
    SkillsApi.listAssignableSkills().catch((err) => {
      console.error('Failed to fetch skills catalog:', err);
      return [] as Awaited<ReturnType<typeof SkillsApi.listAssignableSkills>>;
    }),
  ]);

  const configuredConnectors = mapNodesToConnectors(appNodes);

  return {
    models: models ?? [],
    knowledgeBases: kbResult.knowledgeBases ?? [],
    configuredConnectors,
    skills: skills.map<SkillForBuilder>((s) => ({
      name: s.name,
      description: s.description,
      category: s.category,
    })),
  };
}

async function loadToolsetsForAgentContext(
  agentDetails: AgentDetail | null,
  editingAgentKey: string | null
): Promise<BuilderSidebarToolset[]> {
  if (!isActionsEnabledNow()) return [];
  const isSvc = agentDetails?.isServiceAccount === true;
  const keyForToolsets = agentDetails?._key || editingAgentKey || undefined;
  if (isSvc && keyForToolsets) {
    return ToolsetsApi.getAllAgentToolsets(keyForToolsets, {
      includeRegistry: true,
      limitPerPage: TOOLSETS_PAGE,
    });
  }
  const { toolsets } = await ToolsetsApi.getAllMyToolsets({
    includeRegistry: true,
    limitPerPage: TOOLSETS_PAGE,
  });
  return toolsets;
}

/** Same scoping rule as `loadToolsetsForAgentContext`: service-account agents use their own credentials. */
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

async function fetchAgentAndToolsets(editingAgentKey: string | null) {
  const agentDetails = editingAgentKey
    ? await AgentsApi.getAgent(editingAgentKey).then((r) => r.agent).catch(() => null)
    : null;
  const [allToolsets, mcpServers] = await Promise.all([
    loadToolsetsForAgentContext(agentDetails, editingAgentKey),
    loadMcpServersForAgentContext(agentDetails, editingAgentKey).catch((err) => {
      console.error('Failed to fetch MCP servers:', err);
      return [] as McpMyServerEntry[];
    }),
  ]);
  return { agentDetails, allToolsets, mcpServers };
}

export function useAgentBuilderData(editingAgentKey: string | null) {
  const [availableTools, setAvailableTools] = useState<AgentToolsListRow[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableLlmModel[]>([]);
  const [availableKnowledgeBases, setAvailableKnowledgeBases] = useState<KnowledgeBaseForBuilder[]>(
    []
  );
  const [availableSkills, setAvailableSkills] = useState<SkillForBuilder[]>([]);
  const [configuredConnectors, setConfiguredConnectors] = useState<Connector[]>([]);
  const [toolsets, setToolsets] = useState<BuilderSidebarToolset[]>([]);
  const [mcpServers, setMcpServers] = useState<McpMyServerEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadedAgent, setLoadedAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toolsetsSearchRef = useRef('');
  const staticResourcesLoadedRef = useRef(false);

  const refreshToolsets = useCallback(
    async (agentKey?: string | null, isServiceAccount?: boolean, search?: string) => {
      toolsetsSearchRef.current = search ?? '';
      if (!isActionsEnabledNow()) {
        setToolsets([]);
        return;
      }
      const svc = Boolean(isServiceAccount) && Boolean(agentKey);
      const all = svc
        ? await ToolsetsApi.getAllAgentToolsets(agentKey!, {
            search: toolsetsSearchRef.current || undefined,
            includeRegistry: true,
            limitPerPage: TOOLSETS_PAGE,
          })
        : (
            await ToolsetsApi.getAllMyToolsets({
              search: toolsetsSearchRef.current || undefined,
              includeRegistry: true,
              limitPerPage: TOOLSETS_PAGE,
            })
          ).toolsets;
      setToolsets(all);
    },
    []
  );

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
      } catch (err) {
        // Matches loadMcpServersForAgentContext's callers: this is awaited inside
        // Promise.all in refreshAgent (a rejection here would otherwise also discard the
        // sibling toolset refresh) and fired as `void refreshMcpServers()` from the OAuth
        // message listener, where a rejection would be silently unhandled either way.
        console.error('Failed to refresh MCP servers:', err);
        setMcpServers([]);
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

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      try {
        setLoading(true);
        setError(null);

        if (!staticResourcesLoadedRef.current) {
          const [staticRes, agentPromise] = await Promise.all([
            fetchStaticBuilderResources(),
            editingAgentKey
              ? AgentsApi.getAgent(editingAgentKey).then((r) => r.agent).catch(() => null)
              : Promise.resolve(null),
          ]);

          if (cancelled) return;

          setAvailableModels(staticRes.models);
          setAvailableKnowledgeBases(staticRes.knowledgeBases);
          setAvailableSkills(staticRes.skills);
          setConfiguredConnectors(staticRes.configuredConnectors);

          toolsetsSearchRef.current = '';

          const [allToolsets, mcpServerEntries] = await Promise.all([
            loadToolsetsForAgentContext(agentPromise, editingAgentKey),
            loadMcpServersForAgentContext(agentPromise, editingAgentKey).catch((err) => {
              console.error('Failed to fetch MCP servers:', err);
              return [] as McpMyServerEntry[];
            }),
          ]);

          if (cancelled) return;

          setLoadedAgent(agentPromise ?? null);
          setToolsets(allToolsets);
          setMcpServers(mcpServerEntries);
          staticResourcesLoadedRef.current = true;
        } else {
          toolsetsSearchRef.current = '';

          const { agentDetails, allToolsets, mcpServers: mcpServerEntries } = await fetchAgentAndToolsets(
            editingAgentKey
          );

          if (cancelled) return;

          setLoadedAgent(agentDetails);
          setToolsets(allToolsets);
          setMcpServers(mcpServerEntries);
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
  }, [editingAgentKey]);

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
    toolsets,
    mcpServers,
    loading,
    loadedAgent,
    error,
    setError,
    refreshToolsets,
    refreshMcpServers,
    refreshAgent,
  };
}

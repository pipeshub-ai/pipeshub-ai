'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AgentsApi,
  buildToolsCatalogFromToolsets,
  mergeToolsFromAgentDetail,
} from '../../api';
import { ConnectorsApi } from '@/app/(main)/workspace/connectors/api';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import { ChatApi } from '@/chat/api';
import type { AvailableLlmModel } from '@/chat/types';
import type { AgentDetail } from '../../types';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { AgentToolsListRow, KnowledgeBaseForBuilder } from '../../types';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import type { MCPServerInstance, PaginationInfo } from '@/app/(main)/workspace/mcp-servers/types';

const TOOLSETS_PAGE = 20;
const MCP_SERVERS_PAGE = 20;

/** Models, KB, and connector lists — fetched once per hook mount (route remount resets the ref). */
async function fetchStaticBuilderResources() {
  const [models, kbResult, teamActive, personalActive, teamReg, personalReg] = await Promise.all([
    ChatApi.fetchAvailableLlms(),
    AgentsApi.getAllKnowledgeBasesForBuilder(),
    ConnectorsApi.getActiveConnectors('team', 1, 200).catch(() => ({ connectors: [] as Connector[] })),
    ConnectorsApi.getActiveConnectors('personal', 1, 200).catch(() => ({ connectors: [] as Connector[] })),
    ConnectorsApi.getRegistryConnectors('team', 1, 200).catch(() => ({ connectors: [] as Connector[] })),
    ConnectorsApi.getRegistryConnectors('personal', 1, 200).catch(() => ({ connectors: [] as Connector[] })),
  ]);

  const mergedConfigured = [
    ...(teamActive.connectors ?? []),
    ...(personalActive.connectors ?? []),
  ];
  const mergedRegistry = [...(teamReg.connectors ?? []), ...(personalReg.connectors ?? [])];

  return {
    models: models ?? [],
    knowledgeBases: kbResult.knowledgeBases ?? [],
    configuredConnectors: mergedConfigured,
    connectorRegistry: mergedRegistry,
  };
}

async function loadToolsetsForAgentContext(
  agentDetails: AgentDetail | null,
  editingAgentKey: string | null
): Promise<BuilderSidebarToolset[]> {
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

async function loadMcpServersForAgentContext(
  agentDetails: AgentDetail | null,
  editingAgentKey: string | null,
  params?: { page?: number; limit?: number; search?: string }
): Promise<{ mcpServers: MCPServerInstance[]; pagination: PaginationInfo }> {
  const isSvc = agentDetails?.isServiceAccount === true;
  const keyForMcp = agentDetails?._key || editingAgentKey || undefined;
  const baseParams = { includeRegistry: true, limit: MCP_SERVERS_PAGE, ...params };
  if (isSvc && keyForMcp) {
    return McpServersApi.getAgentMcpServers(keyForMcp, baseParams);
  }
  return McpServersApi.getMyMcpServers(baseParams);
}

async function fetchAgentAndToolsets(editingAgentKey: string | null) {
  const agentDetails = editingAgentKey
    ? await AgentsApi.getAgent(editingAgentKey).then((r) => r.agent).catch(() => null)
    : null;
  const allToolsets = await loadToolsetsForAgentContext(agentDetails, editingAgentKey);
  return { agentDetails, allToolsets };
}

async function fetchAgentAndMcpServers(editingAgentKey: string | null) {
  const agentDetails = editingAgentKey
    ? await AgentsApi.getAgent(editingAgentKey).then((r) => r.agent).catch(() => null)
    : null;
  const result = await loadMcpServersForAgentContext(agentDetails, editingAgentKey, { page: 1 });
  return { agentDetails, mcpServers: result.mcpServers, mcpPagination: result.pagination };
}

export function useAgentBuilderData(editingAgentKey: string | null) {
  const [availableTools, setAvailableTools] = useState<AgentToolsListRow[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableLlmModel[]>([]);
  const [availableKnowledgeBases, setAvailableKnowledgeBases] = useState<KnowledgeBaseForBuilder[]>(
    []
  );
  const [configuredConnectors, setConfiguredConnectors] = useState<Connector[]>([]);
  const [connectorRegistry, setConnectorRegistry] = useState<Connector[]>([]);
  const [toolsets, setToolsets] = useState<BuilderSidebarToolset[]>([]);
  const [toolsetsHasMore, setToolsetsHasMore] = useState(false);
  const [toolsetsLoadingMore, setToolsetsLoadingMore] = useState(false);
  const [mcpServers, setMcpServers] = useState<MCPServerInstance[]>([]);
  const [mcpServersHasMore, setMcpServersHasMore] = useState(false);
  const [mcpServersLoadingMore, setMcpServersLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadedAgent, setLoadedAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toolsetsPageRef = useRef(1);
  const toolsetsSearchRef = useRef('');
  const mcpServersPageRef = useRef(1);
  const mcpServersSearchRef = useRef('');
  const staticResourcesLoadedRef = useRef(false);

  const refreshToolsets = useCallback(
    async (agentKey?: string | null, isServiceAccount?: boolean, search?: string) => {
      toolsetsSearchRef.current = search ?? '';
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

  const loadMoreToolsets = useCallback(async () => {
    if (toolsetsLoadingMore || !toolsetsHasMore) return;
    const agentKey = loadedAgent?._key || editingAgentKey || undefined;
    const svc = Boolean(loadedAgent?.isServiceAccount) && Boolean(agentKey);
    setToolsetsLoadingMore(true);
    try {
      const next = toolsetsPageRef.current + 1;
      const res = svc
        ? await ToolsetsApi.getAgentToolsets(agentKey!, {
            page: next,
            limit: TOOLSETS_PAGE,
            search: toolsetsSearchRef.current || undefined,
            includeRegistry: true,
          })
        : await ToolsetsApi.getMyToolsets({
            page: next,
            limit: TOOLSETS_PAGE,
            search: toolsetsSearchRef.current || undefined,
            includeRegistry: true,
          });
      toolsetsPageRef.current = next;
      setToolsets((prev) => [...prev, ...res.toolsets]);
      setToolsetsHasMore(res.hasNext);
    } catch (err) {
      console.error('[AgentBuilder] Failed to load more toolsets:', err);
    } finally {
      setToolsetsLoadingMore(false);
    }
  }, [toolsetsHasMore, toolsetsLoadingMore, loadedAgent, editingAgentKey]);

  const refreshMcpServers = useCallback(
    async (agentKey?: string | null, isServiceAccount?: boolean, search?: string) => {
      mcpServersPageRef.current = 1;
      mcpServersSearchRef.current = search ?? '';
      const svc = Boolean(isServiceAccount) && Boolean(agentKey);
      const params = {
        includeRegistry: true,
        limit: MCP_SERVERS_PAGE,
        search: mcpServersSearchRef.current || undefined,
        page: 1,
      };
      const result = svc
        ? await McpServersApi.getAgentMcpServers(agentKey!, params)
        : await McpServersApi.getMyMcpServers(params);
      setMcpServers(result.mcpServers);
      setMcpServersHasMore(result.pagination.hasNext);
    },
    []
  );

  const loadMoreMcpServers = useCallback(async () => {
    if (mcpServersLoadingMore || !mcpServersHasMore) return;
    const agentKey = loadedAgent?._key || editingAgentKey || undefined;
    const svc = Boolean(loadedAgent?.isServiceAccount) && Boolean(agentKey);
    setMcpServersLoadingMore(true);
    try {
      const next = mcpServersPageRef.current + 1;
      const params = {
        page: next,
        limit: MCP_SERVERS_PAGE,
        search: mcpServersSearchRef.current || undefined,
        includeRegistry: true,
      };
      const result = svc
        ? await McpServersApi.getAgentMcpServers(agentKey!, params)
        : await McpServersApi.getMyMcpServers(params);
      mcpServersPageRef.current = next;
      setMcpServers((prev) => [...prev, ...result.mcpServers]);
      setMcpServersHasMore(result.pagination.hasNext);
    } catch (err) {
      console.error('[AgentBuilder] Failed to load more MCP servers:', err);
    } finally {
      setMcpServersLoadingMore(false);
    }
  }, [mcpServersHasMore, mcpServersLoadingMore, loadedAgent, editingAgentKey]);

  const refreshAgent = useCallback(
    async (agentKey: string, opts?: { knownAgent?: AgentDetail }) => {
      const agent = opts?.knownAgent ?? (await AgentsApi.getAgent(agentKey)).agent;
      if (agent) setLoadedAgent(agent);
      await Promise.all([
        refreshToolsets(agentKey, agent?.isServiceAccount, toolsetsSearchRef.current),
        refreshMcpServers(agentKey, agent?.isServiceAccount, mcpServersSearchRef.current),
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
          setConfiguredConnectors(staticRes.configuredConnectors);
          setConnectorRegistry(staticRes.connectorRegistry);

          toolsetsSearchRef.current = '';
          mcpServersPageRef.current = 1;
          mcpServersSearchRef.current = '';

          const [allToolsets, mcpResult] = await Promise.all([
            loadToolsetsForAgentContext(agentPromise, editingAgentKey),
            loadMcpServersForAgentContext(agentPromise, editingAgentKey, { page: 1 }),
          ]);

          if (cancelled) return;

          setLoadedAgent(agentPromise ?? null);
          setToolsets(allToolsets);
          setToolsetsHasMore(false);
          setMcpServers(mcpResult.mcpServers);
          setMcpServersHasMore(mcpResult.pagination.hasNext);
          staticResourcesLoadedRef.current = true;
        } else {
          toolsetsSearchRef.current = '';
          mcpServersPageRef.current = 1;
          mcpServersSearchRef.current = '';

          const [toolsetsResult, mcpResult] = await Promise.all([
            fetchAgentAndToolsets(editingAgentKey),
            fetchAgentAndMcpServers(editingAgentKey),
          ]);

          if (cancelled) return;

          setLoadedAgent(toolsetsResult.agentDetails);
          setToolsets(toolsetsResult.allToolsets);
          setToolsetsHasMore(false);
          setMcpServers(mcpResult.mcpServers);
          setMcpServersHasMore(mcpResult.mcpPagination.hasNext);
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
    activeAgentConnectors: configuredConnectors.filter((c) => c.isAgentActive),
    configuredConnectors,
    connectorRegistry,
    toolsets,
    loading,
    loadedAgent,
    error,
    setError,
    refreshToolsets,
    refreshAgent,
    loadMoreToolsets,
    toolsetsHasMore,
    toolsetsLoadingMore,
    mcpServers,
    mcpServersHasMore,
    mcpServersLoadingMore,
    refreshMcpServers,
    loadMoreMcpServers,
  };
}

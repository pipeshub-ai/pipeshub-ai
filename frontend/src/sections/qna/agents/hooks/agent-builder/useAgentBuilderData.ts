// src/sections/qna/agents/hooks/useFlowBuilderData.ts
import { useState, useCallback, useEffect, useRef } from 'react';
import type { Agent } from 'src/types/agent';
import type { Connector } from 'src/sections/accountdetails/connectors/types/types';
import { ConnectorApiService } from 'src/sections/accountdetails/connectors/services/api';
import ToolsetApiService from 'src/services/toolset-api';
import * as McpServerApi from 'src/services/mcp-server-api';
import type { UseAgentBuilderDataReturn, AgentBuilderError } from '../../types/agent';
import AgentApiService from '../../services/api';

// ─── Helper ───────────────────────────────────────────────────────────────────

/** Map a raw toolset item from the API into the sidebar-compatible format. */
const toSidebarToolset = (inst: any) => ({
  ...inst,
  name: inst.toolsetType || inst.instanceName || '',
  normalized_name: inst.toolsetType || '',
  displayName: inst.instanceName || inst.displayName || inst.toolsetType || '',
  description: inst.description || '',
  iconPath: inst.iconPath || '/assets/icons/toolsets/default.svg',
  category: inst.category || 'app',
  supportedAuthTypes: inst.supportedAuthTypes || [],
  toolCount: inst.toolCount || (inst.tools || []).length,
  tools: (inst.tools || []).map((t: any) => ({
    name: t.name || '',
    fullName: t.fullName || `${inst.toolsetType}.${t.name}`,
    description: t.description || '',
    appName: inst.toolsetType || '',
  })),
  isConfigured: inst.isConfigured,
  isAuthenticated: inst.isAuthenticated ?? false,
  isFromRegistry: !!inst.isFromRegistry,
  instanceId: inst.instanceId,
  instanceName: inst.instanceName,
  toolsetType: inst.toolsetType,
});

/** Map a raw MCP server item from the API into the sidebar-compatible format. */
const toSidebarMcpServer = (inst: any) => ({
  ...inst,
  name: inst.serverType || inst.instanceName || '',
  displayName: inst.instanceName || inst.displayName || inst.serverType || '',
  description: inst.description || '',
  iconPath: inst.iconPath || '/assets/icons/mcp-servers/server.svg',
  serverType: inst.serverType || 'custom',
  supportedAuthTypes: inst.supportedAuthTypes || [],
  toolCount: inst.toolCount || (inst.tools || []).length,
  tools: (inst.tools || []).map((t: any) => ({
    name: t.name || '',
    namespacedName: t.namespacedName || `mcp_${inst.serverType}_${t.name}`,
    description: t.description || '',
  })),
  isConfigured: inst.isConfigured,
  isAuthenticated: inst.isAuthenticated ?? false,
  isFromRegistry: !!inst.isFromRegistry,
  instanceId: inst.instanceId,
  instanceName: inst.instanceName,
});

/** Page size used for all toolset API calls. */
const TOOLSETS_PAGE_SIZE = 20;
const MCP_SERVERS_PAGE_SIZE = 20;

/**
 * Fetch MCP servers page (agent-scoped or user-scoped).
 */
const fetchMcpServersPage = async (
  agentKey: string | undefined,
  isServiceAccount: boolean,
  page: number,
  search: string
) => {
  const params = { includeRegistry: true, page, limit: MCP_SERVERS_PAGE_SIZE, search: search || undefined };
  if (isServiceAccount && agentKey) {
    return McpServerApi.getAgentMcpServers(agentKey, params);
  }
  return McpServerApi.getMyMcpServers(params);
};

/**
 * Fetch the right toolsets based on whether the agent is a service account.
 *
 * - Service account agent → GET /agents/{agentKey}?includeRegistry=true
 *   (agent-scoped credentials, not the user's)
 * - Regular agent / new agent → GET /my-toolsets?includeRegistry=true
 */
const fetchToolsetsPage = async (
  agentKey: string | undefined,
  isServiceAccount: boolean,
  page: number,
  search: string
) => {
  const params = { includeRegistry: true, page, limit: TOOLSETS_PAGE_SIZE, search: search || undefined };
  if (isServiceAccount && agentKey) {
    return AgentApiService.getAgentToolsets(agentKey, params);
  }
  return ToolsetApiService.getMyToolsets(params);
};

// ─── Hook ─────────────────────────────────────────────────────────────────────

export const useAgentBuilderData = (editingAgent?: Agent | { _key: string } | null): UseAgentBuilderDataReturn => {
  const [availableTools, setAvailableTools] = useState<any[]>([]);
  const [availableModels, setAvailableModels] = useState<any[]>([]);
  const [availableKnowledgeBases, setAvailableKnowledgeBases] = useState<any[]>([]);
  const [activeAgentConnectors, setActiveAgentConnectors] = useState<Connector[]>([]);
  const [configuredConnectors, setConfiguredConnectors] = useState<Connector[]>([]);
  const [connectorRegistry, setConnectorRegistry] = useState<any[]>([]);
  const [toolsets, setToolsets] = useState<any[]>([]);
  const [mcpServers, setMcpServers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadedAgent, setLoadedAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | AgentBuilderError | null>(null);

  // Pagination / search state for toolsets
  const [toolsetsHasMore, setToolsetsHasMore] = useState(false);
  const [toolsetsLoadingMore, setToolsetsLoadingMore] = useState(false);

  // Pagination / search state for MCP servers
  const [mcpServersHasMore, setMcpServersHasMore] = useState(false);
  const [mcpServersLoadingMore, setMcpServersLoadingMore] = useState(false);

  // Internal refs for pagination tracking (not needed in render so kept as refs)
  const toolsetsPageRef = useRef(1);
  const toolsetsSearchRef = useRef('');
  const toolsetsAgentKeyRef = useRef<string | undefined>(undefined);
  const toolsetsIsServiceAccountRef = useRef(false);

  const mcpServersPageRef = useRef(1);
  const mcpServersSearchRef = useRef('');
  const mcpServersAgentKeyRef = useRef<string | undefined>(undefined);
  const mcpServersIsServiceAccountRef = useRef(false);

  // Use refs to prevent duplicate API calls (React StrictMode, re-renders)
  const isLoadingRef = useRef(false);
  const hasLoadedRef = useRef(false);
  const agentKeyRef = useRef<string | undefined>(editingAgent?._key);

  // ── Main resource loader ──────────────────────────────────────────────────
  useEffect(() => {
    // Prevent duplicate calls
    if (isLoadingRef.current || hasLoadedRef.current) {
      return;
    }

    const loadResources = async () => {
      isLoadingRef.current = true;

      try {
        setLoading(true);
        setError(null);

        const agentKey = editingAgent?._key;

        // ── Step 1: Load all non-toolset resources AND the agent details in
        // parallel. We need the full agent object (specifically isServiceAccount)
        // before we can decide which toolsets endpoint to call.
        const [
          modelsResponse,
          kbResponse,
          activeAgentConnectorsResponse,
          configuredConnectorsResponse,
          connectorRegistryResponse,
          agentDetails,
        ] = await Promise.all([
          AgentApiService.getAvailableModels(),
          AgentApiService.getKnowledgeBases(),
          ConnectorApiService.getActiveAgentConnectorInstances(1, 100, ''),
          ConnectorApiService.getConfiguredConnectorInstances(undefined, 1, 100, ''),
          ConnectorApiService.getConnectorRegistry(undefined, 1, 100, ''),
          // Fetch agent details now so we know isServiceAccount for Step 2.
          // If there's no agentKey (new agent) this resolves to null immediately.
          agentKey
            ? AgentApiService.getAgent(agentKey).catch((err) => {
                console.error('Error loading agent details:', err);
                return null;
              })
            : Promise.resolve(null),
        ]);

        // Apply non-toolset state
        setAvailableTools([]); // Deprecated — tools come from toolsets now
        setAvailableModels(Array.isArray(modelsResponse) ? modelsResponse : []);
        setAvailableKnowledgeBases(kbResponse?.knowledgeBases || []);
        setActiveAgentConnectors(activeAgentConnectorsResponse?.connectors || []);
        setConfiguredConnectors(
          Array.isArray(configuredConnectorsResponse?.connectors)
            ? configuredConnectorsResponse.connectors
            : []
        );
        setConnectorRegistry(connectorRegistryResponse?.connectors || []);

        if (agentDetails) {
          setLoadedAgent(agentDetails);
        }

        // ── Step 2: Now that we know isServiceAccount from the actual agent
        // record, fetch the correct toolsets endpoint (page 1).
        const isServiceAccount = (agentDetails as Agent | null)?.isServiceAccount ?? false;

        // Store context for subsequent load-more / search calls
        toolsetsAgentKeyRef.current = agentKey;
        toolsetsIsServiceAccountRef.current = isServiceAccount;
        toolsetsPageRef.current = 1;
        toolsetsSearchRef.current = '';

        mcpServersAgentKeyRef.current = agentKey;
        mcpServersIsServiceAccountRef.current = isServiceAccount;
        mcpServersPageRef.current = 1;
        mcpServersSearchRef.current = '';

        const [toolsetsResponse, mcpServersResponse] = await Promise.all([
          fetchToolsetsPage(agentKey, isServiceAccount, 1, ''),
          fetchMcpServersPage(agentKey, isServiceAccount, 1, '').catch(() => null),
        ]);

        const items = (toolsetsResponse?.toolsets || []).map(toSidebarToolset);
        setToolsets(items);
        setToolsetsHasMore(toolsetsResponse?.pagination?.hasNext ?? false);

        if (mcpServersResponse) {
          setMcpServers((mcpServersResponse?.mcpServers || []).map(toSidebarMcpServer));
          setMcpServersHasMore(mcpServersResponse?.pagination?.hasNext ?? false);
        }

        hasLoadedRef.current = true;
      } catch (err) {
        setError('Failed to load resources');
        console.error('Error loading resources:', err);
      } finally {
        setLoading(false);
        isLoadingRef.current = false;
      }
    };

    loadResources();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  // Intentionally omitting all deps except editingAgent?._key: the effect must run
  // once on mount and whenever the agent key changes. Internal helpers (loadAgentDetails,
  // setters) are stable across renders; re-adding them would trigger infinite re-fetches.
  }, [editingAgent?._key]);

  // ── Agent-key change handler (switching between agents) ──────────────────
  // When the parent navigates to a different agent (agentKey URL param changes),
  // reload the agent details AND refresh toolsets for the new agent.
  useEffect(() => {
    if (editingAgent?._key === agentKeyRef.current) {
      return;
    }
    agentKeyRef.current = editingAgent?._key;

    if (!editingAgent?._key) {
      setLoadedAgent(null);
      // Reset toolsets to user-scoped ones for the "new agent" form
      toolsetsAgentKeyRef.current = undefined;
      toolsetsIsServiceAccountRef.current = false;
      toolsetsPageRef.current = 1;
      toolsetsSearchRef.current = '';

      mcpServersAgentKeyRef.current = undefined;
      mcpServersIsServiceAccountRef.current = false;
      mcpServersPageRef.current = 1;
      mcpServersSearchRef.current = '';

      ToolsetApiService.getMyToolsets({ includeRegistry: true, page: 1, limit: TOOLSETS_PAGE_SIZE })
        .then((res: any) => {
          setToolsets((res?.toolsets || []).map(toSidebarToolset));
          setToolsetsHasMore(res?.pagination?.hasNext ?? false);
        })
        .catch(() => {});

      McpServerApi.getMyMcpServers({ includeRegistry: true, page: 1, limit: MCP_SERVERS_PAGE_SIZE })
        .then((res: any) => {
          setMcpServers((res?.mcpServers || []).map(toSidebarMcpServer));
          setMcpServersHasMore(res?.pagination?.hasNext ?? false);
        })
        .catch(() => {});
      return;
    }

    if (!hasLoadedRef.current) {
      // Main loader hasn't finished yet — it will handle agent + toolsets
      return;
    }

    // Main loader already ran for a previous agent; reload for the new one.
    AgentApiService.getAgent(editingAgent._key)
      .then(async (agentDetails) => {
        setLoadedAgent(agentDetails);
        const isServiceAccount = agentDetails?.isServiceAccount ?? false;

        toolsetsAgentKeyRef.current = editingAgent._key;
        toolsetsIsServiceAccountRef.current = isServiceAccount;
        toolsetsPageRef.current = 1;
        toolsetsSearchRef.current = '';

        mcpServersAgentKeyRef.current = editingAgent._key;
        mcpServersIsServiceAccountRef.current = isServiceAccount;
        mcpServersPageRef.current = 1;
        mcpServersSearchRef.current = '';

        const [toolsetsResponse, mcpServersResponse] = await Promise.all([
          fetchToolsetsPage(editingAgent._key, isServiceAccount, 1, ''),
          fetchMcpServersPage(editingAgent._key, isServiceAccount, 1, '').catch(() => null),
        ]);
        setToolsets((toolsetsResponse?.toolsets || []).map(toSidebarToolset));
        setToolsetsHasMore(toolsetsResponse?.pagination?.hasNext ?? false);

        if (mcpServersResponse) {
          setMcpServers((mcpServersResponse?.mcpServers || []).map(toSidebarMcpServer));
          setMcpServersHasMore(mcpServersResponse?.pagination?.hasNext ?? false);
        }
      })
      .catch((err) => {
        console.error('Error reloading agent on key change:', err);
        setError('Failed to load agent details');
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  // Intentionally omitting loadAgentDetails and setter functions: all refs and setters
  // are stable; including them would cause re-runs on every render. The effect fires
  // only when the agent key changes, which is the correct semantics for "agent switch".
  }, [editingAgent?._key]);

  // ── refreshToolsets ───────────────────────────────────────────────────────
  /**
   * Manually refresh toolsets after an OAuth callback, credential change, or
   * search query update.  Always resets to page 1 and replaces the list.
   *
   * @param agentKey        Agent key for service-account agents.
   * @param isServiceAccount  Whether to use agent-scoped credentials.
   * @param search          Search string; pass '' to clear.
   */
  const refreshToolsets = useCallback(async (
    agentKey?: string,
    isServiceAccount?: boolean,
    search?: string,
  ) => {
    try {
      // Update stored context so load-more uses the right params
      if (agentKey !== undefined) toolsetsAgentKeyRef.current = agentKey;
      if (isServiceAccount !== undefined) toolsetsIsServiceAccountRef.current = isServiceAccount;

      const effectiveSearch = search !== undefined ? search : toolsetsSearchRef.current;
      toolsetsSearchRef.current = effectiveSearch;
      toolsetsPageRef.current = 1;

      const toolsetsResponse = await fetchToolsetsPage(
        toolsetsAgentKeyRef.current,
        toolsetsIsServiceAccountRef.current,
        1,
        effectiveSearch,
      );
      setToolsets((toolsetsResponse?.toolsets || []).map(toSidebarToolset));
      setToolsetsHasMore(toolsetsResponse?.pagination?.hasNext ?? false);
    } catch (err) {
      console.error('Error refreshing toolsets:', err);
    }
  }, []);

  // ── refreshAgent ─────────────────────────────────────────────────────────
  /**
   * Re-fetch the agent record from the API and immediately update loadedAgent
   * + reload toolsets using the freshly-retrieved isServiceAccount flag.
   *
   * Used after an in-place conversion to service-account mode so the builder
   * reflects the new state without a full page navigation / remount.
   */
  const refreshAgent = useCallback(async (agentKey: string) => {
    try {
      const agentDetails = await AgentApiService.getAgent(agentKey);
      setLoadedAgent(agentDetails);

      const isSvcAcct = agentDetails?.isServiceAccount ?? false;

      // Update all refs so subsequent load-more / search calls use the right params
      toolsetsAgentKeyRef.current = agentKey;
      toolsetsIsServiceAccountRef.current = isSvcAcct;
      toolsetsPageRef.current = 1;
      toolsetsSearchRef.current = '';

      mcpServersAgentKeyRef.current = agentKey;
      mcpServersIsServiceAccountRef.current = isSvcAcct;
      mcpServersPageRef.current = 1;
      mcpServersSearchRef.current = '';

      const [toolsetsResponse, mcpServersResponse] = await Promise.all([
        fetchToolsetsPage(agentKey, isSvcAcct, 1, ''),
        fetchMcpServersPage(agentKey, isSvcAcct, 1, '').catch(() => null),
      ]);
      setToolsets((toolsetsResponse?.toolsets || []).map(toSidebarToolset));
      setToolsetsHasMore(toolsetsResponse?.pagination?.hasNext ?? false);

      if (mcpServersResponse) {
        setMcpServers((mcpServersResponse?.mcpServers || []).map(toSidebarMcpServer));
        setMcpServersHasMore(mcpServersResponse?.pagination?.hasNext ?? false);
      }
    } catch (err) {
      console.error('Error refreshing agent:', err);
    }
  }, []);

  // ── refreshMcpServers ────────────────────────────────────────────────────
  const refreshMcpServers = useCallback(async (
    agentKey?: string,
    isServiceAccount?: boolean,
    search?: string,
  ) => {
    try {
      if (agentKey !== undefined) mcpServersAgentKeyRef.current = agentKey;
      if (isServiceAccount !== undefined) mcpServersIsServiceAccountRef.current = isServiceAccount;

      const effectiveSearch = search !== undefined ? search : mcpServersSearchRef.current;
      mcpServersSearchRef.current = effectiveSearch;
      mcpServersPageRef.current = 1;

      const res = await fetchMcpServersPage(
        mcpServersAgentKeyRef.current,
        mcpServersIsServiceAccountRef.current,
        1,
        effectiveSearch,
      );
      setMcpServers((res?.mcpServers || []).map(toSidebarMcpServer));
      setMcpServersHasMore(res?.pagination?.hasNext ?? false);
    } catch (err) {
      console.error('Error refreshing MCP servers:', err);
    }
  }, []);

  // ── loadMoreToolsets ──────────────────────────────────────────────────────
  /**
   * Load the next page of toolsets and append to the current list.
   * No-op when already loading or there are no more pages.
   */
  const loadMoreToolsets = useCallback(async () => {
    if (toolsetsLoadingMore || !toolsetsHasMore) return;

    setToolsetsLoadingMore(true);
    try {
      const nextPage = toolsetsPageRef.current + 1;
      const toolsetsResponse = await fetchToolsetsPage(
        toolsetsAgentKeyRef.current,
        toolsetsIsServiceAccountRef.current,
        nextPage,
        toolsetsSearchRef.current,
      );
      const newItems = (toolsetsResponse?.toolsets || []).map(toSidebarToolset);
      setToolsets((prev) => [...prev, ...newItems]);
      setToolsetsHasMore(toolsetsResponse?.pagination?.hasNext ?? false);
      toolsetsPageRef.current = nextPage;
    } catch (err) {
      console.error('Error loading more toolsets:', err);
    } finally {
      setToolsetsLoadingMore(false);
    }
  }, [toolsetsLoadingMore, toolsetsHasMore]);

  // ── loadMoreMcpServers ───────────────────────────────────────────────────
  const loadMoreMcpServers = useCallback(async () => {
    if (mcpServersLoadingMore || !mcpServersHasMore) return;

    setMcpServersLoadingMore(true);
    try {
      const nextPage = mcpServersPageRef.current + 1;
      const res = await fetchMcpServersPage(
        mcpServersAgentKeyRef.current,
        mcpServersIsServiceAccountRef.current,
        nextPage,
        mcpServersSearchRef.current,
      );
      const newItems = (res?.mcpServers || []).map(toSidebarMcpServer);
      setMcpServers((prev) => [...prev, ...newItems]);
      setMcpServersHasMore(res?.pagination?.hasNext ?? false);
      mcpServersPageRef.current = nextPage;
    } catch (err) {
      console.error('Error loading more MCP servers:', err);
    } finally {
      setMcpServersLoadingMore(false);
    }
  }, [mcpServersLoadingMore, mcpServersHasMore]);

  return {
    availableTools,
    availableModels,
    availableKnowledgeBases,
    activeAgentConnectors,
    configuredConnectors,
    connectorRegistry,
    toolsets,
    mcpServers,
    loading,
    loadedAgent,
    error,
    setError,
    refreshToolsets,
    refreshMcpServers,
    refreshAgent,
    toolsetsHasMore,
    toolsetsLoadingMore,
    loadMoreToolsets,
    mcpServersHasMore,
    mcpServersLoadingMore,
    loadMoreMcpServers,
  };
};

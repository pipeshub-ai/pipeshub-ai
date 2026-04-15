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
import { ToolsetsApi, type BuilderSidebarToolset } from '../../toolsets-api';
import type { AgentToolsListRow, KnowledgeBaseForBuilder } from '../../types';

const TOOLSETS_PAGE = 20;

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
  return isSvc && keyForToolsets
    ? ToolsetsApi.getAllAgentToolsets(keyForToolsets, {
        includeRegistry: true,
        limitPerPage: TOOLSETS_PAGE,
      })
    : ToolsetsApi.getAllMyToolsets({ includeRegistry: true, limitPerPage: TOOLSETS_PAGE });
}

async function fetchAgentAndToolsets(editingAgentKey: string | null) {
  const agentDetails = editingAgentKey
    ? await AgentsApi.getAgent(editingAgentKey).then((r) => r.agent).catch(() => null)
    : null;
  const allToolsets = await loadToolsetsForAgentContext(agentDetails, editingAgentKey);
  return { agentDetails, allToolsets };
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
  const [loading, setLoading] = useState(true);
  const [loadedAgent, setLoadedAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toolsetsPageRef = useRef(1);
  const toolsetsSearchRef = useRef('');
  const staticResourcesLoadedRef = useRef(false);

  const refreshToolsets = useCallback(
    async (agentKey?: string | null, isServiceAccount?: boolean, search?: string) => {
      toolsetsPageRef.current = 1;
      toolsetsSearchRef.current = search ?? '';
      const svc = Boolean(isServiceAccount) && Boolean(agentKey);
      const all = svc
        ? await ToolsetsApi.getAllAgentToolsets(agentKey!, {
            search: toolsetsSearchRef.current || undefined,
            includeRegistry: true,
            limitPerPage: TOOLSETS_PAGE,
          })
        : await ToolsetsApi.getAllMyToolsets({
            search: toolsetsSearchRef.current || undefined,
            includeRegistry: true,
            limitPerPage: TOOLSETS_PAGE,
          });
      setToolsets(all);
      setToolsetsHasMore(false);
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

  const refreshAgent = useCallback(
    async (agentKey: string, opts?: { knownAgent?: AgentDetail }) => {
      const agent = opts?.knownAgent ?? (await AgentsApi.getAgent(agentKey)).agent;
      if (agent) setLoadedAgent(agent);
      await refreshToolsets(agentKey, agent?.isServiceAccount, toolsetsSearchRef.current);
    },
    [refreshToolsets]
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

          toolsetsPageRef.current = 1;
          toolsetsSearchRef.current = '';

          const allToolsets = await loadToolsetsForAgentContext(agentPromise, editingAgentKey);

          if (cancelled) return;

          setLoadedAgent(agentPromise ?? null);
          setToolsets(allToolsets);
          setToolsetsHasMore(false);
          staticResourcesLoadedRef.current = true;
        } else {
          toolsetsPageRef.current = 1;
          toolsetsSearchRef.current = '';

          const { agentDetails, allToolsets } = await fetchAgentAndToolsets(editingAgentKey);

          if (cancelled) return;

          setLoadedAgent(agentDetails);
          setToolsets(allToolsets);
          setToolsetsHasMore(false);
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
  };
}

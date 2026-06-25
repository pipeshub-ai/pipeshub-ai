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
import type { AgentToolsListRow, KnowledgeBaseForBuilder } from '../../types';

const TOOLSETS_PAGE = 20;

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

/** Models, KB, and knowledge-hub nodes — fetched once per hook mount (route remount resets the ref). */
async function fetchStaticBuilderResources() {
  const [models, kbResult, appNodes] = await Promise.all([
    ChatApi.fetchAvailableLlms(),
    AgentsApi.getAllKnowledgeBasesForBuilder(),
    AgentsApi.getAllKnowledgeHubAppNodes().catch((err) => {
      console.error('Failed to fetch knowledge hub app nodes:', err);
      return [] as KnowledgeHubAppNode[];
    }),
  ]);

  const configuredConnectors = mapNodesToConnectors(appNodes);

  return {
    models: models ?? [],
    knowledgeBases: kbResult.knowledgeBases ?? [],
    configuredConnectors,
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
  const [toolsets, setToolsets] = useState<BuilderSidebarToolset[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadedAgent, setLoadedAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toolsetsSearchRef = useRef('');
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

          toolsetsSearchRef.current = '';

          const allToolsets = await loadToolsetsForAgentContext(agentPromise, editingAgentKey);

          if (cancelled) return;

          setLoadedAgent(agentPromise ?? null);
          setToolsets(allToolsets);
          staticResourcesLoadedRef.current = true;
        } else {
          toolsetsSearchRef.current = '';

          const { agentDetails, allToolsets } = await fetchAgentAndToolsets(editingAgentKey);

          if (cancelled) return;

          setLoadedAgent(agentDetails);
          setToolsets(allToolsets);
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
    activeAgentConnectors: configuredConnectors,
    configuredConnectors,
    toolsets,
    loading,
    loadedAgent,
    error,
    setError,
    refreshToolsets,
    refreshAgent,
  };
}

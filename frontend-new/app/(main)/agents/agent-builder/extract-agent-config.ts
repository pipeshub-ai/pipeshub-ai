import { i18n } from '@/lib/i18n';
import type { AgentDetail } from '../types';
import type { AgentFormPayload, KnowledgeReference, MCPServerReference, MCPServerToolRef, ToolsetReference } from './types';
import { mcpNamespacedName } from './mcp-naming';

interface ToolsetDataInternal {
  name: string;
  displayName: string;
  type: string;
  instanceId?: string;
  tools: { name: string; fullName: string; description?: string }[];
}

interface KnowledgeDataInternal {
  connectorId: string;
  filters: { recordGroups?: string[]; records?: string[]; [key: string]: unknown };
  category: 'knowledge' | 'action';
}

interface McpServerDataInternal {
  name: string;
  displayName: string;
  type: string;
  instanceId?: string;
  tools: MCPServerToolRef[];
}

export function extractAgentConfigFromFlow(
  agentName: string,
  nodes: { id: string; data: { type?: string; label?: string; config?: Record<string, unknown> } }[],
  edges: {
    source: string;
    target: string;
    targetHandle?: string | null;
    sourceHandle?: string | null;
  }[],
  currentAgent?: AgentDetail | null,
  shareWithOrg?: boolean,
  isServiceAccount?: boolean
): AgentFormPayload {
  const toolsetsInternal: ToolsetDataInternal[] = [];
  const knowledgeInternal: KnowledgeDataInternal[] = [];
  const mcpServersInternal: McpServerDataInternal[] = [];
  const models: { provider: string; modelName: string; isReasoning: boolean; modelKey: string }[] = [];

  const addToolsetWithTools = (
    toolsetName: string,
    displayName: string,
    toolsetType: string,
    toolsToAdd: { name: string; fullName: string; description?: string }[],
    instanceId?: string
  ) => {
    if (!toolsetName || toolsToAdd.length === 0) return;
    const normalizedName = toolsetName.toLowerCase().replace(/[^a-z0-9]/g, '');
    const existingIndex = instanceId
      ? toolsetsInternal.findIndex((ts) => ts.instanceId === instanceId)
      : toolsetsInternal.findIndex((ts) => ts.name === normalizedName);
    if (existingIndex >= 0) {
      toolsToAdd.forEach((tool) => {
        if (!toolsetsInternal[existingIndex].tools.find((t) => t.fullName === tool.fullName)) {
          toolsetsInternal[existingIndex].tools.push(tool);
        }
      });
    } else {
      toolsetsInternal.push({
        name: normalizedName,
        displayName: displayName || toolsetName,
        type: toolsetType || 'app',
        instanceId: instanceId || undefined,
        tools: toolsToAdd,
      });
    }
  };

  const addKnowledgeSource = (
    connectorId: string,
    filters: { recordGroups?: string[]; records?: string[]; [key: string]: unknown },
    category: 'knowledge' | 'action' = 'knowledge'
  ) => {
    if (!connectorId) return;
    const normalizedFilters = {
      recordGroups: Array.isArray(filters?.recordGroups) ? filters.recordGroups : [],
      records: Array.isArray(filters?.records) ? filters.records : [],
      ...filters,
    };
    const existingIndex = knowledgeInternal.findIndex((k) => k.connectorId === connectorId);
    if (existingIndex >= 0) {
      const existing = knowledgeInternal[existingIndex];
      const existingRecordGroups = Array.isArray(existing.filters?.recordGroups)
        ? existing.filters.recordGroups
        : [];
      const existingRecords = Array.isArray(existing.filters?.records) ? existing.filters.records : [];
      knowledgeInternal[existingIndex] = {
        connectorId,
        filters: {
          ...existing.filters,
          ...normalizedFilters,
          recordGroups: Array.from(
            new Set([...existingRecordGroups, ...normalizedFilters.recordGroups])
          ),
          records: Array.from(new Set([...existingRecords, ...normalizedFilters.records])),
        },
        category,
      };
    } else {
      knowledgeInternal.push({ connectorId, filters: normalizedFilters, category });
    }
  };

  // ── MCP Server extraction ──────────────────────────────────────────────────

  const connectedMcpNodeIds = new Set<string>();
  edges.forEach((edge) => {
    const sourceNode = nodes.find((n) => n.id === edge.source);
    const targetNode = nodes.find((n) => n.id === edge.target);
    const st = sourceNode?.data?.type ?? '';
    const tt = targetNode?.data?.type ?? '';
    if (st.startsWith('mcp-server-') && tt === 'agent-core' && edge.targetHandle === 'mcpServers') {
      connectedMcpNodeIds.add(sourceNode!.id);
    }
  });

  nodes.forEach((node) => {
    const nt = node.data?.type ?? '';
    if (!nt.startsWith('mcp-server-') || !connectedMcpNodeIds.has(node.id)) return;
    const mcpCfg = node.data.config ?? {};
    const mcpName =
      (mcpCfg.mcpServerName as string) ||
      (mcpCfg.instanceName as string) ||
      node.data.label ||
      '';
    const displayName = (mcpCfg.displayName as string) || mcpName;
    const serverType = (mcpCfg.serverType as string) || (mcpCfg.type as string) || '';
    const instanceId = mcpCfg.instanceId as string | undefined;
    const toolsFromConfig: MCPServerToolRef[] = [];
    const toolsArr = mcpCfg.tools as unknown[] | undefined;
    if (toolsArr && Array.isArray(toolsArr)) {
      toolsArr.forEach((tool: unknown) => {
        const t = tool as { name?: string; namespacedName?: string; description?: string; inputSchema?: Record<string, unknown> };
        const toolName = t.name || '';
        const namespacedName = mcpNamespacedName(serverType || undefined, mcpName, toolName, t.namespacedName);
        if (toolName) {
          toolsFromConfig.push({
            name: toolName,
            namespacedName,
            description: t.description,
            inputSchema: t.inputSchema,
          });
        }
      });
    }
    if (toolsFromConfig.length > 0) {
      mcpServersInternal.push({
        name: mcpName,
        displayName,
        type: serverType,
        instanceId,
        tools: toolsFromConfig,
      });
    }
  });

  // ── Toolset extraction ─────────────────────────────────────────────────────

  const connectedToolsetNodeIds = new Set<string>();
  edges.forEach((edge) => {
    const sourceNode = nodes.find((n) => n.id === edge.source);
    const targetNode = nodes.find((n) => n.id === edge.target);
    const st = sourceNode?.data?.type ?? '';
    const tt = targetNode?.data?.type ?? '';
    if (st.startsWith('toolset-') && tt === 'agent-core') connectedToolsetNodeIds.add(sourceNode!.id);
    if (tt.startsWith('toolset-') && st === 'agent-core') connectedToolsetNodeIds.add(targetNode!.id);
  });

  nodes.forEach((node) => {
    const nt = node.data?.type ?? '';
    if (!nt.startsWith('toolset-') || !connectedToolsetNodeIds.has(node.id)) return;
    const toolsetConfig = node.data.config ?? {};
    const toolsetName =
      (toolsetConfig.toolsetName as string) ||
      (toolsetConfig.name as string) ||
      node.data.label ||
      '';
    const displayName = (toolsetConfig.displayName as string) || node.data.label || toolsetName;
    const toolsetType = (toolsetConfig.type as string) || (toolsetConfig.category as string) || 'app';
    const instanceId = toolsetConfig.instanceId as string | undefined;
    const toolsFromConfig: { name: string; fullName: string; description?: string }[] = [];
    const toolsArr = toolsetConfig.tools as unknown[] | undefined;
    if (toolsArr && Array.isArray(toolsArr)) {
      toolsArr.forEach((tool: unknown) => {
        const t = tool as { name?: string; toolName?: string; fullName?: string; description?: string };
        const toolName = t.name || t.toolName || '';
        const normalizedToolsetName = toolsetName.toLowerCase().replace(/[^a-z0-9]/g, '');
        const toolFullName = t.fullName || `${normalizedToolsetName}.${toolName}`;
        if (toolFullName && toolName) {
          toolsFromConfig.push({ name: toolName, fullName: toolFullName, description: t.description || '' });
        }
      });
    }
    if (toolsFromConfig.length === 0 && Array.isArray(toolsetConfig.selectedTools)) {
      (toolsetConfig.selectedTools as string[]).forEach((selectedToolName) => {
        const tool = (toolsArr as { name?: string; toolName?: string; fullName?: string; description?: string }[] | undefined)?.find(
          (x) => x.name === selectedToolName || x.toolName === selectedToolName
        );
        const normalizedToolsetName = toolsetName.toLowerCase().replace(/[^a-z0-9]/g, '');
        const toolFullName = tool?.fullName || `${normalizedToolsetName}.${selectedToolName}`;
        if (!toolsFromConfig.find((x) => x.fullName === toolFullName)) {
          toolsFromConfig.push({
            name: tool?.name || tool?.toolName || selectedToolName,
            fullName: toolFullName,
            description: tool?.description || '',
          });
        }
      });
    }
    if (toolsFromConfig.length > 0) {
      addToolsetWithTools(toolsetName, displayName, toolsetType, toolsFromConfig, instanceId);
    }
  });

  const connectedKnowledgeNodeIds = new Set<string>();
  const connectedLLMNodeIds = new Set<string>();
  edges.forEach((edge) => {
    const sourceNode = nodes.find((n) => n.id === edge.source);
    const targetNode = nodes.find((n) => n.id === edge.target);
    const st = sourceNode?.data?.type ?? '';
    if (
      (st.startsWith('kb-') && st !== 'kb-group') ||
      (st.startsWith('app-') && st !== 'app-group')
    ) {
      if (targetNode?.data?.type === 'agent-core' && edge.targetHandle === 'knowledge' && sourceNode) {
        connectedKnowledgeNodeIds.add(sourceNode.id);
      }
    }
    if (st.startsWith('llm-')) {
      if (targetNode?.data?.type === 'agent-core' && edge.targetHandle === 'llms' && sourceNode) {
        connectedLLMNodeIds.add(sourceNode.id);
      }
    }
  });

  nodes.forEach((node) => {
    const nt = node.data?.type ?? '';
    const cfg = node.data.config ?? {};
    if (nt.startsWith('llm-')) {
      if (connectedLLMNodeIds.has(node.id)) {
        models.push({
          provider: (cfg.provider as string) || 'azureOpenAI',
          modelName: (cfg.modelName as string) || (cfg.model as string) || '',
          isReasoning: Boolean(cfg.isReasoning),
          modelKey: (cfg.modelKey as string) || '',
        });
      }
    } else if (nt.startsWith('kb-') && nt !== 'kb-group') {
      if (connectedKnowledgeNodeIds.has(node.id)) {
        const kbId = cfg.kbId as string | undefined;
        if (kbId) {
          const kbConnectorInstanceId =
            (cfg.connectorInstanceId as string) || (cfg.kbConnectorId as string);
          if (!kbConnectorInstanceId) {
            console.warn(`KB node ${kbId} missing connectorInstanceId. Using KB ID as fallback.`);
          }
          const filters = {
            recordGroups: [kbId],
            records: (cfg.selectedRecords as string[]) || (cfg.filters as { records?: string[] })?.records || [],
            ...((cfg.filters as object) || {}),
          };
          if (!filters.recordGroups.includes(kbId)) {
            filters.recordGroups = [kbId, ...filters.recordGroups];
          }
          addKnowledgeSource(kbConnectorInstanceId || kbId, filters, 'knowledge');
        }
      }
    } else if (nt.startsWith('app-') && nt !== 'app-group') {
      if (connectedKnowledgeNodeIds.has(node.id)) {
        const connectorInstanceId = (cfg.connectorInstanceId as string) || (cfg.id as string);
        if (connectorInstanceId) {
          const nodeFilters = (cfg.filters as Record<string, unknown>) || {};
          const filters = {
            ...nodeFilters,
            recordGroups: (cfg.selectedRecordGroups as string[]) || (nodeFilters.recordGroups as string[]) || [],
            records: (cfg.selectedRecords as string[]) || (nodeFilters.records as string[]) || [],
          };
          addKnowledgeSource(connectorInstanceId, filters, 'knowledge');
        }
      }
    }
  });

  const appKnowledgeGroupNode = nodes.find((n) => n.data?.type === 'app-group');
  if (appKnowledgeGroupNode) {
    const isAppGroupConnected = edges.some((edge) => {
      const sourceNode = nodes.find((n) => n.id === edge.source);
      const targetNode = nodes.find((n) => n.id === edge.target);
      return (
        sourceNode?.id === appKnowledgeGroupNode.id &&
        targetNode?.data?.type === 'agent-core' &&
        edge.targetHandle === 'knowledge'
      );
    });
    const cfg = appKnowledgeGroupNode.data.config ?? {};
    const selectedApps = cfg.selectedApps as string[] | undefined;
    if (isAppGroupConnected && selectedApps) {
      const appFilters = (cfg.appFilters as Record<string, { recordGroups?: string[]; records?: string[] }>) || {};
      selectedApps.forEach((connectorInstanceId) => {
        const connectorFilters = appFilters[connectorInstanceId] || { recordGroups: [], records: [] };
        addKnowledgeSource(connectorInstanceId, connectorFilters, 'knowledge');
      });
    }
  }

  const kbGroupNode = nodes.find((n) => n.data?.type === 'kb-group');
  if (kbGroupNode) {
    const isKBGroupConnected = edges.some((edge) => {
      const sourceNode = nodes.find((n) => n.id === edge.source);
      const targetNode = nodes.find((n) => n.id === edge.target);
      return (
        sourceNode?.id === kbGroupNode.id &&
        targetNode?.data?.type === 'agent-core' &&
        edge.targetHandle === 'knowledge'
      );
    });
    const cfg = kbGroupNode.data.config ?? {};
    const selectedKBs = cfg.selectedKBs as string[] | undefined;
    if (isKBGroupConnected && selectedKBs) {
      const kbConnectorIds = (cfg.kbConnectorIds as Record<string, string>) || {};
      const sharedConnectorId =
        (cfg.connectorInstanceId as string) || (cfg.kbConnectorId as string);
      selectedKBs.forEach((kbId) => {
        let connectorId = kbConnectorIds[kbId] || sharedConnectorId;
        if (!connectorId) {
          console.warn(`KB group: KB ${kbId} missing connectorId. Using KB ID as fallback.`);
          connectorId = kbId;
        }
        const kbSpecificFilters = (cfg.kbFilters as Record<string, { records?: string[] }>)?.[kbId] || {};
        const filters = {
          ...kbSpecificFilters,
          recordGroups: [kbId],
          records: kbSpecificFilters.records || [],
        };
        if (!filters.recordGroups.includes(kbId)) {
          filters.recordGroups = [kbId, ...filters.recordGroups];
        }
        addKnowledgeSource(connectorId, filters, 'knowledge');
      });
    }
  }

  const agentCoreNode = nodes.find((n) => n.data?.type === 'agent-core');
  const coreCfg = agentCoreNode?.data?.config ?? {};

  const toolsets: ToolsetReference[] = toolsetsInternal.map((ts) => ({
    id: ts.instanceId || ts.name,
    instanceId: ts.instanceId,
    name: ts.name,
    displayName: ts.displayName,
    type: ts.type,
    tools: ts.tools,
  }));

  const mcpServers: MCPServerReference[] = mcpServersInternal.map((ms) => ({
    id: ms.instanceId || ms.name,
    instanceId: ms.instanceId,
    name: ms.name,
    displayName: ms.displayName,
    type: ms.type,
    tools: ms.tools,
  }));

  const knowledge: KnowledgeReference[] = knowledgeInternal.map((k, index) => ({
    id: k.connectorId || `knowledge-${index}`,
    connectorId: k.connectorId,
    filters: k.filters,
  }));

  return {
    name: agentName,
    description:
      (coreCfg.description as string) ||
      currentAgent?.description ||
      i18n.t('agentBuilder.extractDefaultDescription'),
    startMessage:
      (coreCfg.startMessage as string) ||
      currentAgent?.startMessage ||
      i18n.t('agentBuilder.extractDefaultStartMessage'),
    systemPrompt:
      (coreCfg.systemPrompt as string) ||
      currentAgent?.systemPrompt ||
      i18n.t('agentBuilder.extractDefaultSystemPrompt'),
    instructions: agentCoreNode
      ? (coreCfg.instructions as string | undefined)
      : currentAgent?.instructions,
    toolsets,
    mcpServers: mcpServers.length > 0 ? mcpServers : undefined,
    knowledge,
    models,
    tags: currentAgent?.tags?.length ? currentAgent.tags : ['flow-based', 'visual-workflow'],
    shareWithOrg: shareWithOrg !== undefined ? shareWithOrg : (currentAgent?.shareWithOrg ?? false),
    isServiceAccount:
      currentAgent?.isServiceAccount === true
        ? true
        : isServiceAccount !== undefined
          ? isServiceAccount
          : false,
  };
}

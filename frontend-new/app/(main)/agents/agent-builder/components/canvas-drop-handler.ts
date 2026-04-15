import type { Edge, Node } from '@xyflow/react';
import type { TFunction } from 'i18next';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import type { FlowNodeData, NodeTemplate } from '../types';
import { normalizeDisplayName } from '../display-utils';
import { applyAutoConnectToEdges } from '../connection-rules';
import { resolvePremiumDropPosition } from '../drop-position';

type SetNodes = React.Dispatch<React.SetStateAction<Node<FlowNodeData>[]>>;
type SetEdges = React.Dispatch<React.SetStateAction<Edge[]>>;

function parseJson<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

type NormalizedSidebarTool = {
  name: string;
  fullName: string;
  description: string;
  toolsetName: string;
};

function mergeToolsByKey(a: NormalizedSidebarTool[], b: NormalizedSidebarTool[]): NormalizedSidebarTool[] {
  const map = new Map<string, NormalizedSidebarTool>();
  [...a, ...b].forEach((tool) => {
    const k = tool.fullName || tool.name;
    if (k) map.set(k, tool);
  });
  return Array.from(map.values());
}

/** Call from onDrop with event + deps */
export function handleFlowCanvasDrop(
  event: React.DragEvent,
  ctx: {
    /** Drop point in flow coordinates (from `screenToFlowPosition`). */
    flowPointer: { x: number; y: number };
    nodes: Node<FlowNodeData>[];
    setNodes: SetNodes;
    setEdges: SetEdges;
    nodeTemplates: NodeTemplate[];
    configuredConnectors: Connector[];
    activeAgentConnectors: Connector[];
    readOnly: boolean;
    t: TFunction;
    onError?: (message: string) => void;
  }
): void {
  if (ctx.readOnly) return;
  event.preventDefault();

  const type = event.dataTransfer.getData('application/reactflow');
  const connectorId = event.dataTransfer.getData('connectorId');
  const connectorType = event.dataTransfer.getData('connectorType');
  const connectorScope = event.dataTransfer.getData('scope');
  const toolAppName = event.dataTransfer.getData('toolAppName');
  const connectorName = event.dataTransfer.getData('connectorName');
  const connectorIconPath = event.dataTransfer.getData('connectorIconPath');
  const allToolsStr = event.dataTransfer.getData('allTools');
  const toolCount = event.dataTransfer.getData('toolCount');

  const toolsetInstanceId = event.dataTransfer.getData('instanceId');
  const toolsetInstanceName = event.dataTransfer.getData('instanceName');
  const toolsetName = event.dataTransfer.getData('toolsetName');
  let toolsetDisplayName = event.dataTransfer.getData('displayName');
  if (toolsetDisplayName?.includes(' - ')) {
    toolsetDisplayName = toolsetDisplayName.split(' - ')[0].trim();
  }
  const toolsetIconPath = event.dataTransfer.getData('iconPath');
  const toolsetCategory = event.dataTransfer.getData('category');
  const toolsetType = event.dataTransfer.getData('type');
  const toolFullName = event.dataTransfer.getData('fullName');
  const toolName = event.dataTransfer.getData('toolName');
  const toolDescription = event.dataTransfer.getData('description');
  const isToolsetConfigured = event.dataTransfer.getData('isConfigured') === 'true';
  const isToolsetAuthenticated = event.dataTransfer.getData('isAuthenticated') === 'true';

  const {
    flowPointer,
    nodes,
    setNodes,
    setEdges,
    nodeTemplates,
    configuredConnectors,
    activeAgentConnectors,
    onError,
    t,
  } = ctx;

  const place = (plannedType: string) => resolvePremiumDropPosition(flowPointer, plannedType, nodes);

  const appendNodeWithAutoConnect = (newNode: Node<FlowNodeData>) => {
    setNodes((nds) => [...nds, newNode]);
    setEdges((eds) => applyAutoConnectToEdges(newNode, [...nodes, newNode], eds));
  };

  const normalizeTool = (toolset: string, row: Record<string, unknown>) => ({
    name: (row.toolName as string) || (row.name as string) || '',
    fullName:
      (row.fullName as string) ||
      `${toolset}.${(row.toolName as string) || (row.name as string) || ''}`,
    description: (row.description as string) || '',
    toolsetName: toolset,
  });

  if (type.startsWith('toolset-') || toolsetType === 'toolset') {
    if (!isToolsetConfigured || !isToolsetAuthenticated) {
      onError?.(
        t('agentBuilder.toolsetNotReadyNotify', {
          name: toolsetDisplayName || toolsetName,
          reason: !isToolsetConfigured
            ? t('agentBuilder.notConfiguredReason')
            : t('agentBuilder.notAuthenticatedReason'),
        })
      );
      return;
    }
    if (!allToolsStr) {
      onError?.(t('agentBuilder.dropNoToolsInToolset'));
      return;
    }
    const allTools = parseJson<Record<string, unknown>[]>(allToolsStr, []);
    const selectedToolsStr = event.dataTransfer.getData('selectedTools');
    const selectedTools = selectedToolsStr
      ? parseJson<string[]>(selectedToolsStr, [])
      : allTools.map((t) => (t.toolName as string) || (t.name as string) || '');
    const normalizedTools = allTools.map((t) => normalizeTool(toolsetName, t));

    const existingToolsetNode = nodes.find(
      (n) => n.data?.type?.startsWith('toolset-') && n.data.config?.toolsetName === toolsetName
    );

    if (existingToolsetNode) {
      const existingTools = (existingToolsetNode.data.config?.tools as typeof normalizedTools) || [];
      const existingAvail =
        (existingToolsetNode.data.config?.availableTools as typeof normalizedTools) || existingTools;
      const mergedAvail = mergeToolsByKey(existingAvail, normalizedTools);
      const existingNames = new Set(existingTools.map((t) => t.fullName || t.name));
      const newTools = normalizedTools.filter(
        (t) => !existingNames.has(t.fullName) && !existingNames.has(t.name)
      );
      if (newTools.length > 0) {
        ctx.setNodes((nds) =>
          nds.map((node) =>
            node.id === existingToolsetNode!.id
              ? {
                  ...node,
                  data: {
                    ...node.data,
                    config: {
                      ...node.data.config,
                      tools: [...existingTools, ...newTools],
                      selectedTools: [
                        ...((node.data.config?.selectedTools as string[]) || []),
                        ...newTools.map((t) => t.name),
                      ],
                      availableTools: mergedAvail,
                    },
                  },
                }
              : node
          )
        );
      } else {
        ctx.setNodes((nds) =>
          nds.map((node) =>
            node.id === existingToolsetNode!.id
              ? {
                  ...node,
                  data: {
                    ...node.data,
                    config: {
                      ...node.data.config,
                      availableTools: mergedAvail,
                    },
                  },
                }
              : node
          )
        );
      }
      return;
    }

    const resolvedSelectedNames =
      selectedTools.length > 0
        ? selectedTools.map((sel) => {
            const tool = normalizedTools.find((nt) => nt.name === sel);
            return tool ? tool.name : sel;
          })
        : normalizedTools.map((t) => t.name);
    const toolsOnNode = normalizedTools.filter((t) => resolvedSelectedNames.includes(t.name));
    const toolsForNewNode = toolsOnNode.length > 0 ? toolsOnNode : normalizedTools;

    const tsType = `toolset-${toolsetName}`;
    const tsNodeId = `${tsType}-${Date.now()}`;
    const newNode: Node<FlowNodeData> = {
      id: tsNodeId,
      type: 'flowNode',
      position: place(tsType),
      data: {
        id: tsNodeId,
        type: tsType,
        label: normalizeDisplayName(toolsetDisplayName || toolsetName),
        description: t('agentBuilder.toolsetNodeDescriptionTools', {
          name: toolsetDisplayName || toolsetName,
          count: (() => {
            const n = parseInt(String(toolCount || '').trim(), 10);
            return Number.isFinite(n) && n >= 0 ? n : normalizedTools.length;
          })(),
        }),
        icon: toolsetIconPath || 'extension',
        category: 'toolset',
        config: {
          instanceId: toolsetInstanceId || undefined,
          instanceName: toolsetInstanceName || undefined,
          toolsetName,
          displayName: toolsetDisplayName || toolsetName,
          iconPath: toolsetIconPath,
          category: toolsetCategory || 'app',
          tools: toolsForNewNode,
          availableTools: normalizedTools,
          selectedTools: toolsForNewNode.map((t) => t.name),
          isConfigured: isToolsetConfigured,
          isAuthenticated: isToolsetAuthenticated,
        },
        inputs: [],
        outputs: ['output'],
        isConfigured: true,
      },
    };
    appendNodeWithAutoConnect(newNode);
    return;
  }

  const constructedToolFullName =
    toolFullName || (toolsetName && toolName ? `${toolsetName}.${toolName}` : '');
  const isToolsetTool =
    toolsetType === 'tool' && toolsetName && (constructedToolFullName || toolName);

  if (isToolsetTool) {
    if (!isToolsetConfigured || !isToolsetAuthenticated) {
      onError?.(t('agentBuilder.dropToolsetNotReady', { name: toolsetDisplayName || toolsetName }));
      return;
    }
    const finalToolFullName = constructedToolFullName || `${toolsetName}.${toolName}`;
    let allAvailableTools: Record<string, unknown>[] = [];
    if (allToolsStr) {
      allAvailableTools = parseJson(allToolsStr, []);
    }
    if (allAvailableTools.length === 0) {
      allAvailableTools = [
        {
          toolName: toolName || '',
          fullName: finalToolFullName,
          toolsetName,
          description: toolDescription || '',
        },
      ];
    }
    const normalizedAvailable = allAvailableTools.map((t) => normalizeTool(toolsetName, t));
    const droppedTool =
      normalizedAvailable.find(
        (t) => t.fullName === finalToolFullName || t.name === toolName
      ) ||
      normalizeTool(toolsetName, {
        toolName: toolName || '',
        fullName: finalToolFullName,
        description: toolDescription || '',
      });

    const existingToolsetNode = nodes.find(
      (n) => n.data?.type?.startsWith('toolset-') && n.data.config?.toolsetName === toolsetName
    );

    if (existingToolsetNode) {
      const existingTools = (existingToolsetNode.data.config?.tools as typeof normalizedAvailable) || [];
      const exists = existingTools.some(
        (t) => t.fullName === droppedTool.fullName || t.name === droppedTool.name
      );
      if (!exists) {
        setNodes((nds) =>
          nds.map((node) =>
            node.id === existingToolsetNode!.id
              ? {
                  ...node,
                  data: {
                    ...node.data,
                    config: {
                      ...node.data.config,
                      tools: [...existingTools, droppedTool],
                      selectedTools: [
                        ...((node.data.config?.selectedTools as string[]) || []),
                        droppedTool.name,
                      ],
                      availableTools: mergeToolsByKey(
                        (node.data.config?.availableTools as typeof normalizedAvailable) || [],
                        normalizedAvailable.length > 0 ? normalizedAvailable : []
                      ),
                    },
                  },
                }
              : node
          )
        );
      }
      return;
    }

    const tsId = `toolset-${toolsetName}-${Date.now()}`;
    const tsTypeSingle = `toolset-${toolsetName}`;
    appendNodeWithAutoConnect({
      id: tsId,
      type: 'flowNode',
      position: place(tsTypeSingle),
      data: {
        id: tsId,
        type: tsTypeSingle,
        label: normalizeDisplayName(toolsetDisplayName || toolsetName),
        description: toolsetDisplayName || toolsetName,
        icon: toolsetIconPath || 'extension',
        category: 'toolset',
        config: {
          instanceId: toolsetInstanceId || undefined,
          instanceName: toolsetInstanceName || undefined,
          toolsetName,
          displayName: toolsetDisplayName || toolsetName,
          iconPath: toolsetIconPath,
          category: toolsetCategory || 'app',
          tools: [droppedTool],
          availableTools: normalizedAvailable.length > 0 ? normalizedAvailable : [droppedTool],
          selectedTools: [droppedTool.name],
          isConfigured: isToolsetConfigured,
          isAuthenticated: isToolsetAuthenticated,
        },
        inputs: [],
        outputs: ['output'],
        isConfigured: true,
      },
    });
    return;
  }

  const template = nodeTemplates.find((t) => t.type === type);
  if (!template) return;

  const isConnectorConfigured = event.dataTransfer.getData('isConfigured') === 'true';
  const isConnectorAgentActive = event.dataTransfer.getData('isAgentActive') === 'true';

  const findConnector = (): { id: string; name: string } | null => {
    if (connectorId) {
      const connector =
        configuredConnectors.find((c) => c._key === connectorId) ||
        activeAgentConnectors.find((c) => c._key === connectorId);
      return {
        id: connectorId,
        name: connector?.name || connectorName || connectorType || toolAppName || 'Connector',
      };
    }
    const appName = (template.defaultConfig?.appName as string) || toolAppName || connectorType;
    if (appName) {
      const connector =
        configuredConnectors.find(
          (c) => c.name?.toUpperCase() === appName.toUpperCase() || c.type?.toUpperCase() === appName.toUpperCase()
        ) ||
        activeAgentConnectors.find(
          (c) => c.name?.toUpperCase() === appName.toUpperCase() || c.type?.toUpperCase() === appName.toUpperCase()
        );
      if (connector?._key) {
        return { id: connector._key, name: connector.name || appName };
      }
    }
    return null;
  };

  if (template.type.startsWith('tool-') && !template.type.startsWith('tool-group-')) {
    const appName = (template.defaultConfig?.appName as string) || toolAppName;
    if (!isConnectorConfigured || !isConnectorAgentActive) {
      const connector = findConnector();
      onError?.(
        connector
          ? t('agentBuilder.dropConnectorMustEnableNamed', { name: connector.name })
          : t('agentBuilder.dropConnectorMustEnableApp', { app: appName || '' })
      );
      return;
    }
  }

  if (template.type.startsWith('tool-group-')) {
    if (!isConnectorAgentActive) {
      onError?.(t('agentBuilder.dropEnableConnectorToolGroup'));
      return;
    }
    const connectorAppType = connectorType || (template.defaultConfig?.appName as string);
    if (connectorAppType) {
      const dup = nodes.some(
        (n) =>
          n.data?.type?.startsWith('tool-group-') &&
          (n.data.config?.connectorType === connectorAppType ||
            n.data.config?.appName === connectorAppType)
      );
      if (dup) {
        onError?.(t('agentBuilder.dropOnlyOneToolGroup', { type: connectorAppType }));
        return;
      }
    }
  }

  if (template.type.startsWith('tool-group-') && allToolsStr && connectorId) {
    const allTools = parseJson<Record<string, unknown>[]>(allToolsStr, []);
    const tgId = `${type}-${Date.now()}`;
    appendNodeWithAutoConnect({
      id: tgId,
      type: 'flowNode',
      position: place(template.type),
      data: {
        id: tgId,
        type: template.type,
        label: normalizeDisplayName(connectorName || template.label),
        description: t('agentBuilder.connectorToolGroupTools', {
          type: connectorType,
          count: (() => {
            const n = parseInt(String(toolCount || '').trim(), 10);
            return Number.isFinite(n) && n >= 0 ? n : allTools.length;
          })(),
        }),
        icon: template.icon,
        config: {
          ...template.defaultConfig,
          connectorInstanceId: connectorId,
          connectorType,
          connectorName,
          iconPath: connectorIconPath || template.defaultConfig?.iconPath,
          tools: allTools,
          selectedTools: allTools.map((t) => t.toolId as string),
          appName: connectorType,
          appDisplayName: connectorName || connectorType,
          scope: connectorScope,
        },
        inputs: template.inputs || ['input'],
        outputs: template.outputs || ['output'],
        isConfigured: true,
      },
    });
    return;
  }

  if (template.type === 'app-group') {
    const selectedAppsStr = event.dataTransfer.getData('selectedApps');
    const appDetailsStr = event.dataTransfer.getData('appDetails');
    let selectedApps: string[] = (template.defaultConfig?.selectedApps as string[]) || [];
    let apps = (template.defaultConfig?.apps as unknown[]) || [];
    if (selectedAppsStr) selectedApps = parseJson(selectedAppsStr, selectedApps);
    if (appDetailsStr) apps = parseJson(appDetailsStr, apps);
    const appFilters: Record<string, { recordGroups: string[]; records: string[] }> = {};
    selectedApps.forEach((id) => {
      appFilters[id] = { recordGroups: [], records: [] };
    });
    const agId = `${type}-${Date.now()}`;
    appendNodeWithAutoConnect({
      id: agId,
      type: 'flowNode',
      position: place(template.type),
      data: {
        id: agId,
        type: template.type,
        label: normalizeDisplayName(template.label),
        description: template.description,
        icon: template.icon,
        config: { ...template.defaultConfig, apps, selectedApps, appFilters },
        inputs: template.inputs,
        outputs: template.outputs,
        isConfigured: true,
      },
    });
    return;
  }

  if (template.type === 'kb-group') {
    const selectedKBsStr = event.dataTransfer.getData('selectedKBs');
    const kbConnectorIdsStr = event.dataTransfer.getData('kbConnectorIds');
    let selectedKBs: string[] = (template.defaultConfig?.selectedKBs as string[]) || [];
    let kbConnectorIds: Record<string, string> =
      (template.defaultConfig?.kbConnectorIds as Record<string, string>) || {};
    if (selectedKBsStr) selectedKBs = parseJson(selectedKBsStr, selectedKBs);
    if (kbConnectorIdsStr) kbConnectorIds = parseJson(kbConnectorIdsStr, kbConnectorIds);
    const kbgId = `${type}-${Date.now()}`;
    appendNodeWithAutoConnect({
      id: kbgId,
      type: 'flowNode',
      position: place(template.type),
      data: {
        id: kbgId,
        type: template.type,
        label: normalizeDisplayName(template.label),
        description: template.description,
        icon: template.icon,
        config: { ...template.defaultConfig, selectedKBs, kbConnectorIds },
        inputs: template.inputs,
        outputs: template.outputs,
        isConfigured: true,
      },
    });
    return;
  }

  const fallbackId = `${type}-${Date.now()}`;
  appendNodeWithAutoConnect({
    id: fallbackId,
    type: 'flowNode',
    position: place(template.type),
    data: {
      id: fallbackId,
      type: template.type,
      label: normalizeDisplayName(template.label),
      description: template.description,
      icon: template.icon,
      config: {
        ...template.defaultConfig,
        ...(connectorId ? { connectorInstanceId: connectorId } : {}),
        ...(connectorType ? { connectorType } : {}),
        ...(template.type.startsWith('tool-') && !template.type.startsWith('tool-group-')
          ? {
              connectorInstanceId: connectorId || template.defaultConfig?.connectorInstanceId,
              connectorType: connectorType || template.defaultConfig?.appName,
              connectorName: connectorName || connectorType || template.defaultConfig?.appName,
              iconPath: connectorIconPath || template.defaultConfig?.iconPath,
              scope: connectorScope || template.defaultConfig?.scope,
            }
          : {}),
      },
      inputs: template.inputs,
      outputs: template.outputs,
      isConfigured:
        template.type.startsWith('app-') || template.type.startsWith('tool-group-') ? true : false,
    },
  });
}

'use client';

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Node, Edge } from '@xyflow/react';
import {
  truncateText,
  normalizeDisplayName,
  formattedProvider,
} from '../display-utils';
import { FLOW_EDGE } from '../flow-theme';
import type { AgentConfiguredModel, AgentToolset, AgentToolDefinition } from '../../types';
import type { AgentReconstructionSource, FlowNodeData } from '../types';

/** Reconstructed model config — unified shape for both object and legacy string entries. */
interface ModelConfigObject {
  modelKey: string;
  provider: string;
  modelName: string;
  isReasoning?: boolean;
  isMultimodal?: boolean;
  isDefault?: boolean;
  modelType?: string;
}

/** Raw knowledge item shape from the agent API. Fields vary across API versions. */
interface KnowledgeItem {
  connectorId?: string;
  type?: string;
  name?: string;
  displayName?: string;
  _key?: string;
  filters?: Record<string, unknown> | string;
  filtersParsed?: Record<string, unknown>;
  recordGroups?: string[];
  records?: string[];
}

export function useAgentBuilderReconstruction(): {
  reconstructFlowFromAgent: (
    agent: AgentReconstructionSource,
    models: unknown[],
    tools: unknown[],
    knowledgeBases: { id: string; name: string; connectorId?: string }[]
  ) => { nodes: Node<FlowNodeData>[]; edges: Edge[] };
} {
  const { t } = useTranslation();
  const reconstructFlowFromAgent = useCallback(
    (agent: AgentReconstructionSource, models: unknown[], tools: unknown[], knowledgeBases: { id: string; name: string; connectorId?: string }[]) => {
      // ── Fast path: if the agent was saved with its visual layout, restore it
      // directly so the user's custom positions survive edits.
      if (
        agent.flow?.nodes &&
        Array.isArray(agent.flow.nodes) &&
        agent.flow.nodes.length > 0
      ) {
        const savedNodes = agent.flow.nodes as Node<FlowNodeData>[];
        const savedEdges = (agent.flow.edges || []) as Edge[];
        // Validate that the saved nodes look like proper ReactFlow flow nodes
        // (they must have an id, the 'flowNode' type marker, and a data payload).
        const valid = savedNodes.every(
          (n) => n.id && n.type === 'flowNode' && n.data && n.data.type
        );
        if (valid) {
          return { nodes: savedNodes, edges: savedEdges };
        }
      }

      const nodes: Node<FlowNodeData>[] = [];
      const edges: Edge[] = [];
      let nodeCounter = 1;

      const modelCatalog = models as Array<{
        modelKey?: string;
        modelName?: string;
        provider?: string;
        modelFriendlyName?: string;
        modelType?: string;
        isMultimodal?: boolean;
        isDefault?: boolean;
        isReasoning?: boolean;
      }>;
      const toolCatalog = tools as Array<{
        full_name?: string;
        tool_name?: string;
        tool_id?: string;
        app_name?: string;
        description?: string;
      }>;

      // Enhanced Tree Layout - Optimized for visual clarity and scalability
      const layout = {
        // Six distinct horizontal layers with proper separation
        layers: {
          input: { x: 100, baseY: 400 },         // Layer 1: User Input (left)
          knowledge: { x: 500, baseY: 400 },     // Layer 2: Knowledge & Context
          llm: { x: 950, baseY: 400 },           // Layer 3: LLMs
          tools: { x: 1400, baseY: 400 },        // Layer 4: Toolsets (separate layer)
          agent: { x: 1850, baseY: 400 },        // Layer 5: Agent Core
          output: { x: 2300, baseY: 400 },       // Layer 6: Response Output
        },

        // Dynamic spacing based on node density
        spacing: {
          // Vertical spacing between nodes in same layer
          getVerticalSpacing: (nodeCount: number) => {
            if (nodeCount === 1) return 0;
            if (nodeCount <= 2) return 280;
            if (nodeCount <= 3) return 240;
            if (nodeCount <= 4) return 210;
            if (nodeCount <= 6) return 180;
            if (nodeCount <= 8) return 160;
            if (nodeCount <= 10) return 145;
            return 130; // For 10+ nodes
          },

          // Minimum gaps between nodes
          minVerticalGap: 130,
          minHorizontalGap: 350,
        },
      };

      // Calculate node counts for intelligent positioning
      let toolsetsCount = 0;
      if (agent.toolsets && agent.toolsets.length > 0) {
        toolsetsCount = agent.toolsets.length;
      } else if (agent.tools && agent.tools.length > 0) {
        const uniqueApps = new Set<string>();
        agent.tools.forEach((toolName: string) => {
          const appName = toolName.split('.')[0];
          uniqueApps.add(appName);
        });
        toolsetsCount = uniqueApps.size;
      }

      const counts = {
        llm: agent.models?.length || (modelCatalog.length > 0 ? 1 : 0),
        tools: 0,
        toolsets: toolsetsCount,
        knowledge: agent.knowledge?.length || 0,
      };


      // Smart positioning system with visual balance
      const calculateOptimalPosition = (
        layerKey: keyof typeof layout.layers,
        index: number,
        totalInLayer: number
      ) => {
        const layer = layout.layers[layerKey];
        const spacing = layout.spacing.getVerticalSpacing(totalInLayer);

        // Calculate vertical centering with intelligent distribution
        let y: number;
        if (totalInLayer === 1) {
          y = layer.baseY;
        } else {
          // Create vertical distribution centered around baseY
          const totalHeight = (totalInLayer - 1) * spacing;
          const startY = layer.baseY - totalHeight / 2;
          y = startY + index * spacing;
        }

        return {
          x: layer.x,
          y,
        };
      };

      // Enhanced agent positioning with visual balance consideration
      const calculateAgentPosition = () => {
        const connectedPositions: { y: number; weight: number }[] = [];

        // Collect all processing node Y positions with weights
        const addPositions = (
          layerKey: keyof typeof layout.layers,
          count: number,
          weight: number
        ) => {
          for (let i = 0; i < count; i += 1) {
            const pos = calculateOptimalPosition(layerKey, i, count);
            connectedPositions.push({ y: pos.y, weight });
          }
        };

        // Add positions with different weights for visual balance
        if (counts.knowledge > 0) addPositions('knowledge', counts.knowledge, 1.0);
        if (counts.llm > 0) addPositions('llm', counts.llm, 1.5);
        if (counts.toolsets > 0) addPositions('tools', counts.toolsets, 1.0);

        if (connectedPositions.length === 0) {
          return { x: layout.layers.agent.x, y: layout.layers.agent.baseY };
        }

        // Weighted center calculation for optimal visual balance
        const totalWeight = connectedPositions.reduce((sum, pos) => sum + pos.weight, 0);
        const weightedY =
          connectedPositions.reduce((sum, pos) => sum + pos.y * pos.weight, 0) / totalWeight;

        // Apply constraints for better visual bounds
        const minY = Math.min(...connectedPositions.map(p => p.y));
        const maxY = Math.max(...connectedPositions.map(p => p.y));
        const constrainedY = Math.max(minY - 50, Math.min(weightedY, maxY + 50));

        return {
          x: layout.layers.agent.x,
          y: constrainedY,
        };
      };

      // 1. Create Chat Input node
      const chatInputNode: Node<FlowNodeData> = {
        id: 'chat-input-1',
        type: 'flowNode',
        position: calculateOptimalPosition('input', 0, 1),
        data: {
          id: 'chat-input-1',
          type: 'user-input',
          label: t('agentBuilder.nodeLabelChatInput'),
          description: t('agentBuilder.nodeDescUserInputFlow'),
          icon: 'chat',
          config: { placeholder: t('agentBuilder.chatInputPlaceholder') },
          inputs: [],
          outputs: ['message'],
          isConfigured: true,
        },
      };
      nodes.push(chatInputNode);

      // 2. Create Knowledge nodes (Knowledge Bases + App Knowledge)
      const knowledgeNodes: Node<FlowNodeData>[] = [];

      if (agent.knowledge && agent.knowledge.length > 0) {
        agent.knowledge.forEach((raw: unknown, index: number) => {
          const knowledgeItem = raw as KnowledgeItem;
          const connectorId = knowledgeItem.connectorId || '';
          const filters = knowledgeItem.filtersParsed || knowledgeItem.filters || {};

          let filtersParsed: Record<string, unknown>;
          if (typeof filters === 'string') {
            try {
              filtersParsed = JSON.parse(filters) as Record<string, unknown>;
            } catch {
              filtersParsed = {};
            }
          } else {
            filtersParsed = filters as Record<string, unknown>;
          }

          const recordGroups = (filtersParsed.recordGroups as string[]) || [];
          const records = (filtersParsed.records as string[]) || [];

          const knowledgeType = knowledgeItem.type || '';
          const isKB = knowledgeType === 'KB';

          const kbIdsInRecordGroups = recordGroups.filter((rgId: string) =>
            knowledgeBases.some((kb) => kb.id === rgId)
          );
          const hasKBIds = kbIdsInRecordGroups.length > 0;

          const isKnowledgeBase = isKB || hasKBIds;

          if (isKnowledgeBase) {
            const kbName = knowledgeItem.name || knowledgeItem.displayName || t('agentBuilder.nodeCollectionFallbackName');
            const kbDisplayName = knowledgeItem.displayName || knowledgeItem.name || kbName;
            const kbId = kbIdsInRecordGroups[0] || recordGroups[0] || knowledgeItem._key || '';

            const matchingKB = knowledgeBases.find((kb) => kb.id === kbId);

            const kbConnectorId = matchingKB?.connectorId || connectorId;
            const finalKbName = matchingKB?.name || kbDisplayName;
            const finalKbId = matchingKB?.id || kbId;

            nodeCounter += 1;
            const nodeId = `kb-${nodeCounter}`;
            const kbNode: Node<FlowNodeData> = {
              id: nodeId,
              type: 'flowNode',
              position: calculateOptimalPosition('knowledge', index, counts.knowledge),
              data: {
                id: nodeId,
                type: `kb-${finalKbId}`,
                label: truncateText(finalKbName, 18),
                description: t('agentBuilder.nodeKbDescRetrieval'),
                icon: 'folder',
                config: {
                  kbId: finalKbId,
                  kbName: finalKbName,
                  connectorInstanceId: kbConnectorId,
                  filters: {
                    recordGroups: [finalKbId],
                    records,
                  },
                  selectedRecords: records,
                  similarity: 0.8,
                },
                inputs: ['query'],
                outputs: ['context'],
                isConfigured: true,
              },
            };
            nodes.push(kbNode);
            knowledgeNodes.push(kbNode);
          } else {
            const appName = knowledgeItem.name || knowledgeItem.displayName || '';
            const appDisplayName = knowledgeItem.displayName || knowledgeItem.name || appName;

            const displayName =
              appDisplayName || (connectorId.split('/').pop() || connectorId) || t('agentBuilder.knowledgeSourceFallback');
            const normalizedAppSlug = displayName.toLowerCase().replace(/\s+/g, '');
            const appKnowledgeType = knowledgeItem.type?.toLowerCase().replace(/\s+/g, '') || normalizedAppSlug;

            nodeCounter += 1;
            const nodeId = `app-${nodeCounter}`;
            const appKnowledgeNode: Node<FlowNodeData> = {
              id: nodeId,
              type: 'flowNode',
              position: calculateOptimalPosition('knowledge', index, counts.knowledge),
              data: {
                id: nodeId,
                type: `app-${normalizedAppSlug}`,
                label: normalizeDisplayName(displayName),
                description: t('agentBuilder.appKnowledgeAccessDescription', { name: displayName }),
                icon: 'cloud',
                config: {
                  connectorInstanceId: connectorId,
                  appName: displayName,
                  appDisplayName: displayName,
                  connectorType: knowledgeItem.type || displayName,
                  filters: filtersParsed,
                  selectedRecordGroups: (filtersParsed.recordGroups as string[]) || [],
                  selectedRecords: (filtersParsed.records as string[]) || [],
                  iconPath: `/icons/connectors/${appKnowledgeType}.svg`,
                  similarity: 0.8,
                },
                inputs: ['query'],
                outputs: ['context'],
                isConfigured: true,
              },
            };
            nodes.push(appKnowledgeNode);
            knowledgeNodes.push(appKnowledgeNode);
          }
        });
      }


      // 3. Create LLM nodes
      const llmNodes: Node<FlowNodeData>[] = [];
      if (agent.models && agent.models.length > 0) {
        agent.models.forEach((modelConfig: AgentConfiguredModel | string, index) => {
          const isModelObject = typeof modelConfig === 'object' && modelConfig !== null;
          const modelConfigObj: ModelConfigObject | null = isModelObject ? (modelConfig as ModelConfigObject) : null;
          const modelConfigString: string | null = typeof modelConfig === 'string' ? modelConfig : null;

          // Match models with priority: modelKey > (modelName + provider) > modelName > provider
          let matchingModel = null;

          // If modelConfig is a string, try to match by modelKey directly
          if (modelConfigString) {
            matchingModel = modelCatalog.find((m) => m.modelKey === modelConfigString);
          } else if (modelConfigObj) {
            // First, try to match by modelKey (most specific and unique identifier)
            if (modelConfigObj.modelKey) {
              matchingModel = modelCatalog.find((m) => m.modelKey === modelConfigObj.modelKey);
            }

            // If no match by modelKey, try matching by modelName AND provider together
            if (!matchingModel && modelConfigObj.modelName && modelConfigObj.provider) {
              matchingModel = modelCatalog.find(
                (m) => m.modelName === modelConfigObj.modelName && m.provider === modelConfigObj.provider
              );
            }

            // If still no match, try just modelName
            if (!matchingModel && modelConfigObj.modelName) {
              matchingModel = modelCatalog.find((m) => m.modelName === modelConfigObj.modelName);
            }

            // Last resort: match by provider only
            if (!matchingModel && modelConfigObj.provider) {
              matchingModel = modelCatalog.find((m) => m.provider === modelConfigObj.provider);
            }
          }

          // Use friendly name if available, otherwise fallback to modelName
          const displayName =
            matchingModel?.modelFriendlyName ||
            modelConfigObj?.modelName ||
            modelConfigString ||
            t('agentBuilder.placeholderAiModel');
          const modelFriendlyName = matchingModel?.modelFriendlyName;

          nodeCounter += 1;
          const nodeId = `llm-${nodeCounter}`;
          const llmNode: Node<FlowNodeData> = {
            id: nodeId,
            type: 'flowNode',
            position: calculateOptimalPosition('llm', index, counts.llm),
            data: {
              id: nodeId,
              type: `llm-${matchingModel?.modelKey || modelConfigObj?.modelName?.replace(/[^a-zA-Z0-9]/g, '-') || modelConfigString?.replace(/[^a-zA-Z0-9]/g, '-') || 'default'}`,
              label: displayName.trim(),
              description: t('agentBuilder.llmLanguageModelSuffix', {
                provider: formattedProvider(modelConfigObj?.provider || 'AI'),
              }),
              icon: 'psychology',
              config: {
                modelKey: matchingModel?.modelKey || modelConfigObj?.modelKey || modelConfigString || modelConfigObj?.modelName || '',
                modelName: modelConfigObj?.modelName || modelConfigString || '',
                modelFriendlyName,
                provider: modelConfigObj?.provider || '',
                modelType: matchingModel?.modelType || modelConfigObj?.modelType || 'llm',
                isMultimodal: matchingModel?.isMultimodal ?? modelConfigObj?.isMultimodal ?? false,
                isDefault: matchingModel?.isDefault ?? modelConfigObj?.isDefault ?? false,
                isReasoning: matchingModel?.isReasoning ?? modelConfigObj?.isReasoning ?? false,
              },
              inputs: ['prompt', 'context'],
              outputs: ['response'],
              isConfigured: true,
            },
          };
          nodes.push(llmNode);
          llmNodes.push(llmNode);
        });
      } else if (modelCatalog.length > 0) {
        const defaultModel = modelCatalog[0];
        const displayName =
          defaultModel.modelFriendlyName || defaultModel.modelName || t('agentBuilder.placeholderAiModel');
        nodeCounter += 1;
        const nodeId = `llm-${nodeCounter}`;
        const llmNode: Node<FlowNodeData> = {
          id: nodeId,
          type: 'flowNode',
          position: calculateOptimalPosition('llm', 0, 1),
          data: {
            id: nodeId,
            type: `llm-${defaultModel.modelKey || 'default'}`,
            label: displayName.trim(),
            description: t('agentBuilder.llmLanguageModelSuffix', {
              provider: formattedProvider(defaultModel.provider || 'AI'),
            }),
            icon: 'psychology',
            config: {
              modelKey: defaultModel.modelKey,
              modelName: defaultModel.modelName,
              modelFriendlyName: defaultModel.modelFriendlyName,
              provider: defaultModel.provider,
              modelType: defaultModel.modelType,
              isMultimodal: defaultModel.isMultimodal,
              isDefault: defaultModel.isDefault,
              isReasoning: defaultModel.isReasoning,
            },
            inputs: [],
            outputs: ['response'],
            isConfigured: true,
          },
        };
        nodes.push(llmNode);
        llmNodes.push(llmNode);
      }

      // 4. Create Toolset nodes
      const toolsetNodes: Node<FlowNodeData>[] = [];

      const hasToolsets = agent.toolsets && agent.toolsets.length > 0;
      const hasLegacyTools = !hasToolsets && agent.tools && agent.tools.length > 0;

      if (hasToolsets && agent.toolsets) {
        agent.toolsets.forEach((toolset: AgentToolset, index: number) => {
          const toolsetName = toolset.name || '';
          const toolsetDisplayName = toolset.displayName || toolset.name || t('agentBuilder.toolsetDefaultName');
          const toolsetInstanceName = toolset.instanceName?.trim();
          const toolsetType = toolset.type || toolsetName;
          const toolsetInstanceId = toolset.instanceId as string | undefined;
          const normalizedToolsetName = toolsetName.toLowerCase().replace(/[^a-zA-Z0-9]/g, '-');
          const iconPath = `/icons/connectors/${normalizedToolsetName}.svg`;

          const toolsetTools: AgentToolDefinition[] = toolset.tools || [];

          const normalizeTool = (tool: AgentToolDefinition) => {
            const toolName = tool.name || tool.fullName?.split('.').pop() || t('agentBuilder.placeholderTool');
            const toolFullName = tool.fullName || `${toolsetName}.${toolName}`;

            const matchingTool = toolCatalog.find(
              (t) =>
                t.full_name === toolFullName ||
                t.tool_name === toolName ||
                t.tool_id === tool._key
            );

            return {
              name: toolName,
              fullName: toolFullName,
              description:
                tool.description ||
                matchingTool?.description ||
                t('agentBuilder.toolInlineSuffix', { name: toolsetDisplayName }),
              toolsetName,
            };
          };

          const normalizedTools = toolsetTools.map(normalizeTool);

          const catalogTools = toolCatalog
            .filter((t) => (t.app_name || '').toLowerCase() === toolsetName.toLowerCase())
            .map((t) => {
              const tn = t.tool_name || '';
              return {
                name: tn,
                fullName: t.full_name || (tn ? `${toolsetName}.${tn}` : ''),
                description: t.description || '',
                toolsetName,
              };
            })
            .filter((t) => t.name);

          const availByKey = new Map<string, (typeof normalizedTools)[0]>();
          const pushAvail = (t: (typeof normalizedTools)[0]) => {
            const k = t.fullName || `${toolsetName}.${t.name}`;
            if (k) availByKey.set(k, t);
          };
          normalizedTools.forEach(pushAvail);
          catalogTools.forEach((t: (typeof normalizedTools)[0]) => pushAvail(t));
          const availableTools = Array.from(availByKey.values());

          const selectedToolNames =
            toolset.selectedTools && toolset.selectedTools.length > 0
              ? (toolset.selectedTools as string[])
              : normalizedTools.map((t) => t.name);

          const selectedSet = new Set(selectedToolNames);
          const toolsOnNode = availableTools.filter(
            (t) => selectedSet.has(t.name) || selectedSet.has(t.fullName)
          );
          const toolsForNode = toolsOnNode.length > 0 ? toolsOnNode : normalizedTools;

          nodeCounter += 1;
          const nodeId = `toolset-${toolsetName}-${nodeCounter}`;
          const nodeHeaderLabel = toolsetInstanceName || toolsetDisplayName;
          const toolsetNode: Node<FlowNodeData> = {
            id: nodeId,
            type: 'flowNode',
            position: calculateOptimalPosition('tools', index, counts.toolsets),
            data: {
              id: nodeId,
              type: `toolset-${toolsetName}`,
              label: normalizeDisplayName(nodeHeaderLabel),
              description: t('agentBuilder.toolsetWithToolCount', {
                name: toolsetDisplayName,
                count: toolsForNode.length,
              }),
              icon: iconPath,
              category: 'toolset',
              config: {
                instanceId: toolsetInstanceId,
                instanceName: toolsetInstanceName || undefined,
                toolsetName,
                displayName: toolsetDisplayName,
                iconPath,
                category: toolsetType,
                tools: toolsForNode,
                availableTools,
                selectedTools: toolsForNode.map((t) => t.name),
                isConfigured: true,
                isAuthenticated: true,
              },
              inputs: [],
              outputs: ['output'],
              isConfigured: true,
            },
          };
          nodes.push(toolsetNode);
          toolsetNodes.push(toolsetNode);
        });
      } else if (hasLegacyTools && agent.tools) {
        type CatalogEntry = (typeof toolCatalog)[number];
        const toolsByApp = new Map<string, CatalogEntry[]>();

        agent.tools.forEach((toolName: string) => {
          const matchingTool = toolCatalog.find(
            (t) => t.full_name === toolName || t.tool_name === toolName || t.tool_id === toolName
          );

          if (matchingTool) {
            const appName = matchingTool.app_name || toolName.split('.')[0];
            if (!toolsByApp.has(appName)) {
              toolsByApp.set(appName, []);
            }
            toolsByApp.get(appName)!.push(matchingTool);
          }
        });

        let toolsetIndex = 0;
        toolsByApp.forEach((appTools, appName) => {
          const iconPath = `/icons/connectors/${appName.toLowerCase().replace(/[^a-zA-Z0-9]/g, '')}.svg`;

          const normalizedTools = appTools.map((tool: CatalogEntry) => ({
            name: tool.tool_name || tool.full_name?.split('.').pop() || t('agentBuilder.placeholderTool'),
            fullName: tool.full_name || `${appName}.${tool.tool_name}`,
            description: tool.description || t('agentBuilder.toolInlineSuffix', { name: appName }),
            toolsetName: appName,
          }));

          const catalogForApp = toolCatalog
            .filter((t) => (t.app_name || '').toLowerCase() === appName.toLowerCase())
            .map((t: CatalogEntry) => ({
              name: t.tool_name || '',
              fullName: t.full_name || `${appName}.${t.tool_name || ''}`,
              description: t.description || '',
              toolsetName: appName,
            }))
            .filter((t) => t.name);

          const legacyAvail = new Map<string, (typeof normalizedTools)[0]>();
          normalizedTools.forEach((t) => legacyAvail.set(t.fullName || `${appName}.${t.name}`, t));
          catalogForApp.forEach((t) => {
            const k = t.fullName || `${appName}.${t.name}`;
            if (!legacyAvail.has(k)) legacyAvail.set(k, t);
          });
          const availableLegacyTools = Array.from(legacyAvail.values());

          nodeCounter += 1;
          const nodeId = `toolset-${appName}-${nodeCounter}`;
          const toolsetNode: Node<FlowNodeData> = {
            id: nodeId,
            type: 'flowNode',
            position: calculateOptimalPosition('tools', toolsetIndex, toolsByApp.size),
            data: {
              id: nodeId,
              type: `toolset-${appName}`,
              label: normalizeDisplayName(appName),
              description: t('agentBuilder.toolsetWithToolCount', {
                name: appName,
                count: normalizedTools.length,
              }),
              icon: iconPath,
              category: 'toolset',
              config: {
                toolsetName: appName,
                displayName: appName,
                iconPath,
                category: 'app',
                tools: normalizedTools,
                availableTools: availableLegacyTools,
                selectedTools: normalizedTools.map((t) => t.name),
                isConfigured: true,
                isAuthenticated: true,
              },
              inputs: [],
              outputs: ['output'],
              isConfigured: true,
            },
          };
          nodes.push(toolsetNode);
          toolsetNodes.push(toolsetNode);
          toolsetIndex += 1;
        });
      }

      // 4b. Web search node (if the agent has one attached)
      const webSearchNodes: Node<FlowNodeData>[] = [];
      if (agent.webSearch?.provider) {
        nodeCounter += 1;
        const nodeId = `web-search-${nodeCounter}`;
        const wsNode: Node<FlowNodeData> = {
          id: nodeId,
          type: 'flowNode',
          position: calculateOptimalPosition('tools', counts.toolsets, Math.max(counts.toolsets + 1, 1)),
          data: {
            id: nodeId,
            type: 'web-search',
            label: agent.webSearch.providerLabel || agent.webSearch.provider,
            description: t('Web Search'),
            icon: 'public',
            category: 'tools',
            config: {
              provider: agent.webSearch.provider,
              providerKey: agent.webSearch.providerKey || '',
              providerLabel: agent.webSearch.providerLabel || '',
            },
            inputs: ['query'],
            outputs: ['results'],
            isConfigured: true,
          },
        };
        nodes.push(wsNode);
        webSearchNodes.push(wsNode);
      }

      // 5. Create Agent Core with optimal centered positioning
      const agentPosition = calculateAgentPosition();
      const agentCoreNode: Node<FlowNodeData> = {
        id: 'agent-core-1',
        type: 'flowNode',
        position: agentPosition,
        data: {
          id: 'agent-core-1',
          type: 'agent-core',
          label: normalizeDisplayName(t('agentBuilder.coreNodeTitle')),
          description: t('agentBuilder.agentCoreFlowDescription'),
          icon: 'auto_awesome',
          config: {
            systemPrompt: agent.systemPrompt || t('agentBuilder.defaultSystemPrompt'),
            instructions: agent.instructions ?? '',
            startMessage: agent.startMessage || t('agentBuilder.defaultStartMessage'),
            routing: 'auto',
            allowMultipleLLMs: true,
          },
          inputs: ['input', 'toolsets', 'knowledge', 'llms'],
          outputs: ['response'],
          isConfigured: true,
        },
      };
      nodes.push(agentCoreNode);

      // 6. Create Chat Response aligned with agent
      const chatResponseNode: Node<FlowNodeData> = {
        id: 'chat-response-1',
        type: 'flowNode',
        position: { x: layout.layers.output.x, y: agentPosition.y },
        data: {
          id: 'chat-response-1',
          type: 'chat-response',
          label: normalizeDisplayName(t('agentBuilder.nodeLabelChatOutput')),
          description: t('agentBuilder.nodeDescChatResponseFlow'),
          icon: 'reply',
          config: { format: 'text' },
          inputs: ['response'],
          outputs: [],
          isConfigured: true,
        },
      };
      nodes.push(chatResponseNode);

      let edgeCounter = 1;

      const edgeStyle = { stroke: FLOW_EDGE.line, strokeWidth: 1.5 };

      edges.push({
        id: `e-input-agent-${(edgeCounter += 1)}`,
        source: 'chat-input-1',
        target: 'agent-core-1',
        sourceHandle: 'message',
        targetHandle: 'input',
        type: 'smoothstep',
        style: edgeStyle,
        animated: false,
      });

      knowledgeNodes.forEach((knowledgeNode) => {
        edges.push({
          id: `e-knowledge-agent-${(edgeCounter += 1)}`,
          source: knowledgeNode.id,
          target: 'agent-core-1',
          sourceHandle: 'context',
          targetHandle: 'knowledge',
          type: 'smoothstep',
          style: edgeStyle,
          animated: false,
        });
      });

      llmNodes.forEach((llmNode) => {
        edges.push({
          id: `e-llm-agent-${(edgeCounter += 1)}`,
          source: llmNode.id,
          target: 'agent-core-1',
          sourceHandle: 'response',
          targetHandle: 'llms',
          type: 'smoothstep',
          style: edgeStyle,
          animated: false,
        });
      });

      toolsetNodes.forEach((toolsetNode) => {
        edges.push({
          id: `e-toolset-agent-${(edgeCounter += 1)}`,
          source: toolsetNode.id,
          target: 'agent-core-1',
          sourceHandle: 'output',
          targetHandle: 'toolsets',
          type: 'smoothstep',
          style: edgeStyle,
          animated: false,
        });
      });

      webSearchNodes.forEach((wsNode) => {
        edges.push({
          id: `e-web-search-agent-${(edgeCounter += 1)}`,
          source: wsNode.id,
          target: 'agent-core-1',
          sourceHandle: 'results',
          targetHandle: 'toolsets',
          type: 'smoothstep',
          style: edgeStyle,
          animated: false,
        });
      });

      edges.push({
        id: `e-agent-output-${(edgeCounter += 1)}`,
        source: 'agent-core-1',
        target: 'chat-response-1',
        sourceHandle: 'response',
        targetHandle: 'response',
        type: 'smoothstep',
        style: edgeStyle,
        animated: false,
      });

      return { nodes, edges };
    },
    [t]
  );

  return { reconstructFlowFromAgent };
}

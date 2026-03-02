// src/sections/qna/agents/hooks/useFlowReconstruction.ts
import { useCallback } from 'react';
import { useTheme } from '@mui/material';
import { Node, Edge } from '@xyflow/react';
import type { Agent, ConnectorInstance } from 'src/types/agent';
import brainIcon from '@iconify-icons/mdi/brain';
import chatIcon from '@iconify-icons/mdi/chat';
import collectionIcon from '@iconify-icons/mdi/folder-multiple';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import replyIcon from '@iconify-icons/mdi/reply';
import {
  truncateText,
  getAppIcon,
  getAppKnowledgeIcon,
  normalizeAppName,
  normalizeDisplayName,
  formattedProvider,
} from '../../utils/agent';
import type { UseAgentBuilderReconstructionReturn, NodeData } from '../../types/agent';

export const useAgentBuilderReconstruction = (): UseAgentBuilderReconstructionReturn => {
  const theme = useTheme();

  const reconstructFlowFromAgent = useCallback(
    (agent: Agent, models: any[], tools: any[], knowledgeBases: any[]) => {
      const nodes: Node<NodeData>[] = [];
      const edges: Edge[] = [];
      let nodeCounter = 1;

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
        llm: agent.models?.length || (models.length > 0 ? 1 : 0),
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
      const chatInputNode: Node<NodeData> = {
        id: 'chat-input-1',
        type: 'flowNode',
        position: calculateOptimalPosition('input', 0, 1),
        data: {
          id: 'chat-input-1',
          type: 'user-input',
          label: 'Chat Input',
          description: 'Receives user messages and queries',
          icon: chatIcon,
          config: { placeholder: 'Type your message…' },
          inputs: [],
          outputs: ['message'],
          isConfigured: true,
        },
      };
      nodes.push(chatInputNode);

      // 2. Create Knowledge nodes (Knowledge Bases + App Knowledge)
      const knowledgeNodes: Node<NodeData>[] = [];

      if (agent.knowledge && agent.knowledge.length > 0) {
        agent.knowledge.forEach((knowledgeItem: any, index: number) => {
          const connectorId = knowledgeItem.connectorId || '';
          const filters = knowledgeItem.filtersParsed || knowledgeItem.filters || {};
          
          let filtersParsed = filters;
          if (typeof filters === 'string') {
            try {
              filtersParsed = JSON.parse(filters);
            } catch {
              filtersParsed = {};
            }
          }
          
          const recordGroups = filtersParsed.recordGroups || [];
          const records = filtersParsed.records || [];
          
          const knowledgeType = knowledgeItem.type || '';
          const isKB = knowledgeType === 'KB';
          
          const kbIdsInRecordGroups = recordGroups.filter((rgId: string) => 
            knowledgeBases.some((kb) => kb.id === rgId)
          );
          const hasKBIds = kbIdsInRecordGroups.length > 0;
          
          const isKnowledgeBase = isKB || hasKBIds;
          
          if (isKnowledgeBase) {
            const kbName = knowledgeItem.name || knowledgeItem.displayName || 'Collection';
            const kbDisplayName = knowledgeItem.displayName || knowledgeItem.name || kbName;
            const kbId = kbIdsInRecordGroups[0] || recordGroups[0] || knowledgeItem._key || '';
            
            const matchingKB = knowledgeBases.find((kb) => kb.id === kbId);
            
            const kbConnectorId = matchingKB?.connectorId || connectorId;
            const finalKbName = matchingKB?.name || kbDisplayName;
            const finalKbId = matchingKB?.id || kbId;
            
            nodeCounter += 1;
            const nodeId = `kb-${nodeCounter}`;
            const kbNode: Node<NodeData> = {
              id: nodeId,
              type: 'flowNode',
              position: calculateOptimalPosition('knowledge', index, counts.knowledge),
              data: {
                id: nodeId,
                type: `kb-${finalKbId}`,
                label: `${truncateText(finalKbName, 18)}`,
                description: 'Collection for contextual information retrieval',
                icon: collectionIcon,
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
            
            const displayName = appDisplayName || (connectorId.split('/').pop() || connectorId) || 'Knowledge Source';
            const normalizedAppName = displayName.toLowerCase().replace(" ", "").replace(/\s+/g, '-');
            
            nodeCounter += 1;
            const nodeId = `app-${nodeCounter}`;
            const appKnowledgeNode: Node<NodeData> = {
              id: nodeId,
              type: 'flowNode',
              position: calculateOptimalPosition('knowledge', index, counts.knowledge),
              data: {
                id: nodeId,
                type: `app-${normalizedAppName}`,
                label: normalizeDisplayName(displayName),
                description: `Access ${displayName} knowledge and context`,
                icon: getAppKnowledgeIcon(displayName),
                config: {
                  connectorInstanceId: connectorId,
                  appName: displayName,
                  appDisplayName: displayName,
                  connectorType: knowledgeItem.type || displayName,
                  filters: filtersParsed,
                  selectedRecordGroups: filtersParsed.recordGroups || [],
                  selectedRecords: filtersParsed.records || [],
                  iconPath: `/assets/icons/connectors/${normalizedAppName}.svg`,
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
      const llmNodes: Node<NodeData>[] = [];
      if (agent.models && agent.models.length > 0) {
        agent.models.forEach((modelConfig, index) => {
          // Type guard: check if modelConfig is an object or a string
          type ModelConfigObject = {
            modelKey: string;
            provider: string;
            modelName: string;
            isReasoning?: boolean;
            isMultimodal?: boolean;
            isDefault?: boolean;
            modelType?: string;
            [key: string]: any;
          };
          
          const isModelObject = typeof modelConfig === 'object' && modelConfig !== null;
          const modelConfigObj: ModelConfigObject | null = isModelObject ? (modelConfig as ModelConfigObject) : null;
          const modelConfigString: string | null = typeof modelConfig === 'string' ? modelConfig : null;
          
          // Match models with priority: modelKey > (modelName + provider) > modelName > provider
          let matchingModel = null;
          
          // If modelConfig is a string, try to match by modelKey directly
          if (modelConfigString) {
            matchingModel = models.find((m) => m.modelKey === modelConfigString);
          } else if (modelConfigObj) {
            // First, try to match by modelKey (most specific and unique identifier)
            if (modelConfigObj.modelKey) {
              matchingModel = models.find((m) => m.modelKey === modelConfigObj.modelKey);
            }
            
            // If no match by modelKey, try matching by modelName AND provider together
            if (!matchingModel && modelConfigObj.modelName && modelConfigObj.provider) {
              matchingModel = models.find(
                (m) => m.modelName === modelConfigObj.modelName && m.provider === modelConfigObj.provider
              );
            }
            
            // If still no match, try just modelName
            if (!matchingModel && modelConfigObj.modelName) {
              matchingModel = models.find((m) => m.modelName === modelConfigObj.modelName);
            }
            
            // Last resort: match by provider only
            if (!matchingModel && modelConfigObj.provider) {
              matchingModel = models.find((m) => m.provider === modelConfigObj.provider);
            }
          }

          // Use friendly name if available, otherwise fallback to modelName
          const displayName = matchingModel?.modelFriendlyName || (modelConfigObj?.modelName) || modelConfigString || 'AI Model';
          const modelFriendlyName = matchingModel?.modelFriendlyName;

          nodeCounter += 1;
          const nodeId = `llm-${nodeCounter}`;
          const llmNode: Node<NodeData> = {
            id: nodeId,
            type: 'flowNode',
            position: calculateOptimalPosition('llm', index, counts.llm),
            data: {
              id: nodeId,
              type: `llm-${matchingModel?.modelKey || modelConfigObj?.modelName?.replace(/[^a-zA-Z0-9]/g, '-') || modelConfigString?.replace(/[^a-zA-Z0-9]/g, '-') || 'default'}`,
              label: displayName.trim(),
              description: `${formattedProvider(modelConfigObj?.provider || 'AI')} language model`,
              icon: brainIcon,
              config: {
                modelKey: matchingModel?.modelKey || modelConfigObj?.modelKey || modelConfigString || modelConfigObj?.modelName || '',
                modelName: modelConfigObj?.modelName || modelConfigString || '',
                modelFriendlyName,
                provider: modelConfigObj?.provider || '',
                modelType: matchingModel?.modelType || modelConfigObj?.modelType || 'llm',
                isMultimodal: matchingModel?.isMultimodal ?? modelConfigObj?.isMultimodal ?? false,
                isDefault: matchingModel?.isDefault ?? modelConfigObj?.isDefault ?? false,
                isReasoning: modelConfigObj?.isReasoning || false,
              },
              inputs: ['prompt', 'context'],
              outputs: ['response'],
              isConfigured: true,
            },
          };
          nodes.push(llmNode);
          llmNodes.push(llmNode);
        });
      } else if (models.length > 0) {
        const defaultModel = models[0];
        const displayName = defaultModel.modelFriendlyName || defaultModel.modelName || 'AI Model';
        nodeCounter += 1;
        const nodeId = `llm-${nodeCounter}`;
        const llmNode: Node<NodeData> = {
          id: nodeId,
          type: 'flowNode',
          position: calculateOptimalPosition('llm', 0, 1),
          data: {
            id: nodeId,
            type: `llm-${defaultModel.modelKey || 'default'}`,
            label: displayName.trim(),
            description: `${formattedProvider(defaultModel.provider || 'AI')} language model`,
            icon: brainIcon,
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
      const toolsetNodes: Node<NodeData>[] = [];
      
      const hasToolsets = agent.toolsets && agent.toolsets.length > 0;
      const hasLegacyTools = !hasToolsets && agent.tools && agent.tools.length > 0;
      
      if (hasToolsets && agent.toolsets) {
        agent.toolsets.forEach((toolset: any, index: number) => {
          const toolsetName = toolset.name || '';
          const toolsetDisplayName = toolset.displayName || toolset.name || 'Toolset';
          const toolsetType = toolset.type || toolsetName;
          const toolsetInstanceId = toolset.instanceId as string | undefined; // NEW
          const normalizedToolsetName = toolsetName.toLowerCase().replace(/[^a-zA-Z0-9]/g, '-');
          const iconPath = `/assets/icons/connectors/${normalizedToolsetName}.svg`;
          
          const toolsetTools = toolset.tools || [];
          
          const normalizeTool = (tool: any) => {
            const toolName = tool.name || tool.fullName?.split('.').pop() || 'Tool';
            const toolFullName = tool.fullName || `${toolsetName}.${toolName}`;
            
            const matchingTool = tools.find(
              (t) => t.full_name === toolFullName || 
                     t.tool_name === toolName ||
                     t.tool_id === tool._key
            );
            
            return {
              name: toolName,
              fullName: toolFullName,
              description: tool.description || matchingTool?.description || `${toolsetDisplayName} tool`,
              toolsetName,
            };
          };
          
          const normalizedTools = toolsetTools.map(normalizeTool);
          
          const selectedToolNames = toolset.selectedTools && toolset.selectedTools.length > 0
            ? toolset.selectedTools
            : normalizedTools.map((t: any) => t.name);
          
          nodeCounter += 1;
          const nodeId = `toolset-${toolsetName}-${nodeCounter}`;
          const toolsetNode: Node<NodeData> = {
            id: nodeId,
            type: 'flowNode',
            position: calculateOptimalPosition('tools', index, counts.toolsets),
            data: {
              id: nodeId,
              type: `toolset-${toolsetName}`,
              label: normalizeDisplayName(toolsetDisplayName),
              description: `${toolsetDisplayName} with ${normalizedTools.length} tools`,
              icon: iconPath,
              category: 'toolset',
              config: {
                instanceId: toolsetInstanceId,  // NEW
                toolsetName,
                displayName: toolsetDisplayName,
                iconPath,
                category: toolsetType,
                tools: normalizedTools,
                availableTools: normalizedTools,
                selectedTools: selectedToolNames,
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
        const toolsByApp = new Map<string, any[]>();
        
        agent.tools.forEach((toolName: string) => {
          const matchingTool = tools.find(
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
          const iconPath = `/assets/icons/connectors/${appName.toLowerCase().replace(/[^a-zA-Z0-9]/g, '')}.svg`;
          
          const normalizedTools = appTools.map((tool: any) => ({
            name: tool.tool_name || tool.full_name?.split('.').pop() || 'Tool',
            fullName: tool.full_name || `${appName}.${tool.tool_name}`,
            description: tool.description || `${appName} tool`,
            toolsetName: appName,
          }));
          
          nodeCounter += 1;
          const nodeId = `toolset-${appName}-${nodeCounter}`;
          const toolsetNode: Node<NodeData> = {
            id: nodeId,
            type: 'flowNode',
            position: calculateOptimalPosition('tools', toolsetIndex, toolsByApp.size),
            data: {
              id: nodeId,
              type: `toolset-${appName}`,
              label: normalizeDisplayName(appName),
              description: `${appName} with ${normalizedTools.length} tools`,
              icon: iconPath,
              category: 'toolset',
              config: {
                toolsetName: appName,
                displayName: appName,
                iconPath,
                category: 'app',
                tools: normalizedTools,
                availableTools: normalizedTools,
                selectedTools: normalizedTools.map((t: any) => t.name),
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

      // 5. Create Agent Core with optimal centered positioning
      const agentPosition = calculateAgentPosition();
      const agentCoreNode: Node<NodeData> = {
        id: 'agent-core-1',
        type: 'flowNode',
        position: agentPosition,
        data: {
          id: 'agent-core-1',
          type: 'agent-core',
          label: normalizeDisplayName('Agent Core'),
          description: 'Central orchestrator and decision engine',
          icon: sparklesIcon,
          config: {
            systemPrompt: agent.systemPrompt || 'You are a helpful assistant.',
            instructions: agent.instructions ?? '',
            startMessage:
              agent.startMessage || 'Hello! I am ready to assist you. How can I help you today?',
            routing: 'auto',
            allowMultipleLLMs: true,
          },
          inputs: ['input', 'actions', 'knowledge', 'llms'],
          outputs: ['response'],
          isConfigured: true,
        },
      };
      nodes.push(agentCoreNode);

      // 6. Create Chat Response aligned with agent
      const chatResponseNode: Node<NodeData> = {
        id: 'chat-response-1',
        type: 'flowNode',
        position: { x: layout.layers.output.x, y: agentPosition.y },
        data: {
          id: 'chat-response-1',
          type: 'chat-response',
          label: normalizeDisplayName('Chat Response'),
          description: 'Formatted output delivery to users',
          icon: replyIcon,
          config: { format: 'text' },
          inputs: ['response'],
          outputs: [],
          isConfigured: true,
        },
      };
      nodes.push(chatResponseNode);

      // Create elegant edges with enhanced styling
      let edgeCounter = 1;

      // Input to Agent - Primary flow
      edges.push({
        id: `e-input-agent-${(edgeCounter += 1)}`,
        source: 'chat-input-1',
        target: 'agent-core-1',
        sourceHandle: 'message',
        targetHandle: 'input',
        type: 'smoothstep',
        style: {
          stroke: theme.palette.primary.main,
          strokeWidth: 2,
          strokeDasharray: '0',
        },
        animated: false,
      });

      // Knowledge to Agent connections
      knowledgeNodes.forEach((knowledgeNode) => {
        edges.push({
          id: `e-knowledge-agent-${(edgeCounter += 1)}`,
          source: knowledgeNode.id,
          target: 'agent-core-1',
          sourceHandle: 'context',
          targetHandle: 'knowledge',
          type: 'smoothstep',
          style: {
            stroke: theme.palette.secondary.main,
            strokeWidth: 2,
            strokeDasharray: '0',
          },
          animated: false,
        });
      });

      // LLM to Agent connections
      llmNodes.forEach((llmNode) => {
        edges.push({
          id: `e-llm-agent-${(edgeCounter += 1)}`,
          source: llmNode.id,
          target: 'agent-core-1',
          sourceHandle: 'response',
          targetHandle: 'llms',
          type: 'smoothstep',
          style: {
            stroke: theme.palette.info.main,
            strokeWidth: 2,
            strokeDasharray: '0',
          },
          animated: false,
        });
      });

      // Toolset → Agent connections
      // Toolsets connect directly to agent's toolsets handle
      toolsetNodes.forEach((toolsetNode) => {
        edges.push({
          id: `e-toolset-agent-${(edgeCounter += 1)}`,
          source: toolsetNode.id,
          target: 'agent-core-1',
          sourceHandle: 'output',
          targetHandle: 'toolsets',
          type: 'smoothstep',
          style: {
            stroke: theme.palette.warning.main,
            strokeWidth: 2,
            strokeDasharray: '0',
          },
          animated: false,
        });
      });

      // Agent to Output - Final flow
      edges.push({
        id: `e-agent-output-${(edgeCounter += 1)}`,
        source: 'agent-core-1',
        target: 'chat-response-1',
        sourceHandle: 'response',
        targetHandle: 'response',
        type: 'smoothstep',
        style: {
          stroke: theme.palette.success.main,
          strokeWidth: 2,
          strokeDasharray: '0',
        },
        animated: false,
      });

      return { nodes, edges };
    },
    [theme]
  );

  return { reconstructFlowFromAgent };
};

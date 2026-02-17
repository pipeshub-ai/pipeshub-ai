// src/sections/qna/agents/hooks/useFlowReconstruction.ts
import { useCallback } from 'react';
import { useTheme } from '@mui/material';
import { Node, Edge } from '@xyflow/react';
import type { Agent, ConnectorInstance } from 'src/types/agent';
import brainIcon from '@iconify-icons/mdi/brain';
import chatIcon from '@iconify-icons/mdi/chat';
import databaseIcon from '@iconify-icons/mdi/database';
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
        // Five distinct layers with proper separation to prevent overlap
        layers: {
          input: { x: 420, baseY: 1250 }, // Layer 1: User Input
          preprocessing: { x: 100, baseY: 300 }, // Layer 2: Knowledge & Context
          processing: { x: 400, baseY: 450 }, // Layer 3: LLMs & Tools
          agent: { x: 1000, baseY: 450 }, // Layer 4: Agent Core
          output: { x: 1500, baseY: 850 }, // Layer 5: Response Output
        },

        // Dynamic spacing based on node density
        spacing: {
          // Base spacing for different node counts
          getSectionSpacing: (nodeCount: number) => {
            if (nodeCount <= 2) return 160;
            if (nodeCount <= 4) return 140;
            if (nodeCount <= 6) return 120;
            return 100;
          },

          // Vertical distribution within sections
          getVerticalSpacing: (nodeCount: number) => {
            if (nodeCount === 1) return 0;
            if (nodeCount <= 3) return 180;
            if (nodeCount <= 5) return 150;
            return 120;
          },

          // Horizontal spacing between node types in same layer
          typeOffset: 180,

          // Minimum gaps
          minGap: 100,
          maxGap: 250,
        },

        // Sub-positioning within processing layers - Increased separation
        sections: {
          knowledge: {
            baseX: -150, // Further left of preprocessing layer
            priority: 1, // Higher visual priority
          },
          llm: {
            baseX: -100, // Left side of processing layer
            priority: 2,
          },
          tools: {
            baseX: 120, // Right side of processing layer with more space
            priority: 3,
          },
        },
      };

      // Calculate node counts for intelligent positioning
      const counts = {
        llm: agent.models?.length || (models.length > 0 ? 1 : 0),
        tools: agent.tools?.length || 0,
        knowledge: (agent.kb?.length || 0) + (agent.connectors?.filter(ci => ci.category === 'knowledge').length || 0),
      };

      // Smart positioning system with visual balance
      const calculateOptimalPosition = (
        layer: keyof typeof layout.layers,
        section: 'input' | 'knowledge' | 'llm' | 'tools' | 'agent' | 'output',
        index: number,
        totalInSection: number
      ) => {
        const baseLayer = layout.layers[layer];
        const spacing = layout.spacing.getVerticalSpacing(totalInSection);

        // Calculate vertical centering with intelligent distribution
        const getVerticalPosition = () => {
          if (totalInSection === 1) {
            return baseLayer.baseY;
          }

          // For multiple nodes, create elegant vertical distribution
          const totalHeight = (totalInSection - 1) * spacing;
          const startY = baseLayer.baseY - totalHeight / 2;
          return startY + index * spacing;
        };

        // Calculate horizontal position with section-aware logic
        const getHorizontalPosition = () => {
          switch (section) {
            case 'input':
            case 'agent':
            case 'output':
              return baseLayer.x;

            case 'knowledge':
              return layout.layers.preprocessing.x + layout.sections.knowledge.baseX;

            case 'llm': {
              // Adjust LLM position based on memory presence
              let llmX = baseLayer.x + layout.sections.llm.baseX;
              if (counts.knowledge > 0) {
                llmX += layout.spacing.typeOffset * 0.5; // More separation from knowledge
              }
              return llmX;
            }

            case 'tools': {
              // Tools positioned right, considering other node types
              let toolsX = baseLayer.x + layout.sections.tools.baseX;
              if (counts.llm > 0) {
                toolsX += layout.spacing.typeOffset * 0.7; // Even more separation
              }
              return toolsX;
            }

            default:
              return baseLayer.x;
          }
        };

        return {
          x: getHorizontalPosition(),
          y: getVerticalPosition(),
        };
      };

      // Enhanced agent positioning with visual balance consideration
      const calculateAgentPosition = () => {
        const connectedPositions: { x: number; y: number; weight: number }[] = [];

        // Collect all processing node positions with weights
        const addPositions = (
          section: 'knowledge' | 'llm' | 'tools',
          count: number,
          weight: number
        ) => {
          for (let i = 0; i < count; i += 1) {
            const pos = calculateOptimalPosition('preprocessing', section, i, count);
            if (section === 'llm') {
              pos.x = calculateOptimalPosition('processing', section, i, count).x;
            }
            connectedPositions.push({ ...pos, weight });
          }
        };

        // Add positions with different weights for visual balance
        if (counts.knowledge > 0) addPositions('knowledge', counts.knowledge, 1.2);
        if (counts.llm > 0) addPositions('llm', counts.llm, 2.0); // Higher weight for LLMs
        if (counts.tools > 0) addPositions('tools', counts.tools, 1.5);

        if (connectedPositions.length === 0) {
          return { x: layout.layers.agent.x, y: layout.layers.agent.baseY };
        }

        // Weighted center calculation for optimal visual balance
        const totalWeight = connectedPositions.reduce((sum, pos) => sum + pos.weight, 0);
        const weightedY =
          connectedPositions.reduce((sum, pos) => sum + pos.y * pos.weight, 0) / totalWeight;

        // Apply constraints for better visual bounds
        const constrainedY = Math.max(250, Math.min(weightedY, 650));

        return {
          x: layout.layers.agent.x,
          y: constrainedY,
        };
      };

      // 1. Create Chat Input node with enhanced positioning
      const chatInputNode: Node<NodeData> = {
        id: 'chat-input-1',
        type: 'flowNode',
        position: calculateOptimalPosition('input', 'input', 0, 1),
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

      // 2. Create Knowledge nodes first (Knowledge Bases + App Knowledge) - Left side
      const knowledgeNodes: Node<NodeData>[] = [];
      let knowledgeIndex = 0;

      // Knowledge Base nodes (KB)
      if (agent.kb && agent.kb.length > 0) {
        agent.kb.forEach((kbId) => {
          const matchingKB = knowledgeBases.find((kb) => kb.id === kbId);
          if (matchingKB) {
            const kbNode: Node<NodeData> = {
              id: `kb-${(nodeCounter += 1)}`,
              type: 'flowNode',
              position: calculateOptimalPosition(
                'preprocessing',
                'knowledge',
                (knowledgeIndex += 1),
                counts.knowledge
              ),
              data: {
                id: `kb-${nodeCounter - 1}`,
                type: `kb-${matchingKB.id}`,
                label: `KB: ${truncateText(matchingKB.name, 18)}`,
                description: 'Knowledge base for contextual information retrieval',
                icon: databaseIcon,
                config: {
                  kbId: matchingKB.id,
                  kbName: matchingKB.name,
                  similarity: 0.8,
                },
                inputs: ['query'],
                outputs: ['context'],
                isConfigured: true,
              },
            };
            nodes.push(kbNode);
            knowledgeNodes.push(kbNode);
          }
        });
      }

      // App Knowledge nodes
      // Handle knowledge connector instances (category: 'knowledge')
      const knowledgeConnectorInstances = agent.connectors?.filter(ci => ci.category === 'knowledge') || [];
      
      // Process connectors with category='knowledge'
      knowledgeConnectorInstances.forEach((connectorInstance: ConnectorInstance) => {
        const normalizedAppName = connectorInstance.type.toLowerCase().replace(/[^a-zA-Z0-9]/g, '-').replace(/\s+/g, '-');
        const appKnowledgeNode: Node<NodeData> = {
          id: `app-${(nodeCounter += 1)}`,
          type: 'flowNode',
          position: calculateOptimalPosition(
            'preprocessing',
            'knowledge',
            (knowledgeIndex += 1),
            counts.knowledge
          ),
          data: {
            id: `app-${nodeCounter - 1}`,
            type: `app-${normalizedAppName}`,
            label: normalizeDisplayName(connectorInstance.name || connectorInstance.type),
            description: `Access ${connectorInstance.name || connectorInstance.type} knowledge and context`,
            icon: getAppKnowledgeIcon(connectorInstance.type),
            config: {
              connectorInstanceId: connectorInstance.id,
              appName: connectorInstance.type,
              appDisplayName: connectorInstance.name || connectorInstance.type,
              connectorType: connectorInstance.type,
              scope: connectorInstance.scope,
              iconPath: `/assets/icons/connectors/${connectorInstance.type.replace(" ", "").toLowerCase()}.svg`,
              similarity: 0.8,
            },
            inputs: ['query'],
            outputs: ['context'],
            isConfigured: true,
          },
        };
        nodes.push(appKnowledgeNode);
        knowledgeNodes.push(appKnowledgeNode);
      });
      

      // 3. Create LLM nodes with intelligent positioning
      const llmNodes: Node<NodeData>[] = [];
      if (agent.models && agent.models.length > 0) {
        agent.models.forEach((modelConfig, index) => {
          // Match models with priority: modelKey > (modelName + provider) > modelName > provider
          let matchingModel = null;
          const modelConfigAny = modelConfig as any; // Type assertion for optional fields that may exist in runtime data
          
          // First, try to match by modelKey (most specific and unique identifier)
          if (modelConfigAny.modelKey) {
            matchingModel = models.find((m) => m.modelKey === modelConfigAny.modelKey);
          }
          
          // If no match by modelKey, try matching by modelName AND provider together
          if (!matchingModel && modelConfig.modelName && modelConfig.provider) {
            matchingModel = models.find(
              (m) => m.modelName === modelConfig.modelName && m.provider === modelConfig.provider
            );
          }
          
          // If still no match, try just modelName
          if (!matchingModel && modelConfig.modelName) {
            matchingModel = models.find((m) => m.modelName === modelConfig.modelName);
          }
          
          // Last resort: match by provider only
          if (!matchingModel && modelConfig.provider) {
            matchingModel = models.find((m) => m.provider === modelConfig.provider);
          }

          // Use friendly name if available, otherwise fallback to modelName
          const displayName = matchingModel?.modelFriendlyName || modelConfig.modelName || 'AI Model';
          const modelFriendlyName = matchingModel?.modelFriendlyName;
          
          console.log('matchingModel', matchingModel);
          const llmNode: Node<NodeData> = {
            id: `llm-${(nodeCounter += 1)}`,
            type: 'flowNode',
            position: calculateOptimalPosition('processing', 'llm', index, counts.llm),
            data: {
              id: `llm-${nodeCounter - 1}`,
              type: `llm-${matchingModel?.modelKey || modelConfig.modelName?.replace(/[^a-zA-Z0-9]/g, '-') || 'default'}`,
              label: displayName.trim(),
              description: `${formattedProvider(modelConfig.provider || 'AI')} language model`,
              icon: brainIcon,
              config: {
                modelKey: matchingModel?.modelKey || modelConfigAny.modelKey || modelConfig.modelName,
                modelName: modelConfig.modelName,
                modelFriendlyName,
                provider: modelConfig.provider,
                modelType: matchingModel?.modelType || modelConfigAny.modelType || 'llm',
                isMultimodal: matchingModel?.isMultimodal ?? modelConfigAny.isMultimodal ?? false,
                isDefault: matchingModel?.isDefault ?? modelConfigAny.isDefault ?? false,
                isReasoning: modelConfig.isReasoning || false,
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
        // Add default model with optimal positioning
        const defaultModel = models[0];
        const displayName = defaultModel.modelFriendlyName || defaultModel.modelName || 'AI Model';
        const llmNode: Node<NodeData> = {
          id: `llm-${(nodeCounter += 1)}`,
          type: 'flowNode',
          position: calculateOptimalPosition('processing', 'llm', 0, 1),
          data: {
            id: `llm-${nodeCounter - 1}`,
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

      // 4. Create Individual Tool nodes from agent.tools (the actual saved tools)
      const individualToolNodes: Node<NodeData>[] = [];
      const toolGroupNodes: Node<NodeData>[] = [];
      
      if (agent.tools && agent.tools.length > 0) {
        // Agent has specific tools saved - create individual tool nodes
        agent.tools.forEach((toolName, index) => {
          const matchingTool = tools.find(
            (t) => t.full_name === toolName || t.tool_name === toolName || t.tool_id === toolName
          );

          if (matchingTool) {
            // Find the connector instance for this tool
            const connectorInstance = agent.connectors?.find(
              (ci) => ci.type?.toUpperCase() === matchingTool.app_name?.toUpperCase() ||
                     ci.type?.toLowerCase() === matchingTool.app_name?.toLowerCase()
            );

            // Construct iconPath from connector type
            const connectorType = connectorInstance?.type || matchingTool.app_name;
            const iconPath = `/assets/icons/connectors/${connectorType.replace(" ", "").toLowerCase()}.svg`;

            const toolNode: Node<NodeData> = {
              id: `tool-${(nodeCounter += 1)}`,
              type: 'flowNode',
              position: calculateOptimalPosition('processing', 'tools', index, agent.tools.length),
              data: {
                id: `tool-${nodeCounter - 1}`,
                type: `tool-${matchingTool.tool_id}`,
                label: normalizeDisplayName(matchingTool.tool_name.replace(/_/g, ' ')),
                description: matchingTool.description || `${matchingTool.app_name} integration`,
                icon: getAppIcon(matchingTool.app_name),
                config: {
                  toolId: matchingTool.tool_id,
                  fullName: matchingTool.full_name,
                  appName: matchingTool.app_name,
                  parameters: matchingTool.parameters || [],
                  // Associate with connector instance
                  connectorInstanceId: connectorInstance?.id,
                  connectorType: connectorInstance?.type || matchingTool.app_name,
                  connectorName: connectorInstance?.name || connectorInstance?.type,
                  scope: connectorInstance?.scope || 'personal',
                  iconPath, // Add connector icon for visual distinction
                },
                inputs: ['input'],
                outputs: ['output'],
                isConfigured: true,
              },
            };
            nodes.push(toolNode);
            individualToolNodes.push(toolNode);
          }
        });
      } else if (agent.connectors && Array.isArray(agent.connectors)) {
        // No specific tools saved, fallback to creating tool-group nodes from connector instances with category='action'
        const actionConnectorInstances = agent.connectors.filter(ci => ci.category === 'action');
        actionConnectorInstances.forEach((connectorInstance: ConnectorInstance, index: number) => {
          // Find all tools that belong to this connector type
          const connectorTools = tools.filter((t) => 
            t.app_name?.toUpperCase() === connectorInstance.type?.toUpperCase() ||
            t.app_name?.toLowerCase() === connectorInstance.type?.toLowerCase()
          );
          
          // Construct iconPath from connector type
          const groupIconPath = `/assets/icons/connectors/${connectorInstance.type.replace(" ", "").toLowerCase()}.svg`;
          
          // Create a tool-group node with all the connector's tools
          const toolGroupNode: Node<NodeData> = {
            id: `tool-group-${(nodeCounter += 1)}`,
            type: 'flowNode',
            position: calculateOptimalPosition('processing', 'tools', index, actionConnectorInstances.length),
            data: {
              id: `tool-group-${nodeCounter - 1}`,
              type: `tool-group-${connectorInstance.type?.toLowerCase().replace(/\s+/g, '-') || connectorInstance.name?.toLowerCase().replace(/\s+/g, '-') || 'tools'}`,
              label: normalizeDisplayName(connectorInstance.name || connectorInstance.type),
              description: `${connectorInstance.type} with ${connectorTools.length} tools`,
              icon: getAppIcon(connectorInstance.type || connectorInstance.name),
              config: {
                connectorInstanceId: connectorInstance.id,
                connectorType: connectorInstance.type,
                connectorName: connectorInstance.name || connectorInstance.type,
                appName: connectorInstance.type,
                appDisplayName: connectorInstance.name || connectorInstance.type,
                scope: connectorInstance.scope,
                iconPath: groupIconPath, // Add connector icon
                tools: connectorTools.map(t => ({
                  toolId: t.tool_id,
                  fullName: t.full_name,
                  toolName: t.tool_name,
                  appName: t.app_name,
                  description: t.description
                })),
                selectedTools: connectorTools.map(t => t.tool_id), // All tools selected by default
              },
              inputs: ['input'],
              outputs: ['output'],
              isConfigured: true,
            },
          };
          nodes.push(toolGroupNode);
          toolGroupNodes.push(toolGroupNode);
        });
      }

      // 6. Create Agent Core with optimal centered positioning
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

      // 7. Create Chat Response with aligned positioning
      const chatResponseNode: Node<NodeData> = {
        id: 'chat-response-1',
        type: 'flowNode',
        position: { x: layout.layers.output.x, y: agentPosition.y }, // Align with agent
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

      // Knowledge to Agent connections - Context flow
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

      // LLM to Agent connections - Processing flow
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

      // Individual Tools → Agent connections (if individual tools exist)
      individualToolNodes.forEach((toolNode) => {
        edges.push({
          id: `e-tool-agent-${(edgeCounter += 1)}`,
          source: toolNode.id,
          target: 'agent-core-1',
          sourceHandle: 'output',
          targetHandle: 'actions',
          type: 'smoothstep',
          style: {
            stroke: theme.palette.warning.main,
            strokeWidth: 2,
            strokeDasharray: '0',
          },
          animated: false,
        });
      });

      // Tool Group → Agent connections (fallback if no individual tools)
      // Tools can now connect directly to agent's actions handle
      // No need for intermediate connector instance nodes
      // Flow: Tool Group (source) → Agent actions handle (target)
      toolGroupNodes.forEach((toolGroupNode) => {
        edges.push({
          id: `e-toolgroup-agent-${(edgeCounter += 1)}`,
          source: toolGroupNode.id,
          target: 'agent-core-1',
          sourceHandle: 'output',
          targetHandle: 'actions',
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

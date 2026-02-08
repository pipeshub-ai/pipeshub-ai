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

          // Vertical distribution within sections - improved for better balance
          getVerticalSpacing: (nodeCount: number) => {
            if (nodeCount === 1) return 0;
            if (nodeCount <= 2) return 200;
            if (nodeCount <= 4) return 180;
            if (nodeCount <= 6) return 150;
            return 130;
          },

          // Horizontal spacing between node types in same layer
          typeOffset: 200,

          // Minimum gaps
          minGap: 100,
          maxGap: 250,
        },

        // Sub-positioning within processing layers - Better separation for toolsets
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
            baseX: 150, // Right side of processing layer with more space for toolsets
            priority: 3,
          },
        },
      };

      // Calculate node counts for intelligent positioning
      // For toolsets: count the number of toolset nodes (one per toolset)
      // For legacy tools: count unique apps (tools will be grouped by app)
      let toolsetsCount = 0;
      if (agent.toolsets && agent.toolsets.length > 0) {
        toolsetsCount = agent.toolsets.length;
      } else if (agent.tools && agent.tools.length > 0) {
        // For legacy tools, count unique apps
        const uniqueApps = new Set<string>();
        agent.tools.forEach((toolName: string) => {
          const appName = toolName.split('.')[0];
          uniqueApps.add(appName);
        });
        toolsetsCount = uniqueApps.size;
      }
      
      const counts = {
        llm: agent.models?.length || (models.length > 0 ? 1 : 0),
        tools: 0, // Not used for positioning anymore - toolsets are used instead
        toolsets: toolsetsCount,
        knowledge: agent.knowledge?.length || 0,
      };

      // Calculate total processing nodes for better vertical distribution
      const totalProcessingNodes = counts.knowledge + counts.llm + counts.toolsets;
      
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
          // For processing layer nodes (knowledge, llm, tools), use combined distribution
          if (section === 'knowledge' || section === 'llm' || section === 'tools') {
            // Calculate cumulative index across all processing sections
            let cumulativeIndex = 0;
            if (section === 'knowledge') {
              cumulativeIndex = index;
            } else if (section === 'llm') {
              cumulativeIndex = counts.knowledge + index;
            } else if (section === 'tools') {
              cumulativeIndex = counts.knowledge + counts.llm + index;
            }
            
            // Use total processing nodes for better spacing
            if (totalProcessingNodes === 1) {
              return baseLayer.baseY;
            }
            
            // Better spacing when multiple types of nodes exist
            const combinedSpacing = totalProcessingNodes <= 3 ? 200 : totalProcessingNodes <= 5 ? 180 : 150;
            const totalHeight = (totalProcessingNodes - 1) * combinedSpacing;
            const startY = baseLayer.baseY - totalHeight / 2;
            return startY + cumulativeIndex * combinedSpacing;
          }
          
          // For other sections, use section-specific distribution
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
              // Toolsets positioned right, considering other node types
              let toolsX = baseLayer.x + layout.sections.tools.baseX;
              // Add spacing based on presence of LLMs and knowledge nodes
              if (counts.llm > 0) {
                toolsX += layout.spacing.typeOffset * 0.6; // Good separation from LLMs
              }
              if (counts.knowledge > 0 && counts.llm === 0) {
                toolsX += layout.spacing.typeOffset * 0.3; // Some separation if only knowledge exists
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
            // Knowledge nodes use preprocessing layer, LLMs and toolsets use processing layer
            const layer = section === 'knowledge' ? 'preprocessing' : 'processing';
            const pos = calculateOptimalPosition(layer, section, i, count);
            connectedPositions.push({ ...pos, weight });
          }
        };

        // Add positions with different weights for visual balance
        if (counts.knowledge > 0) addPositions('knowledge', counts.knowledge, 1.2);
        if (counts.llm > 0) addPositions('llm', counts.llm, 2.0); // Higher weight for LLMs
        if (counts.toolsets > 0) addPositions('tools', counts.toolsets, 1.5);

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

      // Knowledge nodes from agent.knowledge (includes both apps and KBs)
      if (agent.knowledge && agent.knowledge.length > 0) {
        agent.knowledge.forEach((knowledgeItem: any) => {
          const connectorId = knowledgeItem.connectorId || '';
          const filters = knowledgeItem.filtersParsed || knowledgeItem.filters || {};
          
          // Parse filters if it's a string
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
          
          // Determine if this is a KB or an App:
          // - KBs: recordGroups contain KB IDs (record group IDs), and connectorId might be KB connector or KB ID
          // - Apps: connectorId is a connector instance ID, recordGroups are optional (record groups within connector)
          // We identify KBs by checking if recordGroups contain KB IDs that match knowledgeBases
          const kbIdsInRecordGroups = recordGroups.filter((rgId: string) => 
            knowledgeBases.some((kb) => kb.id === rgId)
          );
          
          const isKB = kbIdsInRecordGroups.length > 0;
          
          if (isKB) {
            // This is a KB - create KB nodes for each KB record group
            kbIdsInRecordGroups.forEach((kbId: string) => {
              const matchingKB = knowledgeBases.find((kb) => kb.id === kbId);
              if (matchingKB) {
                // Use connectorId from KB document (the actual KB connector instance ID)
                // Fallback to connectorId from knowledge item if KB document doesn't have it
                const kbConnectorId = matchingKB.connectorId || connectorId;
                
                nodeCounter += 1;
                knowledgeIndex += 1;
                const nodeId = `kb-${nodeCounter}`;
                const kbNode: Node<NodeData> = {
                  id: nodeId,
                  type: 'flowNode',
                  position: calculateOptimalPosition(
                    'preprocessing',
                    'knowledge',
                    knowledgeIndex,
                    counts.knowledge
                  ),
                  data: {
                    id: nodeId,
                    type: `kb-${matchingKB.id}`,
                    label: `KB: ${truncateText(matchingKB.name, 18)}`,
                    description: 'Knowledge base for contextual information retrieval',
                    icon: databaseIcon,
                    config: {
                      kbId: matchingKB.id, // KB ID (record group ID)
                      kbName: matchingKB.name,
                      connectorInstanceId: kbConnectorId, // KB connector instance ID from KB document
                      filters: {
                        recordGroups: [kbId], // KB ID is the record group ID
                        records, // Records within this KB
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
              }
            });
          } else {
            // This is an app connector - create app knowledge node
            // Apps are connectors with connectorId and optional recordGroups/records filters
            const displayName = connectorId.split('/').pop() || connectorId || 'Knowledge Source';
            const normalizedAppName = displayName.toLowerCase().replace(/[^a-zA-Z0-9]/g, '-').replace(/\s+/g, '-');
            
            nodeCounter += 1;
            knowledgeIndex += 1;
            const nodeId = `app-${nodeCounter}`;
            const appKnowledgeNode: Node<NodeData> = {
              id: nodeId,
              type: 'flowNode',
              position: calculateOptimalPosition(
                'preprocessing',
                'knowledge',
                knowledgeIndex,
                counts.knowledge
              ),
              data: {
                id: nodeId,
                type: `app-${normalizedAppName}`,
                label: normalizeDisplayName(displayName),
                description: `Access ${displayName} knowledge and context`,
                icon: getAppKnowledgeIcon(displayName),
                config: {
                  connectorInstanceId: connectorId, // Connector instance ID
                  appName: displayName,
                  appDisplayName: displayName,
                  connectorType: displayName,
                  filters: filtersParsed, // Contains recordGroups (within connector) and records
                  selectedRecordGroups: filtersParsed.recordGroups || [], // Record groups within this connector
                  selectedRecords: filtersParsed.records || [], // Records within this connector
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
      

      // 3. Create LLM nodes with intelligent positioning
      // agent.models is now an array of enriched model objects from backend
      // Each object has: modelKey, modelName, provider, isReasoning, etc.
      const llmNodes: Node<NodeData>[] = [];
      
      // Use models from agent (now enriched by backend)
      const agentModels = agent.models || [];
      
      if (agentModels.length > 0) {
        agentModels.forEach((modelItem: any, index: number) => {
          // Handle both string keys and object formats
          const isStringKey = typeof modelItem === 'string';
          const modelKey = isStringKey ? modelItem : (modelItem.modelKey || modelItem);
          
          // Find matching model from available models
          const matchingModel = models.find(
            (m) => m.modelKey === modelKey || 
                   m.modelName === (isStringKey ? modelKey : modelItem.modelName) ||
                   m.provider === (isStringKey ? '' : modelItem.provider)
          );

          // Use matching model data or fallback to modelItem data
          const modelName = matchingModel?.modelName || (isStringKey ? modelKey : modelItem.modelName) || 'AI Model';
          const provider = matchingModel?.provider || (isStringKey ? 'AI' : modelItem.provider) || 'AI';
          const isReasoning = matchingModel?.isReasoning || (isStringKey ? false : modelItem.isReasoning) || false;

          nodeCounter += 1;
          const nodeId = `llm-${nodeCounter}`;
          const llmNode: Node<NodeData> = {
            id: nodeId,
            type: 'flowNode',
            position: calculateOptimalPosition('processing', 'llm', index, counts.llm),
            data: {
              id: nodeId,
              type: `llm-${matchingModel?.modelKey || modelKey?.replace(/[^a-zA-Z0-9]/g, '-') || 'default'}`,
              label: modelName.trim() || 'AI Model',
              description: `${formattedProvider(provider)} language model`,
              icon: brainIcon,
              config: {
                modelKey: matchingModel?.modelKey || modelKey,
                modelName,
                provider,
                modelType: matchingModel?.modelType || 'llm',
                isMultimodal: matchingModel?.isMultimodal || false,
                isDefault: matchingModel?.isDefault || false,
                isReasoning,
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
        nodeCounter += 1;
        const nodeId = `llm-${nodeCounter}`;
        const llmNode: Node<NodeData> = {
          id: nodeId,
          type: 'flowNode',
          position: calculateOptimalPosition('processing', 'llm', 0, 1),
          data: {
            id: nodeId,
            type: `llm-${defaultModel.modelKey || 'default'}`,
            label:
              defaultModel.modelName
                .trim() || 'AI Model',
            description: `${formattedProvider(defaultModel.provider || 'AI')} language model`,
            icon: brainIcon,
            config: {
              modelKey: defaultModel.modelKey,
              modelName: defaultModel.modelName,
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

      // 4. Create Toolset nodes from agent.toolsets (new format with nested tools)
      // Falls back to legacy agent.tools if toolsets not present
      const toolsetNodes: Node<NodeData>[] = [];
      let toolsetIndex = 0;
      
      // Check if we have the new toolsets format
      const hasToolsets = agent.toolsets && agent.toolsets.length > 0;
      // Legacy fallback: agent.tools is array of strings like "googledrive.get_files_list"
      const hasLegacyTools = !hasToolsets && agent.tools && agent.tools.length > 0;
      
      if (hasToolsets && agent.toolsets) {
        // New format: toolsets with nested tools - create toolset nodes
        agent.toolsets.forEach((toolset: any) => {
          const toolsetName = toolset.name || '';
          const toolsetDisplayName = toolset.displayName || toolset.name || 'Toolset';
          const toolsetType = toolset.type || toolsetName;
          const normalizedToolsetName = toolsetName.toLowerCase().replace(/[^a-zA-Z0-9]/g, '-');
          const iconPath = `/assets/icons/connectors/${normalizedToolsetName}.svg`;
          
          // Get the tools from this toolset
          const toolsetTools = toolset.tools || [];
          
          // Normalize tools to match ToolsetNode expected format
          // ToolsetNode expects: { name, fullName, description, toolsetName }
          const normalizeTool = (tool: any) => {
            const toolName = tool.name || tool.fullName?.split('.').pop() || 'Tool';
            const toolFullName = tool.fullName || `${toolsetName}.${toolName}`;
            
            // Try to find matching tool in available tools for additional metadata
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
          
          // Get selected tools - use all tools if selectedTools is null/empty
          const selectedToolNames = toolset.selectedTools && toolset.selectedTools.length > 0
            ? toolset.selectedTools
            : normalizedTools.map((t: any) => t.name);
          
          // Create toolset node (not individual tool nodes)
          nodeCounter += 1;
          const nodeId = `toolset-${toolsetName}-${nodeCounter}`;
          const toolsetNode: Node<NodeData> = {
            id: nodeId,
            type: 'flowNode',
            position: calculateOptimalPosition('processing', 'tools', toolsetIndex, counts.toolsets),
            data: {
              id: nodeId,
              type: `toolset-${toolsetName}`,
              label: normalizeDisplayName(toolsetDisplayName),
              description: `${toolsetDisplayName} with ${normalizedTools.length} tools`,
              icon: iconPath,
              category: 'toolset',
              config: {
                toolsetName,
                displayName: toolsetDisplayName,
                iconPath,
                category: toolsetType,
                tools: normalizedTools,
                availableTools: normalizedTools, // All tools are available
                selectedTools: selectedToolNames,
                isConfigured: true,
                isAuthenticated: true, // Assume authenticated if toolset is in agent config
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
      } else if (hasLegacyTools && agent.tools) {
        // Legacy format: agent.tools is array of strings like "googledrive.get_files_list"
        // Group tools by app name to create toolset nodes
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
        
        // Create a toolset node for each app
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
            position: calculateOptimalPosition('processing', 'tools', toolsetIndex, toolsByApp.size),
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

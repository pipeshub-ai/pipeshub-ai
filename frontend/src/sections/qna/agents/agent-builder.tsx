// src/sections/qna/agents/components/flow-agent-builder.tsx
import React, { useCallback, useEffect, useState } from 'react';
import { useNodesState, useEdgesState, addEdge, Connection, Node, Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Box, useTheme, alpha } from '@mui/material';

// Icons
import brainIcon from '@iconify-icons/mdi/brain';
import chatIcon from '@iconify-icons/mdi/chat';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import replyIcon from '@iconify-icons/mdi/reply';

import { useAccountType } from 'src/hooks/use-account-type';

import type { AgentFormData, AgentTemplate } from 'src/types/agent';
import type { AgentBuilderProps, NodeData } from './types/agent';
// Custom hooks
import { useAgentBuilderData } from './hooks/agent-builder/useAgentBuilderData';
import { useAgentBuilderState } from './hooks/agent-builder/useAgentBuilderState';
import { useAgentBuilderNodeTemplates } from './hooks/agent-builder/useNodeTemplates';
import { useAgentBuilderReconstruction } from './hooks/agent-builder/useFlowReconstruction';

// Components
import AgentBuilderHeader from './components/agent-builder/header';
import AgentBuilderCanvasWrapper from './components/agent-builder/canvas-wrapper';
import AgentBuilderNotificationPanel from './components/agent-builder/notification-panel';
import AgentBuilderDialogManager from './components/agent-builder/dialog-manager';
import TemplateSelector from './components/template-selector';

// Utils and types
import { extractAgentConfigFromFlow, normalizeDisplayName, formattedProvider } from './utils/agent';
import AgentApiService from './services/api';

const AgentBuilder: React.FC<AgentBuilderProps> = ({ editingAgent, onSuccess, onClose }) => {
  const theme = useTheme();
  const SIDEBAR_WIDTH = 280;

  // Data loading hook - ALL data fetched once
  const {
    availableTools,
    availableModels,
    availableKnowledgeBases,
    activeAgentConnectors,
    configuredConnectors,
    connectorRegistry,
    toolsets,
    loading,
    loadedAgent,
    error,
    setError,
  } = useAgentBuilderData(editingAgent);

  const {isBusiness} = useAccountType();

  // State management hook
  const {
    selectedNode,
    setSelectedNode,
    configDialogOpen,
    setConfigDialogOpen,
    deleteDialogOpen,
    setDeleteDialogOpen,
    nodeToDelete,
    setNodeToDelete,
    edgeDeleteDialogOpen,
    setEdgeDeleteDialogOpen,
    edgeToDelete,
    setEdgeToDelete,
    sidebarOpen,
    setSidebarOpen,
    agentName,
    setAgentName,
    saving,
    setSaving,
    deleting,
    setDeleting,
    success,
    setSuccess,
  } = useAgentBuilderState(editingAgent);

  // Template dialog state
  const [templateDialogOpen, setTemplateDialogOpen] = useState(false);
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);

  // Share with org state - initialized from loaded agent data
  const [shareWithOrg, setShareWithOrg] = useState<boolean>(false);

  // Sync shareWithOrg from loaded agent
  useEffect(() => {
    if (loadedAgent) {
      setShareWithOrg(loadedAgent.shareWithOrg ?? false);
    }
  }, [loadedAgent]);

  // Node templates hook - receives data instead of fetching
  const { nodeTemplates } = useAgentBuilderNodeTemplates(
    availableTools,
    availableModels,
    availableKnowledgeBases,
    activeAgentConnectors,
    configuredConnectors
  );

  // Flow reconstruction hook
  const { reconstructFlowFromAgent } = useAgentBuilderReconstruction();

  // ReactFlow state - Explicitly typed
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // Update agent name when agent data changes (prioritize loaded agent from API)
  useEffect(() => {
    if (loadedAgent?.name) {
      // Use the loaded agent data from API (most accurate)
      setAgentName(loadedAgent.name);
    } else if (editingAgent && 'name' in editingAgent && !loading) {
      // Fallback to editing agent data if no loaded agent yet
      setAgentName(editingAgent.name);
    } else if (!editingAgent && !loading) {
      // Clear name for new agents
      setAgentName('');
    }
  }, [loadedAgent, editingAgent, loading, setAgentName]);

  // Templates disabled for v1
  // Load templates
  // useEffect(() => {
  //   const loadTemplates = async () => {
  //     try {
  //       setTemplatesLoading(true);
  //       const loadedTemplates = await AgentApiService.getTemplates();
  //       setTemplates(loadedTemplates);
  //     } catch (err) {
  //       console.error('Failed to load templates:', err);
  //     } finally {
  //       setTemplatesLoading(false);
  //     }
  //   };

  //   loadTemplates();
  // }, []);

  // Reset nodes when switching between different agents
  useEffect(() => {
    if (editingAgent && !loading) {
      setNodes([]);
      setEdges([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingAgent?._key, loading, setNodes, setEdges]);

  // Create initial flow when resources are loaded
  useEffect(() => {
    if (!loading && availableModels.length > 0 && nodes.length === 0) {
      const agentToUse = loadedAgent || editingAgent;

      // If editing an existing agent, load its flow configuration
      if (agentToUse && 'flow' in agentToUse && agentToUse.flow?.nodes && agentToUse.flow?.edges) {
        setNodes(agentToUse.flow.nodes as any);
        setEdges(agentToUse.flow.edges as any);
        return;
      }

      // If editing an agent without flow data, reconstruct from agent config
      if (agentToUse) {
        const reconstructedFlow = reconstructFlowFromAgent(
          agentToUse,
          availableModels,
          availableTools,
          availableKnowledgeBases
        );
        setNodes(reconstructedFlow.nodes);
        setEdges(reconstructedFlow.edges);
        return;
      }

      let initalModel = availableModels.find((model) => model.isReasoning);

      if (!initalModel) {
        initalModel = availableModels[0];
      }

      // Create default flow for new agents
      const initialNodes = [
        {
          id: 'chat-input-1',
          type: 'flowNode',
          position: { x: 50, y: 650 },
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
        },
        {
          id: 'llm-1',
          type: 'flowNode',
          position: { x: 50, y: 250 },
          data: {
            id: 'llm-1',
            type: `llm-${(initalModel?.modelKey || `${initalModel?.provider || 'azureOpenAI'}-${initalModel?.modelName || 'default'}`).replace(/[^a-zA-Z0-9]/g, '-').toLowerCase()}`,
            label:
              initalModel?.modelName
                .trim() || 'AI Model',
            description: `${formattedProvider(initalModel?.provider || 'AI')} language model for text generation`,
            icon: brainIcon,
            config: {
              modelKey: initalModel?.modelKey,
              modelName: initalModel?.modelName,
              provider: initalModel?.provider || 'azureOpenAI',
              modelType: initalModel?.modelType || 'llm',
              isMultimodal: initalModel?.isMultimodal || false,
              isDefault: initalModel?.isDefault || false,
              isReasoning: initalModel?.isReasoning || false,
              modelFriendlyName: initalModel?.modelFriendlyName,
            },
            inputs: [],
            outputs: ['response'],
            isConfigured: true,
          },
        },
        {
          id: 'agent-core-1',
          type: 'flowNode',
          position: { x: 550, y: 150 },
          data: {
            id: 'agent-core-1',
            type: 'agent-core',
            label: normalizeDisplayName('Agent'),
            description: 'Central orchestrator receiving inputs and producing responses',
            icon: sparklesIcon,
            config: {
              systemPrompt:
                (loadedAgent as any)?.systemPrompt ||
                (editingAgent as any)?.systemPrompt ||
                'You are a helpful assistant.',
              startMessage:
                (loadedAgent as any)?.startMessage ||
                (editingAgent as any)?.startMessage ||
                'Hello! I am ready to assist you. How can I help you today?',
              routing: 'auto',
              allowMultipleLLMs: true,
            },
            inputs: ['input', 'actions', 'knowledge', 'llms'],
            outputs: ['response'],
            isConfigured: true,
          },
        },
        {
          id: 'chat-response-1',
          type: 'flowNode',
          position: { x: 1100, y: 450 },
          data: {
            id: 'chat-response-1',
            type: 'chat-response',
            label: normalizeDisplayName('Chat Output'),
            description: 'Delivers responses to users in the chat interface',
            icon: replyIcon,
            config: { format: 'text' },
            inputs: ['response'],
            outputs: [],
            isConfigured: true,
          },
        },
      ];

      const initialEdges = [
        {
          id: 'e-input-agent',
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
        },
        {
          id: 'e-llm-agent',
          source: 'llm-1',
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
        },
        {
          id: 'e-agent-output',
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
        },
      ];

      setNodes(initialNodes);
      setEdges(initialEdges);
    }
  }, [
    loading,
    availableModels,
    availableTools,
    availableKnowledgeBases,
    nodes.length,
    setNodes,
    setEdges,
    theme,
    loadedAgent,
    editingAgent,
    reconstructFlowFromAgent,
  ]);

  // Handle connections
  const onConnect = useCallback(
    (connection: Connection) => {
      // Get source and target nodes
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);

      // Validate connection rules (NEW FLOW)
      if (sourceNode && targetNode) {
        const sourceType = sourceNode.data.type;
        const targetType = targetNode.data.type;

        // ============================================
        // VALIDATION: Only allow connections to/from agent-core
        // ============================================
        
        // Knowledge nodes (KB and app) must connect to agent's knowledge handle
        if ((sourceType.startsWith('kb-') && sourceType !== 'kb-group') || 
            (sourceType.startsWith('app-') && sourceType !== 'app-group')) {
          if (targetType !== 'agent-core') {
            setError('Knowledge nodes must be connected to the agent\'s knowledge handle');
            return;
          }
          if (connection.targetHandle !== 'knowledge') {
            setError('Knowledge nodes must be connected to the agent\'s knowledge handle');
            return;
          }
        }

        // LLM nodes must connect to agent's llms handle
        if (sourceType.startsWith('llm-')) {
          if (targetType !== 'agent-core') {
            setError('LLM nodes must be connected to the agent\'s llms handle');
            return;
          }
          if (connection.targetHandle !== 'llms') {
            setError('LLM nodes must be connected to the agent\'s llms handle');
            return;
          }
        }

        // Input nodes must connect to agent's input handle
        if (sourceType === 'user-input') {
          if (targetType !== 'agent-core') {
            setError('Input nodes must be connected to the agent\'s input handle');
            return;
          }
          if (connection.targetHandle !== 'input') {
            setError('Input nodes must be connected to the agent\'s input handle');
            return;
          }
        }

        // Tool-groups can now connect directly to agent's toolsets handle
        if (sourceType.startsWith('tool-group-') && targetType === 'agent-core') {
          if (connection.targetHandle !== 'toolsets') {
            setError('Tool groups must be connected to the agent\'s toolsets handle');
            return;
          }
        }

        // Toolset nodes must connect to agent's toolsets handle
        if (sourceType.startsWith('toolset-') && targetType === 'agent-core') {
          if (connection.targetHandle !== 'toolsets') {
            setError('Toolsets must be connected to the agent\'s toolsets handle');
            return;
          }
        }

        // Individual tools can also connect directly to agent's toolsets handle
        if (sourceType.startsWith('tool-') && !sourceType.startsWith('tool-group-') && targetType === 'agent-core') {
          if (connection.targetHandle !== 'toolsets') {
            setError('Tools must be connected to the agent\'s toolsets handle');
            return;
          }
          
          // Validate that the tool has a connector instance associated
          if (!sourceNode.data.config?.connectorInstanceId && !sourceNode.data.config?.connectorType && !sourceNode.data.config?.scope) {
            setError('This tool needs to be configured with a connector instance first');
            return;
          }
        }

        // Agent can only connect to output nodes
        if (sourceType === 'agent-core') {
          if (targetType !== 'chat-response') {
            setError('Agent can only connect to output nodes');
            return;
          }
          if (connection.sourceHandle !== 'response') {
            setError('Agent must connect from its response handle');
            return;
          }
        }

        // Prevent invalid connections between non-agent nodes
        if (sourceType !== 'agent-core' && targetType !== 'agent-core' && targetType !== 'chat-response') {
          setError('Nodes can only connect to the agent or output nodes');
          return;
        }

        // Tool-groups should only connect to agent
        if (sourceType.startsWith('tool-group-') && targetType !== 'agent-core') {
          setError('Tool groups must be connected to the agent');
          return;
        }

        // Individual tools should only connect to agent
        if (sourceType.startsWith('tool-') && !sourceType.startsWith('tool-group-') && targetType !== 'agent-core') {
          setError('Tools must be connected to the agent');
          return;
        }
      }

      const newEdge = {
        id: `e-${connection.source}-${connection.target}-${Date.now()}`,
        ...connection,
        style: {
          stroke: alpha(theme.palette.primary.main, 0.6),
          strokeWidth: 2,
        },
        type: 'smoothstep',
        animated: false,
      };
      setEdges((eds) => addEdge(newEdge as any, eds));
    },
    [setEdges, theme, nodes, setError]
  );

  // Handle edge selection and deletion (one-click delete)
  const onEdgeClick = useCallback(
    (event: React.MouseEvent, edge: any) => {
      event.preventDefault();
      event.stopPropagation();
      // Delete edge immediately without dialog
      setEdges((eds) => eds.filter((e) => e.id !== edge.id));
    },
    [setEdges]
  );

  // Delete edge
  const deleteEdge = useCallback(
    async (edge: any) => {
      try {
        setDeleting(true);
        setEdges((eds) => eds.filter((e) => e.id !== edge.id));
        setEdgeDeleteDialogOpen(false);
        setEdgeToDelete(null);
      } finally {
        setDeleting(false);
      }
    },
    [setEdges, setEdgeDeleteDialogOpen, setEdgeToDelete, setDeleting]
  );

  // Handle node selection
  const onNodeClick = useCallback(
    (event: React.MouseEvent, node: any) => {
      event.stopPropagation();
      event.preventDefault();

      // Don't open config dialog for toolset nodes
      if (node.data.type.startsWith('toolset-') || node.data.category === 'toolset') {
        return; // Toolset nodes don't need config dialog
      }

      if (node.data.type !== 'agent-core' && !configDialogOpen && !selectedNode) {
        setSelectedNode(node);
        setTimeout(() => {
          setConfigDialogOpen(true);
        }, 10);
      }
    },
    [configDialogOpen, selectedNode, setSelectedNode, setConfigDialogOpen]
  );

  // Handle node configuration
  const handleNodeConfig = useCallback(
    (nodeId: string, config: Record<string, any>) => {
      setNodes((nds) =>
        nds.map((node) =>
          node.id === nodeId
            ? {
                ...node,
                data: {
                  ...node.data,
                  config,
                  isConfigured: true,
                },
              }
            : node
        )
      );
    },
    [setNodes]
  );

  // Delete node
  const deleteNode = useCallback(
    async (nodeId: string) => {
      try {
        setDeleting(true);
        setNodes((nds) => nds.filter((node) => node.id !== nodeId));
        setEdges((eds) => eds.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
        setDeleteDialogOpen(false);
        setNodeToDelete(null);
        setConfigDialogOpen(false);
        setSelectedNode(null);
      } finally {
        setDeleting(false);
      }
    },
    [
      setNodes,
      setEdges,
      setDeleteDialogOpen,
      setNodeToDelete,
      setConfigDialogOpen,
      setSelectedNode,
      setDeleting,
    ]
  );

  // Handle delete confirmation
  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodeToDelete(nodeId);
      setDeleteDialogOpen(true);
    },
    [setNodeToDelete, setDeleteDialogOpen]
  );

  // Drag and drop functionality
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((event: React.DragEvent) => {
    // This will be handled by the FlowBuilderCanvas component
  }, []);

  // Compute whether the current flow has any toolsets connected to the agent
  const hasToolsets = nodes.some((node) => node.data?.type?.startsWith('toolset-'));

  // Save agent
  const handleSave = useCallback(async () => {
    try {
      setSaving(true);
      setError(null);

      // Guard: org-shared agents cannot have toolsets
      if (shareWithOrg && hasToolsets) {
        setError(
          'Cannot share with organization: this agent has toolsets. ' +
          'Remove all toolsets before enabling organization sharing.'
        );
        setSaving(false);
        return;
      }

      const currentAgent = loadedAgent || editingAgent;
      // extractAgentConfigFromFlow now returns properly typed ToolsetReference[] and KnowledgeReference[]
      const agentConfig: AgentFormData = extractAgentConfigFromFlow(
        agentName,
        nodes,
        edges,
        currentAgent,
        shareWithOrg
      );

      const agent = currentAgent
        ? await AgentApiService.updateAgent(currentAgent._key, agentConfig)
        : await AgentApiService.createAgent(agentConfig);

      setSuccess(currentAgent ? 'Agent updated successfully!' : 'Agent created successfully!');
      setTimeout(() => {
        onSuccess(agent);
      }, 1000);
    } catch (err: any) {
      const message = err?.response?.data?.detail || (editingAgent ? 'Failed to update agent' : 'Failed to create agent');
      setError(message);
      console.error('Error saving agent:', err);
    } finally {
      setSaving(false);
    }
  }, [
    agentName,
    nodes,
    edges,
    loadedAgent,
    editingAgent,
    shareWithOrg,
    hasToolsets,
    onSuccess,
    setSaving,
    setError,
    setSuccess,
  ]);

  return (
    <Box sx={{ height: '90vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Header */}
      <AgentBuilderHeader
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
        agentName={agentName}
        setAgentName={setAgentName}
        saving={saving}
        onSave={handleSave}
        onClose={onClose}
        editingAgent={editingAgent}
        originalAgentName={loadedAgent?.name}
        templateDialogOpen={templateDialogOpen}
        setTemplateDialogOpen={setTemplateDialogOpen}
        templatesLoading={templatesLoading}
        agentId={editingAgent?._key || ''}
        shareWithOrg={shareWithOrg}
        setShareWithOrg={setShareWithOrg}
        hasToolsets={hasToolsets}
      />

      {/* Main Content */}
      <AgentBuilderCanvasWrapper
        sidebarOpen={sidebarOpen}
        sidebarWidth={SIDEBAR_WIDTH}
        nodeTemplates={nodeTemplates}
        loading={loading}
        activeAgentConnectors={activeAgentConnectors}
        configuredConnectors={configuredConnectors}
        connectorRegistry={connectorRegistry}
        toolsets={toolsets}
        isBusiness={isBusiness}
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onDrop={onDrop}
        onDragOver={onDragOver}
        setNodes={setNodes}
        onNodeEdit={(nodeId: string, data: any) => {
          if (data.type === 'agent-core') {
            console.log('Edit agent node:', nodeId, data);
          } else {
            const node = nodes.find((n) => n.id === nodeId);
            if (node) {
              setSelectedNode(node);
              setConfigDialogOpen(true);
            }
          }
        }}
        onNodeDelete={(nodeId: string) => {
          setNodeToDelete(nodeId);
          setDeleteDialogOpen(true);
        }}
        onError={(errorMsg) => setError(errorMsg)}
      />

      {/* Notifications */}
      <AgentBuilderNotificationPanel
        error={error}
        success={success}
        onErrorClose={() => setError(null)}
        onSuccessClose={() => setSuccess(null)}
      />

      {/* Dialogs */}
      <AgentBuilderDialogManager
        selectedNode={selectedNode}
        configDialogOpen={configDialogOpen}
        onConfigDialogClose={() => {
          setConfigDialogOpen(false);
          setSelectedNode(null);
        }}
        onNodeConfig={handleNodeConfig}
        onDeleteNode={handleDeleteNode}
        deleteDialogOpen={deleteDialogOpen}
        onDeleteDialogClose={() => setDeleteDialogOpen(false)}
        nodeToDelete={nodeToDelete}
        onConfirmDelete={() => (nodeToDelete ? deleteNode(nodeToDelete) : Promise.resolve())}
        edgeDeleteDialogOpen={edgeDeleteDialogOpen}
        onEdgeDeleteDialogClose={() => {
          setEdgeDeleteDialogOpen(false);
          setEdgeToDelete(null);
        }}
        edgeToDelete={edgeToDelete}
        onConfirmEdgeDelete={() => (edgeToDelete ? deleteEdge(edgeToDelete) : Promise.resolve())}
        deleting={deleting}
        nodes={nodes}
      />

      {/* Template Selector Dialog - Disabled for v1 */}
      {/* <TemplateSelector
        open={templateDialogOpen}
        onClose={() => setTemplateDialogOpen(false)}
        onSelect={(template) => {
          // Apply template to the agent node
          const agentNode = nodes.find((node) => node.data.type === 'agent-core');
          if (agentNode) {
            handleNodeConfig(agentNode.id, {
              ...agentNode.data.config,
              systemPrompt: template.systemPrompt,
              startMessage: template.startMessage,
              description: template.description || 'AI agent for task automation and assistance',
              templateId: template._key,
            });
          }
          setTemplateDialogOpen(false);
        }}
        templates={templates}
      /> */}
    </Box>
  );
};

export default AgentBuilder;

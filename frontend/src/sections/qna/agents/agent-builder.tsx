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

  // Data loading hook
  const {
    availableTools,
    availableModels,
    availableKnowledgeBases,
    loading,
    loadedAgent,
    error,
    setError,
  } = useAgentBuilderData(editingAgent);

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

  // Node templates hook
  const { nodeTemplates } = useAgentBuilderNodeTemplates(
    availableTools,
    availableModels,
    availableKnowledgeBases
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

  // Load templates
  useEffect(() => {
    const loadTemplates = async () => {
      try {
        setTemplatesLoading(true);
        const loadedTemplates = await AgentApiService.getTemplates();
        setTemplates(loadedTemplates);
      } catch (err) {
        console.error('Failed to load templates:', err);
      } finally {
        setTemplatesLoading(false);
      }
    };

    loadTemplates();
  }, []);

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
            type: `llm-${initalModel?.provider || 'azureOpenAI'}-${initalModel?.modelName || 'default'}`.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase(),
            label:
              initalModel?.modelName
                ?.replace(/[^a-zA-Z0-9]/g, ' ')
                .replace(/\s+/g, ' ')
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
            },
            inputs: ['prompt', 'context'],
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
    [setEdges, theme]
  );

  // Handle edge selection and deletion
  const onEdgeClick = useCallback(
    (event: React.MouseEvent, edge: any) => {
      event.preventDefault();
      setEdgeToDelete(edge);
      setEdgeDeleteDialogOpen(true);
    },
    [setEdgeToDelete, setEdgeDeleteDialogOpen]
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

  // Save agent
  const handleSave = useCallback(async () => {
    try {
      setSaving(true);
      setError(null);

      const currentAgent = loadedAgent || editingAgent;
      const agentConfig: AgentFormData = extractAgentConfigFromFlow(
        agentName,
        nodes,
        edges,
        currentAgent
      );

      const agent = currentAgent
        ? await AgentApiService.updateAgent(currentAgent._key, agentConfig)
        : await AgentApiService.createAgent(agentConfig);

      setSuccess(currentAgent ? 'Agent updated successfully!' : 'Agent created successfully!');
      setTimeout(() => {
        onSuccess(agent);
      }, 1000);
    } catch (err) {
      setError(editingAgent ? 'Failed to update agent' : 'Failed to create agent');
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
      />

      {/* Main Content */}
      <AgentBuilderCanvasWrapper
        sidebarOpen={sidebarOpen}
        sidebarWidth={SIDEBAR_WIDTH}
        nodeTemplates={nodeTemplates}
        loading={loading}
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

      {/* Template Selector Dialog */}
      <TemplateSelector
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
      />
    </Box>
  );
};

export default AgentBuilder;

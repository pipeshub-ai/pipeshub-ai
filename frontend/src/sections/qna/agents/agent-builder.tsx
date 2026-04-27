// src/sections/qna/agents/components/flow-agent-builder.tsx
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useNodesState, useEdgesState, addEdge, Connection, Node, Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Box,
  useTheme,
  alpha,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Stack,
  IconButton,
  CircularProgress,
  Alert,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
} from '@mui/material';
import robotIcon from '@iconify-icons/mdi/robot';
import checkCircleIcon from '@iconify-icons/mdi/check-circle-outline';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import closeIcon from '@iconify-icons/mdi/close';
import { Icon } from '@iconify/react';

// Icons
import brainIcon from '@iconify-icons/mdi/brain';
import chatIcon from '@iconify-icons/mdi/chat';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import replyIcon from '@iconify-icons/mdi/reply';

import { useAccountType } from 'src/hooks/use-account-type';
import { paths } from 'src/routes/paths';

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
import AgentToolsetConfigDialog from './components/agent-builder/agent-toolset-config-dialog';

// ---------------------------------------------------------------------------
// ServiceAccountConfirmDialog
// ---------------------------------------------------------------------------
interface ServiceAccountConfirmDialogProps {
  open: boolean;
  agentName: string;
  creating: boolean;
  error: string | null;
  /** True when the user already has toolset nodes dropped on the canvas. */
  hasUserToolsets: boolean;
  /** True when converting an existing (already-saved) agent instead of creating a new one. */
  isConverting?: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}

const ServiceAccountConfirmDialog: React.FC<ServiceAccountConfirmDialogProps> = ({
  open,
  agentName,
  creating,
  error,
  hasUserToolsets,
  isConverting = false,
  onClose,
  onConfirm,
}) => {
  const theme = useTheme();

  const features = [
    { text: 'Dedicated credentials per toolset (not per-user)', positive: true },
    { text: 'Cannot be reverted back to a regular agent', positive: false },
  ];

  const title = isConverting ? 'Convert to Service Account' : 'Enable Service Account';
  const description = isConverting
    ? 'This agent will be permanently converted to a Service Account — an independent identity with agent-level toolset credentials, shared with your whole organization.'
    : 'This agent will be created as a Service Account — an independent identity with agent-level toolset credentials, shared with your whole organization.';
  const confirmLabel = isConverting ? 'Convert to Service Account' : 'Create Service Account Agent';
  const busyLabel = isConverting ? 'Converting…' : 'Creating Agent…';
  const nextStepsNote = isConverting
    ? 'The agent will be saved and you can immediately configure agent-level toolset credentials. Existing per-user toolset credentials are not transferred.'
    : 'The agent will be saved now so you can configure its toolset credentials. You can then add knowledge, configure the model, and update other settings before finishing.';

  return (
    <Dialog
      open={open}
      onClose={creating ? undefined : onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2.5,
          boxShadow: '0 24px 48px rgba(0,0,0,0.15)',
        },
      }}
    >
      <DialogTitle sx={{ pb: 1, pr: 7 }}>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: `${theme.palette.secondary.main}18`,
              flexShrink: 0,
            }}
          >
            <Icon icon={robotIcon} width={22} height={22} color={theme.palette.secondary.main} />
          </Box>
          <Box>
            <Typography variant="h6" fontWeight={700} lineHeight={1.2}>
              {title}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Agent: <strong>{agentName || '(unnamed)'}</strong>
            </Typography>
          </Box>
        </Stack>

        {!creating && (
          <IconButton
            onClick={onClose}
            size="small"
            sx={{ position: 'absolute', right: 12, top: 12, color: 'text.secondary' }}
          >
            <Icon icon={closeIcon} width={20} height={20} />
          </IconButton>
        )}
      </DialogTitle>

      <DialogContent sx={{ pt: 0 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {description} Here&apos;s what that means:
        </Typography>

        <Alert
          severity="error"
          variant="outlined"
          icon={<Icon icon={alertCircleIcon} width={22} height={22} />}
          sx={{
            mb: 1.5,
            color: 'error.dark',
            borderColor: 'error.main',
            bgcolor: (t) => alpha(t.palette.error.main, 0.08),
            '& .MuiAlert-message': { width: '100%' },
          }}
        >
          <Typography variant="body2" sx={{ fontWeight: 700, color: 'error.main', mb: 0.5 }}>
            Your permissions apply to this agent&apos;s attached knowledge only
          </Typography>
          <Box
            component="ul"
            sx={{
              m: 0,
              pl: 2.25,
              listStyleType: 'disc',
              color: 'error.dark',
              typography: 'body2',
              '& > li': { mb: 1, pl: 0.5, display: 'list-item', '&:last-child': { mb: 0 } },
            }}
          >
            <Box component="li">
              Retrieval is limited to the knowledge sources <strong>you configure on this agent</strong>{' '}
              (connectors and collections attached in the builder) — not everything in the platform.
            </Box>
            <Box component="li">
              Within those attached sources only, search uses <strong>your</strong> access as the creator.
            </Box>
            <Box component="li">
              Anyone who uses this agent may see answers from content <strong>you</strong> are allowed to
              see there, even when their own permissions would not include it.
            </Box>
          </Box>
        </Alert>

        <Alert
          severity="error"
          variant="outlined"
          icon={<Icon icon={alertCircleIcon} width={22} height={22} />}
          sx={{
            mb: 2,
            color: 'error.dark',
            borderColor: 'error.main',
            bgcolor: (t) => alpha(t.palette.error.main, 0.08),
            '& .MuiAlert-message': { width: '100%' },
          }}
        >
          <Typography variant="body2" sx={{ fontWeight: 700, color: 'error.main', mb: 0.5 }}>
            This service account is shared with everyone in your organization
          </Typography>
          <Typography variant="body2" sx={{ color: 'error.dark' }}>
            The agent is available org-wide. All members of your organization can use this service
            account agent once it is enabled.
          </Typography>
        </Alert>

        <List dense disablePadding>
          {features.map((f) => (
            <ListItem key={f.text} disableGutters sx={{ py: 0.5, alignItems: 'flex-start' }}>
              <ListItemIcon sx={{ minWidth: 28, mt: 0.25 }}>
                <Icon
                  icon={f.positive ? checkCircleIcon : alertCircleIcon}
                  width={18}
                  height={18}
                  color={f.positive ? theme.palette.success.main : theme.palette.warning.main}
                />
              </ListItemIcon>
              <ListItemText
                primary={f.text}
                primaryTypographyProps={{ variant: 'body2', color: 'text.primary' }}
              />
            </ListItem>
          ))}
        </List>

        {hasUserToolsets && (
          <Alert severity="error" sx={{ mt: 2, mb: 1 }}>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              Please remove toolset nodes from the canvas first.
            </Typography>
            <Typography variant="body2">
              Toolsets on the canvas use <em>per-user</em> credentials which are incompatible with
              Service Account mode. Remove the toolset nodes, then{' '}
              {isConverting ? 'convert' : 'create'} the agent. You can drag toolsets back
              afterwards and configure <em>agent-specific</em> credentials for each one.
            </Typography>
          </Alert>
        )}

        <Box
          sx={{
            mt: 2,
            p: 1.5,
            borderRadius: 1.5,
            backgroundColor: `${theme.palette.info.main}10`,
            border: `1px solid ${theme.palette.info.main}30`,
          }}
        >
          <Typography variant="caption" color="text.secondary">
            <strong>Next steps:</strong> {nextStepsNote}
          </Typography>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button onClick={onClose} disabled={creating} variant="outlined" color="inherit">
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          disabled={creating || hasUserToolsets}
          variant="outlined"
          color="secondary"
          startIcon={
            creating ? (
              <CircularProgress size={14} color="inherit" />
            ) : (
              <Icon icon={robotIcon} width={16} height={16} />
            )
          }
          sx={{ minWidth: 210 }}
        >
          {creating ? busyLabel : confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
// ---------------------------------------------------------------------------

const AgentBuilder: React.FC<AgentBuilderProps> = ({ editingAgent, onSuccess, onClose }) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
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
    refreshToolsets, // Function to refresh toolsets after OAuth / credential change
    refreshAgent,    // Re-fetch agent + toolsets in-place (used after conversion)
    loadMoreToolsets,
    toolsetsHasMore,
    toolsetsLoadingMore,
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

  // Derive isServiceAccount directly from the fetched agent so there is no
  // extra render cycle where loadedAgent is set but the old useState value is
  // still false. Using a separate useState caused a brief window where the
  // "Manage Credentials" gear button disappeared for already-authenticated
  // service-account toolsets because isServiceAccount was read as false.
  const isServiceAccount = loadedAgent?.isServiceAccount ?? false;

  // Whether the service account confirmation dialog is open
  const [serviceAccountConfirmOpen, setServiceAccountConfirmOpen] = useState(false);
  const [serviceAccountCreating, setServiceAccountCreating] = useState(false);
  const [serviceAccountError, setServiceAccountError] = useState<string | null>(null);

  // Manage credentials dialog for agent-scoped toolsets
  const [agentToolsetDialog, setAgentToolsetDialog] = useState<{
    toolset: any;
    instanceId: string;
  } | null>(null);

  // The effective agent key — simply the editing agent's key.
  // After a service-account quick-save, we navigate to the edit route so this
  // is always correct once the component (re)mounts.
  const effectiveAgentKey = editingAgent?._key;

  // Existing agent can be opened in view-only mode based on permissions.
  const isReadOnly = useMemo(() => {
    const sourceAgent = loadedAgent || editingAgent;
    if (!sourceAgent) return false;
    return sourceAgent.can_edit === false;
  }, [loadedAgent, editingAgent]);

  // When the builder re-mounts after a service-account quick-save (router state
  // carries serviceAccountJustCreated), show a success toast so the user knows
  // the agent was saved and they are now in edit mode.
  useEffect(() => {
    if (location.state?.serviceAccountJustCreated) {
      setSuccess('Service account agent created! You can now configure toolset credentials using the 🔑 icon in the sidebar.');
      // Clear the router state flag so it doesn't re-trigger on next render
      navigate(location.pathname, { replace: true, state: {} });
    }
    // Only run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync shareWithOrg from loaded agent.
  // Service account agents are always org-shared — force the toggle to true
  // so the UI reflects the enforced invariant even if the stored value is stale.
  useEffect(() => {
    if (loadedAgent) {
      setShareWithOrg(loadedAgent.isServiceAccount === true ? true : (loadedAgent.shareWithOrg ?? false));
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
      // Extract agent config values early, before control flow narrowing
      const systemPrompt: string = (loadedAgent?.systemPrompt ?? editingAgent?.systemPrompt) || 'You are a helpful assistant.';
      const instructions: string = loadedAgent?.instructions ?? editingAgent?.instructions ?? '';
      const startMessage: string = (loadedAgent?.startMessage ?? editingAgent?.startMessage) || 'Hello! I am ready to assist you. How can I help you today?';
      
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
              systemPrompt,
              instructions,
              startMessage,
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
      if (isReadOnly) {
        setError('You have view-only access to this agent.');
        return;
      }

      // Get source and target nodes
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);

      // Validate connection rules (NEW FLOW)
      if (sourceNode && targetNode) {
        const sourceType = sourceNode.data.type;
        const targetType = targetNode.data.type;

        // Linear chaining guardrails for agent->agent connections.
        if (sourceType === 'agent-core' && targetType === 'agent-core') {
          if (connection.sourceHandle !== 'response' || connection.targetHandle !== 'input') {
            setError('Agent-to-agent connections must use response → input');
            return;
          }

          const hasOutgoingAgentConnection = edges.some(
            (edge) =>
              edge.source === connection.source &&
              edge.sourceHandle === 'response' &&
              nodes.find((n) => n.id === edge.target)?.data.type === 'agent-core'
          );
          if (hasOutgoingAgentConnection) {
            setError('Each agent can connect to only one downstream agent');
            return;
          }

          const hasIncomingAgentConnection = edges.some(
            (edge) =>
              edge.target === connection.target &&
              edge.targetHandle === 'input' &&
              nodes.find((n) => n.id === edge.source)?.data.type === 'agent-core'
          );
          if (hasIncomingAgentConnection) {
            setError('Each agent can have only one upstream agent');
            return;
          }
        }

        if (sourceType === 'agent-core' && targetType === 'conditional-check') {
          if (connection.sourceHandle !== 'response' || connection.targetHandle !== 'input') {
            setError('Agent-to-condition connections must use response → input');
            return;
          }

          const hasOutgoingConditionConnection = edges.some(
            (edge) =>
              edge.source === connection.source &&
              edge.sourceHandle === 'response' &&
              nodes.find((n) => n.id === edge.target)?.data.type === 'conditional-check'
          );
          if (hasOutgoingConditionConnection) {
            setError('Each agent can connect to only one downstream condition block');
            return;
          }
        }

        if (targetType === 'conditional-check') {
          if (sourceType !== 'agent-core') {
            setError('Condition blocks can only receive input from an agent');
            return;
          }
          if (connection.targetHandle !== 'input' || connection.sourceHandle !== 'response') {
            setError('Condition blocks must be connected via response → input');
            return;
          }

          const hasIncomingConditionConnection = edges.some(
            (edge) => edge.target === connection.target && edge.targetHandle === 'input'
          );
          if (hasIncomingConditionConnection) {
            setError('Each condition block can have only one upstream agent');
            return;
          }
        }

        if (sourceType === 'conditional-check') {
          if (connection.sourceHandle !== 'pass' && connection.sourceHandle !== 'fail') {
            setError('Condition blocks must connect from pass or fail outputs');
            return;
          }

          if (targetType !== 'agent-core' && targetType !== 'chat-response') {
            setError('Condition blocks can only connect to an agent or chat response');
            return;
          }

          if (targetType === 'agent-core' && connection.targetHandle !== 'input') {
            setError('Condition output to an agent must connect to the input handle');
            return;
          }

          if (targetType === 'chat-response' && connection.targetHandle !== 'response') {
            setError('Condition output to chat response must connect to response handle');
            return;
          }

          const hasExistingBranchConnection = edges.some(
            (edge) =>
              edge.source === connection.source &&
              edge.sourceHandle === connection.sourceHandle
          );
          if (hasExistingBranchConnection) {
            setError(`Condition output "${connection.sourceHandle}" can only be connected once`);
            return;
          }
        }

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
          if (connection.sourceHandle !== 'response') {
            setError('Agent must connect from its response handle');
            return;
          }

          if (targetType === 'chat-response') {
            if (connection.targetHandle !== 'response') {
              setError('Agent output must connect to chat output response handle');
              return;
            }
          } else if (targetType !== 'agent-core' && targetType !== 'conditional-check') {
            setError('Agent can only connect to another agent, a condition block, or output node');
            return;
          }
        }

        // Prevent invalid connections between non-agent nodes
        if (
          sourceType !== 'agent-core' &&
          sourceType !== 'conditional-check' &&
          targetType !== 'agent-core' &&
          targetType !== 'chat-response' &&
          targetType !== 'conditional-check'
        ) {
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

        if (sourceType === 'conditional-check' && targetType === 'conditional-check') {
          setError('Condition blocks cannot connect to other condition blocks directly');
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
    [setEdges, theme, nodes, edges, setError, isReadOnly]
  );

  // Handle edge selection and deletion (one-click delete)
  const onEdgeClick = useCallback(
    (event: React.MouseEvent, edge: any) => {
      if (isReadOnly) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      // Delete edge immediately without dialog
      setEdges((eds) => eds.filter((e) => e.id !== edge.id));
    },
    [setEdges, isReadOnly]
  );

  // Delete edge
  const deleteEdge = useCallback(
    async (edge: any) => {
      if (isReadOnly) {
        return;
      }
      try {
        setDeleting(true);
        setEdges((eds) => eds.filter((e) => e.id !== edge.id));
        setEdgeDeleteDialogOpen(false);
        setEdgeToDelete(null);
      } finally {
        setDeleting(false);
      }
    },
    [setEdges, setEdgeDeleteDialogOpen, setEdgeToDelete, setDeleting, isReadOnly]
  );

  // Handle node selection
  const onNodeClick = useCallback(
    (event: React.MouseEvent, node: any) => {
      if (isReadOnly) {
        return;
      }
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
    [configDialogOpen, selectedNode, setSelectedNode, setConfigDialogOpen, isReadOnly]
  );

  // Handle node configuration
  const handleNodeConfig = useCallback(
    (nodeId: string, config: Record<string, any>) => {
      if (isReadOnly) {
        return;
      }
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
    [setNodes, isReadOnly]
  );

  // Delete node
  const deleteNode = useCallback(
    async (nodeId: string) => {
      if (isReadOnly) {
        return;
      }
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
      isReadOnly,
    ]
  );

  // Handle delete confirmation
  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      if (isReadOnly) {
        return;
      }
      setNodeToDelete(nodeId);
      setDeleteDialogOpen(true);
    },
    [setNodeToDelete, setDeleteDialogOpen, isReadOnly]
  );

  // Drag and drop functionality
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((event: React.DragEvent) => {
    // This will be handled by the FlowBuilderCanvas component
  }, []);

  const activeToolsetTypes = useMemo(
    () =>
      Array.from(
        new Set(
          nodes
            .filter((node) => node.data?.type?.startsWith('toolset-'))
            .map((node) => {
              const config = (node.data?.config as Record<string, any>) || {};
              return (
                config.toolsetType ||
                config.toolsetName ||
                (typeof node.data?.type === 'string'
                  ? node.data.type.replace(/^toolset-/, '')
                  : '')
              );
            })
            .filter(Boolean)
            .map((value) => String(value))
        )
      ),
    [nodes]
  );

  // Compute whether the current flow has any toolsets connected to the agent
  const hasToolsets = nodes.some((node) => node.data?.type?.startsWith('toolset-'));

  // Guard to prevent duplicate submissions from rapid clicks
  const saveSubmittingRef = useRef(false);

  // Save agent
  const handleSave = useCallback(async () => {
    if (isReadOnly) {
      setError('You have view-only access to this agent.');
      return;
    }
    // Prevent duplicate submissions from rapid clicks
    if (saveSubmittingRef.current) {
      return;
    }
    saveSubmittingRef.current = true;
    try {
      setSaving(true);
      setError(null);

      // currentAgent: the agent being edited (from URL param) or null for a fresh create
      const currentAgent = loadedAgent || editingAgent;
      // extractAgentConfigFromFlow now returns properly typed ToolsetReference[] and KnowledgeReference[]
      const agentConfig: AgentFormData = extractAgentConfigFromFlow(
        agentName,
        nodes,
        edges,
        currentAgent,
        shareWithOrg,
        isServiceAccount
      );

      const payload: AgentFormData = {
        ...agentConfig,
        flow: { nodes, edges },
        flowSchemaVersion: 2,
        orchestrationMode: nodes.some((node) => node.data?.type === 'conditional-check')
          ? 'conditional'
          : 'linear',
      };

      const agent = currentAgent
        ? await AgentApiService.updateAgent(currentAgent._key, payload)
        : await AgentApiService.createAgent(payload);

      setSuccess(currentAgent ? 'Agent updated successfully!' : 'Agent created successfully!');
      setTimeout(() => {
        onSuccess(agent);
        // Re-enable save only after navigation hook completes
        setSaving(false);
        saveSubmittingRef.current = false;
      }, 1000);
    } catch (err: any) {
      const message = err?.response?.data?.detail || (editingAgent ? 'Failed to update agent' : 'Failed to create agent');
      setError(message);
      console.error('Error saving agent:', err);
      // Allow retry on failure
      setSaving(false);
      saveSubmittingRef.current = false;
    }
  }, [
    agentName,
    nodes,
    edges,
    loadedAgent,
    editingAgent,
    shareWithOrg,
    isServiceAccount,
    onSuccess,
    setSaving,
    setError,
    setSuccess,
    isReadOnly,
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
        agentId={effectiveAgentKey || ''}
        shareWithOrg={shareWithOrg}
        setShareWithOrg={setShareWithOrg}
        hasToolsets={hasToolsets}
        isReadOnly={isReadOnly}
        isServiceAccount={isServiceAccount}
        onEnableServiceAccount={() => setServiceAccountConfirmOpen(true)}
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
        refreshToolsets={(agentKey?: string, svcAccount?: boolean, search?: string) =>
          refreshToolsets(agentKey, svcAccount, search)
        }
        loadMoreToolsets={loadMoreToolsets}
        toolsetsHasMore={toolsetsHasMore}
        toolsetsLoadingMore={toolsetsLoadingMore}
        isBusiness={isBusiness}
        activeToolsetTypes={activeToolsetTypes}
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
          if (isReadOnly) return;
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
          if (isReadOnly) return;
          setNodeToDelete(nodeId);
          setDeleteDialogOpen(true);
        }}
        onError={(errorMsg) => setError(errorMsg)}
        isReadOnly={isReadOnly}
        isServiceAccount={isServiceAccount}
        agentKey={effectiveAgentKey}
        onManageAgentToolsetCredentials={(toolset: any) => {
          if (!effectiveAgentKey) {
            setError('Enable service account mode first before managing toolset credentials.');
            return;
          }
          setAgentToolsetDialog({
            toolset,
            instanceId: toolset.instanceId || '',
          });
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

      {/* Agent Toolset Credentials Dialog (service account agents) */}
      {agentToolsetDialog && effectiveAgentKey && (
        <AgentToolsetConfigDialog
          toolset={agentToolsetDialog.toolset}
          instanceId={agentToolsetDialog.instanceId}
          agentKey={effectiveAgentKey}
          onClose={() => setAgentToolsetDialog(null)}
          onSuccess={() => {
            setAgentToolsetDialog(null);
            refreshToolsets(effectiveAgentKey, true);
          }}
        />
      )}

      {/* Service Account Confirm Dialog */}
      <ServiceAccountConfirmDialog
        open={serviceAccountConfirmOpen}
        agentName={agentName}
        creating={serviceAccountCreating}
        error={serviceAccountError}
        hasUserToolsets={hasToolsets}
        isConverting={Boolean(loadedAgent || editingAgent)}
        onClose={() => {
          setServiceAccountConfirmOpen(false);
          setServiceAccountError(null);
        }}
        onConfirm={async () => {
          if (!agentName?.trim()) {
            setServiceAccountError('Please enter an agent name before enabling service account mode.');
            return;
          }
          setServiceAccountCreating(true);
          setServiceAccountError(null);
          try {
            // Build payload from the current flow state so all fields are included.
            // isServiceAccount is forced to true regardless of previous value.
            // shareWithOrg is also forced to true: service account agents must always
            // be org-wide so that internal (Slack) calls can reach them.
            const currentAgent = loadedAgent || editingAgent;
            const agentConfig: AgentFormData = extractAgentConfigFromFlow(
              agentName.trim(),
              nodes,
              edges,
              currentAgent ?? null,
              true, // force shareWithOrg = true for service account agents
              true  // isServiceAccount
            );

            if (currentAgent) {
              // ── Converting an existing agent ──────────────────────────────
              // Use updateAgent so the existing _key is preserved, then
              // refresh the agent data in-place (no navigation needed).
              // This avoids the "same URL → no remount" problem and gives an
              // immediate, smooth update: the header badge flips to "Service
              // Account" and the toolset list reloads from the agent endpoint.
              await AgentApiService.updateAgent(currentAgent._key, agentConfig);
              setServiceAccountConfirmOpen(false);
              // refreshAgent re-fetches the agent from the API (gets
              // isServiceAccount: true) and reloads toolsets from the correct
              // agent-scoped endpoint — all without leaving the page.
              await refreshAgent(currentAgent._key);
              setSuccess('Agent converted to Service Account! Configure per-toolset credentials using the 🔑 icon in the sidebar.');
            } else {
              // ── Creating a brand-new service-account agent ────────────────
              // Navigate to the edit route so the builder fully re-mounts with
              // the real _key and fetches agent-scoped toolsets from the start.
              const created = await AgentApiService.createAgent(agentConfig);
              setServiceAccountConfirmOpen(false);
              navigate(paths.dashboard.agent.edit(created._key), {
                state: { serviceAccountJustCreated: true },
              });
            }
          } catch (err: any) {
            const msg = err?.response?.data?.detail || 'Failed to enable service account mode. Please try again.';
            setServiceAccountError(msg);
          } finally {
            setServiceAccountCreating(false);
          }
        }}
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

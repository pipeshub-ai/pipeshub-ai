import React, { useState, useCallback, useEffect } from 'react';
import { Handle, Position, useStore, useReactFlow } from '@xyflow/react';
import {
  Box,
  Card,
  Typography,
  useTheme,
  alpha,
  Chip,
  IconButton,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Switch,
  FormControlLabel,
  Tooltip,
} from '@mui/material';
import { Icon } from '@iconify/react';
import brainIcon from '@iconify-icons/mdi/brain';
import toolIcon from '@iconify-icons/mdi/tools';
import collectionIcon from '@iconify-icons/mdi/folder-multiple-outline';
import closeIcon from '@iconify-icons/eva/close-outline';
import scriptIcon from '@iconify-icons/mdi/script-text';
import pencilIcon from '@iconify-icons/mdi/pencil';
import messageTextIcon from '@iconify-icons/mdi/message-text';
import packageIcon from '@iconify-icons/mdi/package-variant';
import cogIcon from '@iconify-icons/mdi/cog';
import cloudIcon from '@iconify-icons/mdi/cloud-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import chevronUpIcon from '@iconify-icons/mdi/chevron-up';
import checkIcon from '@iconify-icons/mdi/check';
import { formattedProvider, normalizeDisplayName } from '../../utils/agent';
import { NodeData } from '../../types/agent';
import { NodeHandles, NodeIcon } from './nodes';
import { ToolsetNode } from './nodes/ToolsetNode';

interface FlowNodeProps {
  id?: string; // ReactFlow passes the actual node ID as a prop automatically
  data: NodeData;
  selected: boolean;
  onDelete?: (nodeId: string) => void;
}

// Helper function to get model display name (friendly name or fallback to modelName)
const getModelDisplayName = (config: { modelName?: string; modelFriendlyName?: string } | null | undefined): string => {
  if (!config) return '';

  // Prioritize friendly name, fallback to modelName
  return (config.modelFriendlyName && config.modelFriendlyName.trim()) || (config.modelName && config.modelName.trim()) || '';
};

const FlowNode: React.FC<FlowNodeProps> = ({ id: reactFlowId, data, selected, onDelete }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const storeNodes = useStore((s) => s.nodes);
  const storeEdges = useStore((s) => s.edges);
  const { setNodes, setEdges } = useReactFlow();
  const [lastClickTime, setLastClickTime] = useState(0);

  // Editing state
  const [promptDialogOpen, setPromptDialogOpen] = useState(false);
  const [systemPromptValue, setSystemPromptValue] = useState(
    data.config?.systemPrompt || 'You are a helpful assistant.'
  );
  const [instructionsValue, setInstructionsValue] = useState(
    data.config?.instructions || ''
  );
  const [startMessageValue, setStartMessageValue] = useState(
    data.config?.startMessage || 'Hello! How can I help you today?'
  );
  // Prompt field editing & expand state
  const [editingField, setEditingField] = useState<string | null>(null);
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set());

  const nodeId = data.id;

  const handlePromptDialogOpen = () => {
    setSystemPromptValue(data.config?.systemPrompt || 'You are a helpful assistant.');
    setInstructionsValue(data.config?.instructions ?? '');
    setStartMessageValue(data.config?.startMessage || 'Hello! How can I help you today?');
    setEditingField(null);
    setExpandedFields(new Set());
    setPromptDialogOpen(true);
  };

  const handlePromptDialogSave = () => {
    // Single setNodes call to atomically update all three fields at once
    setNodes((nodes: any[]) =>
      nodes.map((node: any) =>
        node.id === nodeId
          ? {
              ...node,
              data: {
                ...node.data,
                config: {
                  ...node.data.config,
                  systemPrompt: systemPromptValue,
                  instructions: instructionsValue,
                  startMessage: startMessageValue,
                },
              },
            }
          : node
      )
    );
    setEditingField(null);
    setExpandedFields(new Set());
    setPromptDialogOpen(false);
  };

  const handlePromptDialogCancel = () => {
    setSystemPromptValue(data.config?.systemPrompt || 'You are a helpful assistant.');
    setInstructionsValue(data.config?.instructions ?? '');
    setStartMessageValue(data.config?.startMessage || 'Hello! How can I help you today?');
    setEditingField(null);
    setExpandedFields(new Set());
    setPromptDialogOpen(false);
  };

  // Sync local state when node data changes externally (e.g. from flow reconstruction)
  useEffect(() => {
    if (!promptDialogOpen) {
      setSystemPromptValue(data.config?.systemPrompt || 'You are a helpful assistant.');
      setInstructionsValue(data.config?.instructions ?? '');
      setStartMessageValue(data.config?.startMessage || 'Hello! How can I help you today?');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.config?.systemPrompt, data.config?.instructions, data.config?.startMessage]);

  const connectedNodesByHandle = React.useMemo(() => {
    if (data.type !== 'agent-core') return {} as Record<string, any[]>;
    const incoming = storeEdges.filter((e) => e.target === data.id);
    const map: Record<string, any[]> = { input: [], toolsets: [], knowledge: [], llms: [] };
    incoming.forEach((e: any) => {
      const sourceNode = storeNodes.find((n) => n.id === e.source) as any;
      if (sourceNode) {
        const handle = e.targetHandle || 'input';
        if (!map[handle]) map[handle] = [];
        map[handle].push(sourceNode.data);
      }
    });
    return map;
  }, [data.id, data.type, storeEdges, storeNodes]);

  // Professional color scheme with better contrast and visibility
  const colors = {
    primary: isDark ? '#9ca3af' : '#6b6b6b',
    secondary: isDark ? '#a8b2c0' : '#808080',
    success: isDark ? '#9ca3af' : '#525252',
    warning: isDark ? '#9ca3af' : '#525252',
    info: isDark ? '#9ca3af' : '#525252',
    background: {
      card: theme.palette.background.paper,
      section: isDark 
        ? alpha('#2a2a2a', 0.6) 
        : alpha(theme.palette.background.default, 0.7),
      field: isDark 
        ? alpha('#2a2a2a', 0.4) 
        : alpha(theme.palette.background.paper, 0.8),
      hover: theme.palette.action.hover,
    },
    border: {
      main: isDark 
        ? '#4a4a4a' 
        : '#d0d0d0',
      subtle: isDark 
        ? '#3a3a3a' 
        : '#e0e0e0',
      focus: isDark ? '#6b6b6b' : '#a0a0a0',
    },
    text: {
      primary: theme.palette.text.primary,
      secondary: theme.palette.text.secondary,
      muted: isDark 
        ? alpha(theme.palette.text.secondary, 0.75) 
        : alpha(theme.palette.text.secondary, 0.65),
    },
  };

  // Enhanced Agent node
  // Enhanced Agent node
  if (data.type === 'agent-core') {
    return (
      <Card
        sx={{
          width: 380,
          minHeight: 600,
          border: selected ? `2px solid ${colors.border.focus}` : `2px solid ${colors.border.main}`,
          borderRadius: 1.5,
          backgroundColor: colors.background.card,
          boxShadow: selected 
            ? (isDark ? `0 4px 12px rgba(0, 0, 0, 0.3)` : `0 4px 12px rgba(0, 0, 0, 0.1)`)
            : isDark 
              ? `0 2px 8px rgba(0, 0, 0, 0.15)`
              : `0 2px 8px rgba(0, 0, 0, 0.08)`,
          cursor: 'pointer',
          transition: 'all 0.2s ease',
          position: 'relative',
          overflow: 'visible',
          '&:hover': {
            borderColor: colors.border.focus,
            borderWidth: '2.5px',
            boxShadow: isDark 
              ? `0 4px 12px rgba(0, 0, 0, 0.25)`
              : `0 4px 12px rgba(0, 0, 0, 0.12)`,
          },
        }}
        onClick={(e) => {
          // Ignore clicks on delete button or other interactive elements
          const target = e.target as HTMLElement;
          if (
            target.closest('button') ||
            target.closest('[role="button"]') ||
            target.tagName === 'BUTTON' ||
            target.closest('svg')
          ) {
            return;
          }
          // Prevent rapid clicks
          const now = Date.now();
          if (now - lastClickTime < 300) return;
          setLastClickTime(now);
          e.stopPropagation();
        }}
      >
        {/* Minimal Header */}
        <Box
          sx={{
            p: 2,
            borderBottom: `2px solid ${colors.border.main}`,
            backgroundColor: isDark 
              ? alpha('#1f1f1f', 0.5) 
              : alpha(theme.palette.background.default, 0.5),
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Icon icon={brainIcon} width={18} height={18} style={{ color: colors.text.secondary }} />
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.9375rem',
                  color: colors.text.primary,
                }}
              >
                Agent
              </Typography>
            </Box>
          </Box>
          <Typography
            variant="body2"
            sx={{
              color: colors.text.secondary,
              fontSize: '0.8125rem',
              lineHeight: 1.5,
              fontWeight: 400,
            }}
          >
            Define the agent&apos;s instructions, then enter a task to complete using tools.
          </Typography>
        </Box>

        {/* Agent Configuration Section */}
        <Box
          sx={{
            px: 2.5,
            py: 2,
            borderBottom: `2px solid ${colors.border.subtle}`,
            backgroundColor: isDark 
              ? alpha('#1f1f1f', 0.3) 
              : alpha(theme.palette.background.default, 0.5),
          }}
        >
          {/* System Prompt */}
          <Box sx={{ mb: 2 }}>
            {' '}
            {/* Keep comfortable margin */}
            <Box
              sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }} // Keep comfortable margin
            >
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 700,
                  color: colors.primary,
                  fontSize: '0.75rem', // Keep comfortable size
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                <Icon
                  icon={scriptIcon}
                  width={12}
                  height={12} // Keep comfortable size
                  style={{ color: colors.primary }}
                />
                System Prompt
              </Typography>
              <Box sx={{ display: 'flex', gap: 1 }}>
                {' '}
                {/* Keep comfortable gap */}
                <IconButton
                  size="small"
                  onClick={handlePromptDialogOpen}
                  sx={{
                    width: 24, // Keep comfortable size
                    height: 24, // Keep comfortable size
                    backgroundColor: alpha(colors.primary, 0.1),
                    border: `1px solid ${alpha(colors.primary, 0.2)}`,
                    color: colors.primary,
                    '&:hover': {
                      backgroundColor: alpha(colors.primary, 0.2),
                      transform: 'scale(1.05)',
                    },
                    transition: 'all 0.2s ease',
                  }}
                >
                  <Icon icon={pencilIcon} width={12} height={12} /> {/* Keep comfortable size */}
                </IconButton>
              </Box>
            </Box>
            <Box
              sx={{
                p: 1.5,
                backgroundColor: isDark 
                  ? alpha('#2a2a2a', 0.5) 
                  : alpha(theme.palette.background.paper, 0.9),
                borderRadius: 1.5,
                border: `1px solid ${colors.border.subtle}`,
                minHeight: 60,
                maxHeight: 80,
                overflow: 'auto',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: isDark 
                    ? alpha('#2a2a2a', 0.7) 
                    : theme.palette.background.paper,
                  borderColor: colors.border.main,
                  borderWidth: '1.5px',
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 500,
                  lineHeight: 1.4,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {data.config?.systemPrompt || 'You are a helpful assistant.'}
              </Typography>
            </Box>
          </Box>

          {/* Starting Message */}
          <Box sx={{ mb: 2 }}>
            {' '}
            {/* Keep comfortable margin */}
            <Box
              sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }} // Keep comfortable margin
            >
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 700,
                  color: colors.success,
                  fontSize: '0.75rem', // Keep comfortable size
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                <Icon
                  icon={messageTextIcon}
                  width={12}
                  height={12} // Keep comfortable size
                  style={{ color: colors.success }}
                />
                Starting Message
              </Typography>
            </Box>
            <Box
              sx={{
                p: 1.5,
                backgroundColor: isDark 
                  ? alpha('#2a2a2a', 0.5) 
                  : alpha(theme.palette.background.paper, 0.9),
                borderRadius: 1.5,
                border: `1px solid ${colors.border.subtle}`,
                minHeight: 40,
                maxHeight: 60,
                overflow: 'auto',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: isDark 
                    ? alpha('#2a2a2a', 0.7) 
                    : theme.palette.background.paper,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 500,
                  lineHeight: 1.4,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {data.config?.startMessage || 'Hello! How can I help you today?'}
              </Typography>
            </Box>
          </Box>

          {/* Instructions */}
          <Box sx={{ mb: 2 }}>
            {' '}
            {/* Keep comfortable margin */}
            <Box
              sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }} // Keep comfortable margin
            >
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 700,
                  color: colors.info,
                  fontSize: '0.75rem', // Keep comfortable size
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                <Icon
                  icon={scriptIcon}
                  width={12}
                  height={12} // Keep comfortable size
                  style={{ color: colors.info }}
                />
                Instructions
              </Typography>
            </Box>
            <Box
              sx={{
                p: 1.5,
                backgroundColor: isDark 
                  ? alpha('#2a2a2a', 0.5) 
                  : alpha(theme.palette.background.paper, 0.9),
                borderRadius: 1.5,
                border: `1px solid ${colors.border.subtle}`,
                minHeight: 40,
                maxHeight: 60,
                overflow: 'auto',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: isDark 
                    ? alpha('#2a2a2a', 0.7) 
                    : theme.palette.background.paper,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: data.config?.instructions?.trim() ? colors.text.primary : colors.text.muted,
                  fontWeight: 500,
                  lineHeight: 1.4,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontStyle: data.config?.instructions?.trim() ? 'normal' : 'italic',
                }}
              >
                {data.config?.instructions?.trim() || 'No instructions set'}
              </Typography>
            </Box>
          </Box>

        </Box>

        {/* Content with improved spacing */}
        <Box sx={{ p: 2.5 }}>
          {' '}
          {/* Keep comfortable padding */}
          {/* Model Provider Section */}
          <Box sx={{ mb: 2.5 }}>
            {' '}
            {/* Keep comfortable margin */}
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.8rem', // Keep comfortable size
                mb: 1.5, // Keep comfortable margin
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              Model Provider
            </Typography>
            <Box
              sx={{
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                position: 'relative',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Handle
                type="target"
                position={Position.Left}
                id="llms"
                style={{
                  top: '50%',
                  left: -9,
                  background: `linear-gradient(135deg, ${colors.primary} 0%, ${colors.secondary} 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.primary, 0.4)}`,
                  zIndex: 10,
                  transformOrigin: 'center',
                }}
              />
              {connectedNodesByHandle.llms?.length > 0 ? (
                <Box>
                  {connectedNodesByHandle.llms.slice(0, 2).map((llmNode, index) => (
                    <Box
                      key={index}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1.5, // Keep comfortable gap
                        mt: index > 0 ? 1.5 : 0, // Keep comfortable margin
                      }}
                    >
                      <Box
                        sx={{
                          width: 24, // Keep comfortable size
                          height: 24, // Keep comfortable size
                          borderRadius: 1.5,
                          background: `linear-gradient(135deg, ${colors.info} 0%, ${colors.primary} 100%)`,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          boxShadow: `0 2px 4px ${alpha(colors.info, 0.3)}`,
                        }}
                      >
                        <Icon
                          icon={brainIcon}
                          width={12}
                          height={12} // Keep comfortable size
                          style={{ color: '#ffffff' }}
                        />
                      </Box>
                      <Box>
                        <Typography
                          sx={{ fontSize: '0.85rem', fontWeight: 600, color: colors.text.primary }} // Keep comfortable size
                        >
                          {formattedProvider(llmNode.config?.provider || 'LLM Provider')}
                        </Typography>
                        <Typography
                          sx={{
                            fontSize: '0.75rem', // Keep comfortable size
                            color: colors.text.secondary,
                            fontWeight: 500,
                          }}
                        >
                          {getModelDisplayName(llmNode.config) || llmNode.label}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                  {connectedNodesByHandle.llms.length > 2 && (
                    <Chip
                      label={`+${connectedNodesByHandle.llms.length - 2} more`}
                      size="small"
                      sx={{
                        height: 22, // Keep comfortable size
                        fontSize: '0.7rem', // Keep comfortable size
                        fontWeight: 600,
                        backgroundColor: isDark
                          ? alpha('#ffffff', 0.2)
                          : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        mt: 1, // Keep comfortable margin
                        '&:hover': {
                          backgroundColor: isDark
                            ? alpha(colors.text.secondary, 0.2)
                            : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          color: isDark ? colors.text.secondary : colors.text.secondary,
                          borderColor: isDark
                            ? alpha(colors.text.secondary, 0.2)
                            : alpha(colors.text.secondary, 0.2),
                        },
                        transition: 'all 0.2s ease',
                      }}
                    />
                  )}
                </Box>
              ) : (
                <Typography
                  sx={{ fontSize: '0.85rem', color: colors.text.muted, fontStyle: 'italic' }} // Keep comfortable size
                >
                  No model connected
                </Typography>
              )}
            </Box>
          </Box>
          {/* Knowledge Section */}
          <Box sx={{ mb: 2.5 }}>
            {' '}
            {/* Keep comfortable margin */}
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.8rem', // Keep comfortable size
                mb: 1.5, // Keep comfortable margin
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              Knowledge
            </Typography>
            <Box
              sx={{
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                position: 'relative',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Handle
                type="target"
                position={Position.Left}
                id="knowledge"
                style={{
                  top: '50%',
                  left: -9,
                  background: `linear-gradient(135deg, ${colors.warning} 0%, #f59e0b 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.warning, 0.4)}`,
                  zIndex: 10,
                  transformOrigin: 'center',
                }}
              />
              {connectedNodesByHandle.knowledge?.length > 0 ? (
                <Box>
                  {connectedNodesByHandle.knowledge.slice(0, 2).map((knowledgeNode, index) => {
                    if (knowledgeNode.type.startsWith('app-group')) {
                      return (
                        <Box key={`app-group-${index}`}>
                          {knowledgeNode.config.apps.slice(0, 3).map((app: any, appIndex: number) => (
                            <Box
                              key={appIndex}
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 1.5,
                                mt: appIndex > 0 ? 1.5 : (index > 0 ? 1.5 : 0),
                              }}
                            >
                              <Box
                                sx={{
                                  width: 24,
                                  height: 24,
                                  borderRadius: 1.5,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  backgroundColor: alpha(colors.info, 0.1),
                                  boxShadow: `0 2px 4px ${alpha(colors.info, 0.2)}`,
                                }}
                              >
                                <img
                                  src={
                                    app.iconPath ||
                                    `/assets/icons/connectors/${(app.type || app.name || '').replace(/\s+/g, '').toLowerCase()}.svg`
                                  }
                                  alt={app.name || app.type}
                                  width={14}
                                  height={14}
                                  style={{
                                    objectFit: 'contain',
                                  }}
                                  onError={(e) => {
                                    e.currentTarget.src = '/assets/icons/connectors/collections-gray.svg';
                                  }}
                                />
                              </Box>
                              <Box sx={{ flex: 1, minWidth: 0 }}>
                                <Typography
                                  sx={{
                                    fontSize: '0.85rem',
                                    fontWeight: 600,
                                    color: colors.text.primary,
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                  }}
                                >
                                  {normalizeDisplayName(
                                    app.displayName || app.name || app.type || 'Unknown'
                                  )}
                                </Typography>
                                <Typography
                                  sx={{
                                    fontSize: '0.7rem',
                                    color: colors.text.secondary,
                                    fontWeight: 500,
                                    mt: 0.25,
                                  }}
                                >
                                  {app.scope === 'team' ? 'Team' : 'Personal'} • {app.type || 'App'}
                                </Typography>
                              </Box>
                            </Box>
                          ))}
                          {knowledgeNode.config.apps.length > 3 && (
                            <Chip
                              label={`+${knowledgeNode.config.apps.length - 3} more`}
                              size="small"
                              sx={{
                                height: 22,
                                fontSize: '0.7rem',
                                fontWeight: 600,
                                backgroundColor: isDark
                                  ? alpha('#ffffff', 0.2)
                                  : alpha(colors.text.secondary, 0.1),
                                color: colors.text.secondary,
                                border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                                mt: 1.5,
                                '&:hover': {
                                  backgroundColor: isDark
                                    ? alpha('#ffffff', 0.2)
                                    : alpha(colors.text.secondary, 0.2),
                                  transform: 'scale(1.05)',
                                  borderColor: isDark
                                    ? alpha(colors.text.secondary, 0.2)
                                    : alpha(colors.text.secondary, 0.2),
                                },
                                transition: 'all 0.2s ease',
                              }}
                            />
                          )}
                        </Box>
                      );
                    }
                    return (
                      <Box
                        key={index}
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 1.5, // Keep comfortable gap
                          mt: index > 0 ? 1.5 : 0, // Keep comfortable margin
                        }}
                      >
                        <Box
                          sx={{
                            width: 24, // Keep comfortable size
                            height: 24, // Keep comfortable size
                            borderRadius: 1.5,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            boxShadow: `0 2px 4px ${alpha(colors.primary, 0.3)}`,
                          }}
                        >
                          {/* <Icon
                          icon={databaseIcon}
                          width={12}
                          height={12} // Keep comfortable size
                          style={{ color: '#ffffff' }}
                        /> */}
                          <img
                            src={
                              knowledgeNode.config?.iconPath ||
                              '/assets/icons/connectors/collections-gray.svg'
                            }
                            alt=""
                            style={{
                              width: 20,
                              height: 20,
                              objectFit: 'contain',
                            }}
                          />
                        </Box>
                        <Box>
                          <Typography
                            sx={{
                              fontSize: '0.85rem',
                              fontWeight: 600,
                              color: colors.text.primary,
                            }} // Keep comfortable size
                          >
                            {knowledgeNode.config?.kbName ||
                              knowledgeNode.config?.appName ||
                              knowledgeNode.label}
                          </Typography>
                          <Typography
                            sx={{
                              fontSize: '0.75rem', // Keep comfortable size
                              color: colors.text.secondary,
                              fontWeight: 500,
                            }}
                          >
                            {knowledgeNode.type.startsWith('kb-')
                              ? 'Collections'
                              : knowledgeNode.type.startsWith('knowledge-hub')
                                ? 'Collections Hub'
                                : 'Knowledge'}
                          </Typography>
                        </Box>
                      </Box>
                    );
                  })}
                  {connectedNodesByHandle.knowledge.length > 2 && (
                    <Chip
                      label={`+${connectedNodesByHandle.knowledge.length - 2} more`}
                      size="small"
                      sx={{
                        height: 22, // Keep comfortable size
                        fontSize: '0.7rem', // Keep comfortable size
                        fontWeight: 600,
                        backgroundColor: isDark
                          ? alpha('#ffffff', 0.2)
                          : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        mt: 1, // Keep comfortable margin
                        '&:hover': {
                          backgroundColor: isDark
                            ? alpha('#ffffff', 0.2)
                            : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          color: isDark ? colors.text.secondary : colors.text.secondary,
                          borderColor: isDark
                            ? alpha(colors.text.secondary, 0.2)
                            : alpha(colors.text.secondary, 0.2),
                        },
                        transition: 'all 0.2s ease',
                      }}
                    />
                  )}
                </Box>
              ) : (
                <Typography
                  sx={{ fontSize: '0.85rem', color: colors.text.muted, fontStyle: 'italic' }} // Keep comfortable size
                >
                  No knowledge connected
                </Typography>
              )}
            </Box>
          </Box>
          {/* Actions/Tools Section */}
          <Box sx={{ mb: 2.5 }}>
            {' '}
            {/* Keep comfortable margin */}
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.8rem', // Keep comfortable size
                mb: 1.5, // Keep comfortable margin
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              Toolsets
            </Typography>
            <Box
              sx={{
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                position: 'relative',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Handle
                type="target"
                position={Position.Left}
                id="toolsets"
                style={{
                  top: '50%',
                  left: -9,
                  background: `linear-gradient(135deg, ${colors.info} 0%, #06b6d4 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.info, 0.4)}`,
                  zIndex: 10,
                  transformOrigin: 'center',
                }}
              />
              {connectedNodesByHandle.toolsets?.length > 0 ? (
                <Box>
                  {connectedNodesByHandle.toolsets.slice(0, 2).map((toolsetNode: any, index: number) => (
                    <Box
                      key={index}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1.5, // Keep comfortable gap
                        mt: index > 0 ? 1.5 : 0, // Keep comfortable margin
                      }}
                    >
                      <Box
                        sx={{
                          width: 24, // Keep comfortable size
                          height: 24, // Keep comfortable size
                          borderRadius: 1.5,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          boxShadow: `0 2px 4px ${alpha(colors.info, 0.3)}`,
                        }}
                      >
                        {toolsetNode.config?.iconPath ? (
                          <img
                            src={toolsetNode.config.iconPath}
                            alt=""
                            style={{
                              width: 14,
                              height: 14,
                              objectFit: 'contain',
                            }}
                            onError={(e) => {
                              e.currentTarget.style.display = 'none';
                              e.currentTarget.nextElementSibling?.setAttribute(
                                'style',
                                'display: block; color: #ffffff;'
                              );
                            }}
                          />
                        ) : null}
                        <Icon
                          icon={toolIcon}
                          width={12}
                          height={12} // Keep comfortable size
                          style={{
                            color: '#ffffff',
                            display: toolsetNode.config?.iconPath ? 'none' : 'block',
                          }}
                        />
                      </Box>
                      <Box>
                        <Typography
                          sx={{ fontSize: '0.85rem', fontWeight: 600, color: colors.text.primary }} // Keep comfortable size
                        >
                          {toolsetNode.config?.displayName ||
                            toolsetNode.config?.name ||
                            toolsetNode.config?.appName ||
                            toolsetNode.label}
                        </Typography>
                        <Typography
                          sx={{
                            fontSize: '0.75rem', // Keep comfortable size
                            color: colors.text.secondary,
                            fontWeight: 500,
                          }}
                        >
                          {toolsetNode.type.startsWith('toolset-')
                            ? 'Toolset'
                            : toolsetNode.type.startsWith('tool-group-')
                              ? 'Tool Group'
                              : toolsetNode.type.startsWith('tool-individual-')
                                ? 'Individual Tool'
                                : toolsetNode.type.startsWith('connector-group-')
                                  ? 'Connector Group'
                                  : 'Toolset'}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                  {connectedNodesByHandle.toolsets.length > 2 && (
                    <Chip
                      label={`+${connectedNodesByHandle.toolsets.length - 2} more`}
                      size="small"
                      sx={{
                        height: 22, // Keep comfortable size
                        fontSize: '0.7rem', // Keep comfortable size
                        fontWeight: 600,
                        backgroundColor: isDark
                          ? alpha('#ffffff', 0.2)
                          : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        mt: 1, // Keep comfortable margin
                        '&:hover': {
                          backgroundColor: isDark
                            ? alpha('#ffffff', 0.2)
                            : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          color: isDark ? colors.text.secondary : colors.text.secondary,
                          borderColor: isDark
                            ? alpha(colors.text.secondary, 0.2)
                            : alpha(colors.text.secondary, 0.2),
                        },
                        transition: 'all 0.2s ease',
                      }}
                    />
                  )}
                </Box>
              ) : (
                <Typography
                  sx={{ fontSize: '0.85rem', color: colors.text.muted, fontStyle: 'italic' }} // Keep comfortable size
                >
                  No toolsets connected
                </Typography>
              )}
            </Box>
          </Box>
          {/* Input Section */}
          <Box sx={{ mb: 2.5 }}>
            {' '}
            {/* Keep comfortable margin */}
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.8rem', // Keep comfortable size
                mb: 1.5, // Keep comfortable margin
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              Input
            </Typography>
            <Box
              sx={{
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                position: 'relative',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Handle
                type="target"
                position={Position.Left}
                id="input"
                style={{
                  top: '50%',
                  left: -9,
                  background: `linear-gradient(135deg, ${colors.secondary} 0%, #a855f7 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.secondary, 0.4)}`,
                  zIndex: 10,
                  transformOrigin: 'center',
                }}
              />
              {connectedNodesByHandle.input?.length > 0 ? (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                  {' '}
                  {/* Keep comfortable gap */}
                  {connectedNodesByHandle.input.slice(0, 2).map((inputNode, index) => (
                    <Chip
                      key={index}
                      label={
                        inputNode.label.length > 12
                          ? `${inputNode.label.slice(0, 12)}...`
                          : inputNode.label
                      }
                      size="small"
                      sx={{
                        height: 24, // Keep comfortable size
                        fontSize: '0.7rem', // Keep comfortable size
                        fontWeight: 600,
                        backgroundColor: isDark
                          ? alpha(colors.secondary, 0.8)
                          : alpha(colors.secondary, 0.1),
                        color: colors.secondary,
                        border: `1px solid ${alpha(colors.secondary, 0.3)}`,
                        '&:hover': {
                          backgroundColor: isDark
                            ? alpha('#ffffff', 0.2)
                            : alpha(colors.secondary, 0.2),
                          borderColor: colors.secondary,
                          transform: 'scale(1.05)',
                          boxShadow: `0 2px 8px ${alpha(colors.secondary, 0.3)}`,
                          color: isDark ? colors.secondary : colors.secondary,
                        },
                        transition: 'all 0.2s ease',
                        '& .MuiChip-label': { px: 1 }, // Keep comfortable padding
                      }}
                    />
                  ))}
                  {connectedNodesByHandle.input.length > 2 && (
                    <Chip
                      label={`+${connectedNodesByHandle.input.length - 2}`}
                      size="small"
                      sx={{
                        height: 24, // Keep comfortable size
                        fontSize: '0.7rem', // Keep comfortable size
                        fontWeight: 600,
                        backgroundColor: isDark
                          ? alpha('#ffffff', 0.2)
                          : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        '&:hover': {
                          backgroundColor: isDark
                            ? alpha(colors.text.secondary, 0.2)
                            : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          color: isDark ? colors.text.secondary : colors.text.secondary,
                          borderColor: isDark
                            ? alpha(colors.text.secondary, 0.2)
                            : alpha(colors.text.secondary, 0.2),
                        },
                        transition: 'all 0.2s ease',
                        '& .MuiChip-label': { px: 1 }, // Keep comfortable padding
                      }}
                    />
                  )}
                </Box>
              ) : (
                <Typography
                  sx={{ fontSize: '0.85rem', color: colors.text.muted, fontStyle: 'italic' }} // Keep comfortable size
                >
                  Receiving input
                </Typography>
              )}
            </Box>
          </Box>
          {/* Response Section */}
          <Box>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.8rem', // Keep comfortable size
                mb: 1.5, // Keep comfortable margin
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              Response
            </Typography>
            <Box
              sx={{
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                position: 'relative',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Handle
                type="source"
                position={Position.Right}
                id="response"
                style={{
                  top: '50%',
                  right: -9,
                  background: `linear-gradient(135deg, ${colors.success} 0%, #16a34a 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.success, 0.4)}`,
                  zIndex: 10,
                  transformOrigin: 'center',
                }}
              />
              <Typography
                sx={{ fontSize: '0.85rem', color: colors.text.muted, fontStyle: 'italic' }} // Keep comfortable size
              >
                Agent response
              </Typography>
            </Box>
          </Box>
        </Box>

        {/* Prompt Configuration Dialog with consistent styling */}
        <Dialog
          open={promptDialogOpen}
          onClose={handlePromptDialogCancel}
          maxWidth="md"
          fullWidth
          BackdropProps={{
            sx: {
              backdropFilter: 'blur(1px)',
              backgroundColor: alpha(theme.palette.common.black, 0.3),
            },
          }}
          PaperProps={{
            sx: {
              borderRadius: 1,
              boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
              overflow: 'hidden',
            },
          }}
        >
          <DialogTitle
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              p: 2.5,
              pl: 3,
              color: theme.palette.text.primary,
              borderBottom: '1px solid',
              borderColor: theme.palette.divider,
              fontWeight: 500,
              fontSize: '1rem',
              m: 0,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 32,
                  height: 32,
                  borderRadius: '6px',
                  bgcolor: alpha(theme.palette.primary.main, 0.1),
                  color: theme.palette.primary.main,
                }}
              >
                <Icon icon={scriptIcon} width={18} height={18} />
              </Box>
              Configure Agent Prompts
            </Box>

            <IconButton
              onClick={handlePromptDialogCancel}
              size="small"
              sx={{ color: theme.palette.text.secondary }}
              aria-label="close"
            >
              <Icon icon={closeIcon} width={20} height={20} />
            </IconButton>
          </DialogTitle>

          <DialogContent
            sx={{
              p: 0,
              '&.MuiDialogContent-root': {
                pt: 3,
                px: 3,
                pb: 0,
              },
              '&::-webkit-scrollbar': {
                width: 8,
              },
              '&::-webkit-scrollbar-track': {
                bgcolor: 'transparent',
              },
              '&::-webkit-scrollbar-thumb': {
                bgcolor: isDark ? alpha('#fff', 0.15) : alpha('#000', 0.15),
                borderRadius: 4,
                border: '2px solid transparent',
                backgroundClip: 'content-box',
                '&:hover': {
                  bgcolor: isDark ? alpha('#fff', 0.25) : alpha('#000', 0.25),
                },
              },
              scrollbarWidth: 'thin',
              scrollbarColor: isDark
                ? `${alpha('#fff', 0.15)} transparent`
                : `${alpha('#000', 0.15)} transparent`,
            }}
          >
            <Box sx={{ mb: 3 }}>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Define the agent&apos;s behavior and initial greeting message for users.
              </Typography>

              {/* System Prompt - Collapsible preview card */}
              {(() => {
                const fieldKey = 'systemPrompt';
                const value = systemPromptValue;
                const setValue = setSystemPromptValue;
                const isEditing = editingField === fieldKey;
                const isExpanded = expandedFields.has(fieldKey);
                const hasContent = value.trim().length > 0;
                const lineCount = value.split('\n').length;
                const charCount = value.length;
                const isLong = lineCount > 5 || charCount > 300;
                const previewBg = isDark ? alpha('#1e1e2e', 0.7) : alpha(theme.palette.grey[100], 0.9);

                return (
                  <Box sx={{ mb: 3 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 500 }}>
                          System Prompt
                        </Typography>
                        {hasContent && (
                          <Chip
                            label={`${lineCount} ${lineCount === 1 ? 'line' : 'lines'} · ${charCount} chars`}
                            size="small"
                            sx={{
                              height: 20,
                              fontSize: '0.65rem',
                              bgcolor: isDark ? alpha('#fff', 0.12) : alpha('#000', 0.06),
                              color: isDark ? alpha('#fff', 0.7) : alpha('#000', 0.55),
                              '& .MuiChip-label': { px: 1 },
                            }}
                          />
                        )}
                      </Box>
                      <Button
                        size="small"
                        onClick={() => setEditingField(isEditing ? null : fieldKey)}
                        startIcon={<Icon icon={isEditing ? checkIcon : pencilIcon} width={13} height={13} />}
                        sx={{
                          fontSize: '0.7rem',
                          textTransform: 'none',
                          color: isEditing ? theme.palette.success.main : colors.text.muted,
                          minWidth: 0,
                          px: 1,
                          '&:hover': { bgcolor: isDark ? alpha('#fff', 0.05) : alpha('#000', 0.04) },
                        }}
                      >
                        {isEditing ? 'Done' : 'Edit'}
                      </Button>
                    </Box>

                    {isEditing ? (
                      <TextField
                        fullWidth
                        multiline
                        minRows={4}
                        maxRows={16}
                        value={value}
                        onChange={(e) => setValue(e.target.value)}
                        autoFocus
                        placeholder="Define the agent's role, capabilities, and behavior instructions..."
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            borderRadius: 1,
                            backgroundColor: isDark
                              ? alpha('#2a2a2a', 0.5)
                              : alpha(theme.palette.background.paper, 0.9),
                            color: colors.text.primary,
                            fontFamily: '"SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace',
                            fontSize: '0.8rem',
                            lineHeight: 1.7,
                            '& fieldset': { borderColor: colors.border.focus, borderWidth: 1.5 },
                            '&:hover fieldset': { borderColor: colors.border.focus },
                            '&.Mui-focused fieldset': { borderColor: theme.palette.primary.main, borderWidth: 1.5 },
                          },
                          '& .MuiInputBase-input': {
                            color: colors.text.primary,
                            '&::placeholder': { color: colors.text.muted, opacity: 1 },
                          },
                        }}
                      />
                    ) : hasContent ? (
                      <Box
                        sx={{
                          position: 'relative',
                          bgcolor: previewBg,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: isDark ? alpha('#fff', 0.08) : alpha('#000', 0.08),
                          overflow: 'hidden',
                          cursor: 'pointer',
                          transition: 'border-color 0.2s',
                          '&:hover': {
                            borderColor: isDark ? alpha('#fff', 0.15) : alpha('#000', 0.15),
                          },
                        }}
                        onClick={() => {
                          if (isLong) {
                            setExpandedFields((prev) => {
                              const next = new Set(prev);
                              if (next.has(fieldKey)) next.delete(fieldKey);
                              else next.add(fieldKey);
                              return next;
                            });
                          }
                        }}
                      >
                        <Box
                          sx={{
                            p: 2,
                            maxHeight: isExpanded ? 400 : 120,
                            overflow: isExpanded ? 'auto' : 'hidden',
                            transition: 'max-height 0.3s ease',
                            '&::-webkit-scrollbar': { width: 6 },
                            '&::-webkit-scrollbar-track': { bgcolor: 'transparent' },
                            '&::-webkit-scrollbar-thumb': {
                              bgcolor: isDark ? alpha('#fff', 0.18) : alpha('#000', 0.18),
                              borderRadius: 3,
                              '&:hover': {
                                bgcolor: isDark ? alpha('#fff', 0.28) : alpha('#000', 0.28),
                              },
                            },
                            scrollbarWidth: 'thin',
                            scrollbarColor: isDark
                              ? `${alpha('#fff', 0.18)} transparent`
                              : `${alpha('#000', 0.18)} transparent`,
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                              fontFamily: '"SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace',
                              fontSize: '0.78rem',
                              lineHeight: 1.7,
                              color: colors.text.primary,
                            }}
                          >
                            {value}
                          </Typography>
                        </Box>
                        {/* Fade gradient for collapsed long content */}
                        {!isExpanded && isLong && (
                          <Box
                            sx={{
                              position: 'absolute',
                              bottom: 0,
                              left: 0,
                              right: 0,
                              height: 48,
                              background: `linear-gradient(transparent, ${isDark ? 'rgba(30,30,46,0.95)' : 'rgba(245,245,245,0.95)'})`,
                              display: 'flex',
                              alignItems: 'flex-end',
                              justifyContent: 'center',
                              pb: 1,
                            }}
                          >
                            <Typography
                              variant="caption"
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 0.5,
                                color: theme.palette.primary.main,
                                fontSize: '0.7rem',
                                fontWeight: 500,
                              }}
                            >
                              <Icon icon={chevronDownIcon} width={14} height={14} />
                              Show all ({lineCount} lines)
                            </Typography>
                          </Box>
                        )}
                        {/* Collapse button when expanded */}
                        {isExpanded && isLong && (
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'center',
                              py: 0.75,
                              borderTop: '1px solid',
                              borderColor: isDark ? alpha('#fff', 0.06) : alpha('#000', 0.06),
                            }}
                          >
                            <Typography
                              variant="caption"
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 0.5,
                                color: theme.palette.primary.main,
                                fontSize: '0.7rem',
                                fontWeight: 500,
                              }}
                            >
                              <Icon icon={chevronUpIcon} width={14} height={14} />
                              Show less
                            </Typography>
                          </Box>
                        )}
                      </Box>
                    ) : (
                      <Box
                        onClick={() => setEditingField(fieldKey)}
                        sx={{
                          p: 2,
                          bgcolor: isDark ? alpha('#1e1e2e', 0.4) : alpha(theme.palette.grey[100], 0.5),
                          borderRadius: 1.5,
                          border: '1px dashed',
                          borderColor: isDark ? alpha('#fff', 0.12) : alpha('#000', 0.12),
                          cursor: 'pointer',
                          transition: 'all 0.2s',
                          '&:hover': {
                            borderColor: theme.palette.primary.main,
                            bgcolor: isDark ? alpha('#1e1e2e', 0.6) : alpha(theme.palette.grey[100], 0.7),
                          },
                        }}
                      >
                        <Typography variant="body2" sx={{ color: colors.text.muted, fontStyle: 'italic', fontSize: '0.8rem' }}>
                          Click to add a system prompt...
                        </Typography>
                      </Box>
                    )}
                  </Box>
                );
              })()}

              {/* Instructions - Collapsible preview card */}
              {(() => {
                const fieldKey = 'instructions';
                const value = instructionsValue;
                const setValue = setInstructionsValue;
                const isEditing = editingField === fieldKey;
                const isExpanded = expandedFields.has(fieldKey);
                const hasContent = value.trim().length > 0;
                const lineCount = value.split('\n').length;
                const charCount = value.length;
                const isLong = lineCount > 5 || charCount > 300;
                const previewBg = isDark ? alpha('#1e1e2e', 0.7) : alpha(theme.palette.grey[100], 0.9);

                return (
                  <Box sx={{ mb: 3 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 500 }}>
                          Instructions
                        </Typography>
                        {hasContent && (
                          <Chip
                            label={`${lineCount} ${lineCount === 1 ? 'line' : 'lines'} · ${charCount} chars`}
                            size="small"
                            sx={{
                              height: 20,
                              fontSize: '0.65rem',
                              bgcolor: isDark ? alpha('#fff', 0.12) : alpha('#000', 0.06),
                              color: isDark ? alpha('#fff', 0.7) : alpha('#000', 0.55),
                              '& .MuiChip-label': { px: 1 },
                            }}
                          />
                        )}
                      </Box>
                      <Button
                        size="small"
                        onClick={() => setEditingField(isEditing ? null : fieldKey)}
                        startIcon={<Icon icon={isEditing ? checkIcon : pencilIcon} width={13} height={13} />}
                        sx={{
                          fontSize: '0.7rem',
                          textTransform: 'none',
                          color: isEditing ? theme.palette.success.main : colors.text.muted,
                          minWidth: 0,
                          px: 1,
                          '&:hover': { bgcolor: isDark ? alpha('#fff', 0.05) : alpha('#000', 0.04) },
                        }}
                      >
                        {isEditing ? 'Done' : 'Edit'}
                      </Button>
                    </Box>

                    {isEditing ? (
                      <TextField
                        fullWidth
                        multiline
                        minRows={3}
                        maxRows={16}
                        value={value}
                        onChange={(e) => setValue(e.target.value)}
                        autoFocus
                        placeholder="Optional additional instructions for the agent (e.g. always respond in French, use bullet points, focus on concise answers)..."
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            borderRadius: 1,
                            backgroundColor: isDark
                              ? alpha('#2a2a2a', 0.5)
                              : alpha(theme.palette.background.paper, 0.9),
                            color: colors.text.primary,
                            fontFamily: '"SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace',
                            fontSize: '0.8rem',
                            lineHeight: 1.7,
                            '& fieldset': { borderColor: colors.border.focus, borderWidth: 1.5 },
                            '&:hover fieldset': { borderColor: colors.border.focus },
                            '&.Mui-focused fieldset': { borderColor: theme.palette.primary.main, borderWidth: 1.5 },
                          },
                          '& .MuiInputBase-input': {
                            color: colors.text.primary,
                            '&::placeholder': { color: colors.text.muted, opacity: 1 },
                          },
                        }}
                      />
                    ) : hasContent ? (
                      <Box
                        sx={{
                          position: 'relative',
                          bgcolor: previewBg,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: isDark ? alpha('#fff', 0.08) : alpha('#000', 0.08),
                          overflow: 'hidden',
                          cursor: 'pointer',
                          transition: 'border-color 0.2s',
                          '&:hover': {
                            borderColor: isDark ? alpha('#fff', 0.15) : alpha('#000', 0.15),
                          },
                        }}
                        onClick={() => {
                          if (isLong) {
                            setExpandedFields((prev) => {
                              const next = new Set(prev);
                              if (next.has(fieldKey)) next.delete(fieldKey);
                              else next.add(fieldKey);
                              return next;
                            });
                          }
                        }}
                      >
                        <Box
                          sx={{
                            p: 2,
                            maxHeight: isExpanded ? 400 : 120,
                            overflow: isExpanded ? 'auto' : 'hidden',
                            transition: 'max-height 0.3s ease',
                            '&::-webkit-scrollbar': { width: 6 },
                            '&::-webkit-scrollbar-track': { bgcolor: 'transparent' },
                            '&::-webkit-scrollbar-thumb': {
                              bgcolor: isDark ? alpha('#fff', 0.18) : alpha('#000', 0.18),
                              borderRadius: 3,
                              '&:hover': {
                                bgcolor: isDark ? alpha('#fff', 0.28) : alpha('#000', 0.28),
                              },
                            },
                            scrollbarWidth: 'thin',
                            scrollbarColor: isDark
                              ? `${alpha('#fff', 0.18)} transparent`
                              : `${alpha('#000', 0.18)} transparent`,
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                              fontFamily: '"SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace',
                              fontSize: '0.78rem',
                              lineHeight: 1.7,
                              color: colors.text.primary,
                            }}
                          >
                            {value}
                          </Typography>
                        </Box>
                        {!isExpanded && isLong && (
                          <Box
                            sx={{
                              position: 'absolute',
                              bottom: 0,
                              left: 0,
                              right: 0,
                              height: 48,
                              background: `linear-gradient(transparent, ${isDark ? 'rgba(30,30,46,0.95)' : 'rgba(245,245,245,0.95)'})`,
                              display: 'flex',
                              alignItems: 'flex-end',
                              justifyContent: 'center',
                              pb: 1,
                            }}
                          >
                            <Typography
                              variant="caption"
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 0.5,
                                color: theme.palette.primary.main,
                                fontSize: '0.7rem',
                                fontWeight: 500,
                              }}
                            >
                              <Icon icon={chevronDownIcon} width={14} height={14} />
                              Show all ({lineCount} lines)
                            </Typography>
                          </Box>
                        )}
                        {isExpanded && isLong && (
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'center',
                              py: 0.75,
                              borderTop: '1px solid',
                              borderColor: isDark ? alpha('#fff', 0.06) : alpha('#000', 0.06),
                            }}
                          >
                            <Typography
                              variant="caption"
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 0.5,
                                color: theme.palette.primary.main,
                                fontSize: '0.7rem',
                                fontWeight: 500,
                              }}
                            >
                              <Icon icon={chevronUpIcon} width={14} height={14} />
                              Show less
                            </Typography>
                          </Box>
                        )}
                      </Box>
                    ) : (
                      <Box
                        onClick={() => setEditingField(fieldKey)}
                        sx={{
                          p: 2,
                          bgcolor: isDark ? alpha('#1e1e2e', 0.4) : alpha(theme.palette.grey[100], 0.5),
                          borderRadius: 1.5,
                          border: '1px dashed',
                          borderColor: isDark ? alpha('#fff', 0.12) : alpha('#000', 0.12),
                          cursor: 'pointer',
                          transition: 'all 0.2s',
                          '&:hover': {
                            borderColor: theme.palette.primary.main,
                            bgcolor: isDark ? alpha('#1e1e2e', 0.6) : alpha(theme.palette.grey[100], 0.7),
                          },
                        }}
                      >
                        <Typography variant="body2" sx={{ color: colors.text.muted, fontStyle: 'italic', fontSize: '0.8rem' }}>
                          Click to add instructions...
                        </Typography>
                      </Box>
                    )}
                  </Box>
                );
              })()}

              {/* Starting Message - Collapsible preview card */}
              {(() => {
                const fieldKey = 'startMessage';
                const value = startMessageValue;
                const setValue = setStartMessageValue;
                const isEditing = editingField === fieldKey;
                const isExpanded = expandedFields.has(fieldKey);
                const hasContent = value.trim().length > 0;
                const lineCount = value.split('\n').length;
                const charCount = value.length;
                const isLong = lineCount > 5 || charCount > 300;
                const previewBg = isDark ? alpha('#1e1e2e', 0.7) : alpha(theme.palette.grey[100], 0.9);

                return (
                  <Box>
                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 500 }}>
                          Starting Message
                        </Typography>
                        {hasContent && (
                          <Chip
                            label={`${lineCount} ${lineCount === 1 ? 'line' : 'lines'} · ${charCount} chars`}
                            size="small"
                            sx={{
                              height: 20,
                              fontSize: '0.65rem',
                              bgcolor: isDark ? alpha('#fff', 0.12) : alpha('#000', 0.06),
                              color: isDark ? alpha('#fff', 0.7) : alpha('#000', 0.55),
                              '& .MuiChip-label': { px: 1 },
                            }}
                          />
                        )}
                      </Box>
                      <Button
                        size="small"
                        onClick={() => setEditingField(isEditing ? null : fieldKey)}
                        startIcon={<Icon icon={isEditing ? checkIcon : pencilIcon} width={13} height={13} />}
                        sx={{
                          fontSize: '0.7rem',
                          textTransform: 'none',
                          color: isEditing ? theme.palette.success.main : colors.text.muted,
                          minWidth: 0,
                          px: 1,
                          '&:hover': { bgcolor: isDark ? alpha('#fff', 0.05) : alpha('#000', 0.04) },
                        }}
                      >
                        {isEditing ? 'Done' : 'Edit'}
                      </Button>
                    </Box>

                    {isEditing ? (
                      <TextField
                        fullWidth
                        multiline
                        minRows={2}
                        maxRows={10}
                        value={value}
                        onChange={(e) => setValue(e.target.value)}
                        autoFocus
                        placeholder="Enter the agent's greeting message to users..."
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            borderRadius: 1,
                            backgroundColor: isDark
                              ? alpha('#2a2a2a', 0.5)
                              : alpha(theme.palette.background.paper, 0.9),
                            color: colors.text.primary,
                            fontFamily: '"SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace',
                            fontSize: '0.8rem',
                            lineHeight: 1.7,
                            '& fieldset': { borderColor: colors.border.focus, borderWidth: 1.5 },
                            '&:hover fieldset': { borderColor: colors.border.focus },
                            '&.Mui-focused fieldset': { borderColor: theme.palette.primary.main, borderWidth: 1.5 },
                          },
                          '& .MuiInputBase-input': {
                            color: colors.text.primary,
                            '&::placeholder': { color: colors.text.muted, opacity: 1 },
                          },
                        }}
                      />
                    ) : hasContent ? (
                      <Box
                        sx={{
                          position: 'relative',
                          bgcolor: previewBg,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: isDark ? alpha('#fff', 0.08) : alpha('#000', 0.08),
                          overflow: 'hidden',
                          cursor: 'pointer',
                          transition: 'border-color 0.2s',
                          '&:hover': {
                            borderColor: isDark ? alpha('#fff', 0.15) : alpha('#000', 0.15),
                          },
                        }}
                        onClick={() => {
                          if (isLong) {
                            setExpandedFields((prev) => {
                              const next = new Set(prev);
                              if (next.has(fieldKey)) next.delete(fieldKey);
                              else next.add(fieldKey);
                              return next;
                            });
                          }
                        }}
                      >
                        <Box
                          sx={{
                            p: 2,
                            maxHeight: isExpanded ? 400 : 120,
                            overflow: isExpanded ? 'auto' : 'hidden',
                            transition: 'max-height 0.3s ease',
                            '&::-webkit-scrollbar': { width: 6 },
                            '&::-webkit-scrollbar-track': { bgcolor: 'transparent' },
                            '&::-webkit-scrollbar-thumb': {
                              bgcolor: isDark ? alpha('#fff', 0.18) : alpha('#000', 0.18),
                              borderRadius: 3,
                              '&:hover': {
                                bgcolor: isDark ? alpha('#fff', 0.28) : alpha('#000', 0.28),
                              },
                            },
                            scrollbarWidth: 'thin',
                            scrollbarColor: isDark
                              ? `${alpha('#fff', 0.18)} transparent`
                              : `${alpha('#000', 0.18)} transparent`,
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                              fontFamily: '"SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace',
                              fontSize: '0.78rem',
                              lineHeight: 1.7,
                              color: colors.text.primary,
                            }}
                          >
                            {value}
                          </Typography>
                        </Box>
                        {!isExpanded && isLong && (
                          <Box
                            sx={{
                              position: 'absolute',
                              bottom: 0,
                              left: 0,
                              right: 0,
                              height: 48,
                              background: `linear-gradient(transparent, ${isDark ? 'rgba(30,30,46,0.95)' : 'rgba(245,245,245,0.95)'})`,
                              display: 'flex',
                              alignItems: 'flex-end',
                              justifyContent: 'center',
                              pb: 1,
                            }}
                          >
                            <Typography
                              variant="caption"
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 0.5,
                                color: theme.palette.primary.main,
                                fontSize: '0.7rem',
                                fontWeight: 500,
                              }}
                            >
                              <Icon icon={chevronDownIcon} width={14} height={14} />
                              Show all ({lineCount} lines)
                            </Typography>
                          </Box>
                        )}
                        {isExpanded && isLong && (
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'center',
                              py: 0.75,
                              borderTop: '1px solid',
                              borderColor: isDark ? alpha('#fff', 0.06) : alpha('#000', 0.06),
                            }}
                          >
                            <Typography
                              variant="caption"
                              sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 0.5,
                                color: theme.palette.primary.main,
                                fontSize: '0.7rem',
                                fontWeight: 500,
                              }}
                            >
                              <Icon icon={chevronUpIcon} width={14} height={14} />
                              Show less
                            </Typography>
                          </Box>
                        )}
                      </Box>
                    ) : (
                      <Box
                        onClick={() => setEditingField(fieldKey)}
                        sx={{
                          p: 2,
                          bgcolor: isDark ? alpha('#1e1e2e', 0.4) : alpha(theme.palette.grey[100], 0.5),
                          borderRadius: 1.5,
                          border: '1px dashed',
                          borderColor: isDark ? alpha('#fff', 0.12) : alpha('#000', 0.12),
                          cursor: 'pointer',
                          transition: 'all 0.2s',
                          '&:hover': {
                            borderColor: theme.palette.primary.main,
                            bgcolor: isDark ? alpha('#1e1e2e', 0.6) : alpha(theme.palette.grey[100], 0.7),
                          },
                        }}
                      >
                        <Typography variant="body2" sx={{ color: colors.text.muted, fontStyle: 'italic', fontSize: '0.8rem' }}>
                          Click to add a starting message...
                        </Typography>
                      </Box>
                    )}
                  </Box>
                );
              })()}
            </Box>
          </DialogContent>

          <DialogActions
            sx={{
              p: 2.5,
              borderTop: '1px solid',
              borderColor: theme.palette.divider,
              bgcolor: alpha(theme.palette.background.default, 0.5),
            }}
          >
            <Button
              variant="text"
              onClick={handlePromptDialogCancel}
              sx={{
                color: theme.palette.text.secondary,
                fontWeight: 500,
                '&:hover': {
                  backgroundColor: alpha(theme.palette.divider, 0.8),
                },
              }}
            >
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handlePromptDialogSave}
              sx={{
                bgcolor: theme.palette.primary.main,
                boxShadow: 'none',
                fontWeight: 500,
                '&:hover': {
                  bgcolor: theme.palette.primary.dark,
                  boxShadow: 'none',
                },
                px: 3,
              }}
            >
              Save Changes
            </Button>
          </DialogActions>
        </Dialog>
      </Card>
    );
  }

  // Enhanced standard nodes - dynamic sizing based on type
  const getNodeDimensions = () => {
    if (data.type.startsWith('tool-group-')) {
      // Tool group nodes need more space for bundle info
      return { width: 320, minHeight: 190 };
    }
    if (data.type.startsWith('tool-')) {
      // Individual tools need space for descriptions
      return { width: 300, minHeight: 180 };
    }
    if (data.type.startsWith('app-')) {
      // App memory nodes need space for app info
      return { width: 300, minHeight: 175 };
    }
    if (data.type === 'kb-group') {
      // Knowledge base group nodes
      return { width: 310, minHeight: 185 };
    }
    if (data.type.startsWith('kb-')) {
      // Individual KB nodes need extra space
      return { width: 290, minHeight: 170 };
    }
    if (data.type.startsWith('llm-')) {
      // LLM nodes need space for model details
      return { width: 285, minHeight: 165 };
    }
    // Default size for other nodes
    return { width: 280, minHeight: 160 };
  };

  const { width, minHeight } = getNodeDimensions();

  // Use specialized ToolsetNode for toolset nodes
  // Check both category and type to ensure toolset nodes are always rendered correctly
  if (data.type.startsWith('toolset-') || data.category === 'toolset') {
    return <ToolsetNode data={data} selected={selected} onDelete={onDelete} />;
  }

  return (
    <Card
      sx={{
        width,
        minHeight,
        border: selected ? `2px solid ${colors.border.focus}` : `2px solid ${colors.border.main}`,
        borderRadius: 1.5,
        backgroundColor: colors.background.card,
        boxShadow: selected 
          ? (isDark ? `0 4px 12px rgba(0, 0, 0, 0.3)` : `0 4px 12px rgba(0, 0, 0, 0.1)`)
          : isDark 
            ? `0 2px 8px rgba(0, 0, 0, 0.15)`
            : `0 2px 8px rgba(0, 0, 0, 0.08)`,
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        position: 'relative',
        overflow: 'visible',
        '&:hover': {
          borderColor: colors.border.focus,
          borderWidth: '2.5px',
          boxShadow: isDark 
            ? `0 4px 12px rgba(0, 0, 0, 0.25)`
            : `0 4px 12px rgba(0, 0, 0, 0.12)`,
        },
      }}
      onClick={(e) => {
        // Prevent rapid clicks
        const now = Date.now();
        if (now - lastClickTime < 300) return;
        setLastClickTime(now);
        e.stopPropagation();
      }}
    >
      {/* Minimal Header */}
      <Box
        sx={{
          p: 2,
          borderBottom: `1px solid ${colors.border.main}`,
          backgroundColor: isDark 
            ? alpha('#1f1f1f', 0.5) 
            : alpha(theme.palette.background.default, 0.5),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            {/* Icon - uses modular NodeIcon component */}
            <NodeIcon data={data} toolIcon={toolIcon} size={24} />

            <Typography
              variant="h6"
              sx={{
                fontWeight: 700,
                fontSize: '0.95rem',
                color: colors.text.primary,
                lineHeight: 1.2,
                letterSpacing: '-0.025em',
              }}
            >
              {data.type.startsWith('llm-') 
                ? normalizeDisplayName(getModelDisplayName(data.config) || data.label)
                : normalizeDisplayName(data.label)}
            </Typography>
          </Box>
          {onDelete && !data?.type?.startsWith('user-input') && !data?.type?.startsWith('chat-response') && (
            <IconButton
              size="small"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                e.nativeEvent.stopImmediatePropagation();
                // Use the ReactFlow node id (reactFlowId) which is the authoritative node identifier,
                // falling back to data.id for backward compatibility
                onDelete(reactFlowId || data.id);
              }}
              sx={{
                width: 30,
                height: 30,
                backgroundColor: alpha(theme.palette.error.main, 0.1),
                border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
                '&:hover': {
                  backgroundColor: alpha(theme.palette.error.main, 0.2),
                  transform: 'scale(1.05)',
                  color: theme.palette.error.main,
                },
                transition: 'all 0.2s ease',
              }}
            >
              <Icon icon={deleteIcon} width={20} height={20} />
            </IconButton>
          )}
        </Box>
        {data.description && (
          <Typography
            variant="body2"
            sx={{
              color: colors.text.secondary,
              fontSize: '0.75rem',
              lineHeight: 1.4,
              mt: 1,
              fontWeight: 500,
              wordBreak: 'break-word',
              whiteSpace: 'normal',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              display: '-webkit-box',
              WebkitLineClamp: 3,
              WebkitBoxOrient: 'vertical',
            }}
          >
            {data.description}
          </Typography>
        )}
      </Box>

      {/* Content */}
      <Box sx={{ p: 2.5 }}>
        {/* Tool Group Section for grouped tool nodes */}
        {data.type.startsWith('tool-group-') && (
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.75rem',
                mb: 1.5,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <Icon icon={packageIcon} width={12} height={12} style={{ color: colors.info }} />
              Tool Bundle
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                minHeight: 45,
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 600,
                  lineHeight: 1.3,
                }}
              >
                {data.config?.appDisplayName || data.config?.appName || 'Tool Group'}
              </Typography>
              <Typography
                sx={{
                  fontSize: '0.65rem',
                  color: colors.text.secondary,
                  fontWeight: 500,
                  mt: 0.5,
                }}
              >
                {data.config?.tools?.length || 0} tools available
              </Typography>
            </Box>
          </Box>
        )}

        {/* Actions Section for individual tools */}
        {data.type.startsWith('tool-') && !data.type.startsWith('tool-group-') && (
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.75rem',
                mb: 1.5,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <Icon icon={cogIcon} width={12} height={12} style={{ color: colors.info }} />
              Tool Details
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                minHeight: 45,
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 600,
                  lineHeight: 1.3,
                  wordBreak: 'break-word',
                  whiteSpace: 'normal',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                }}
              >
                {normalizeDisplayName(data.label)}
              </Typography>
              {data.config?.appName && (
                <Typography
                  sx={{
                    fontSize: '0.65rem',
                    color: colors.text.secondary,
                    fontWeight: 500,
                    mt: 0.5,
                    textTransform: 'capitalize',
                  }}
                >
                  {data.config.appName.replace(/_/g, ' ')}
                </Typography>
              )}
            </Box>
          </Box>
        )}

        {/* Connector Group Section for connector group nodes */}
        {data.type.startsWith('connector-group-') && (
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.75rem',
                mb: 1.5,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <img src={data.config?.iconPath} alt={data.label} width={12} height={12} />
              Connector
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                minHeight: 45,
                position: 'relative',
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 600,
                  lineHeight: 1.3,
                }}
              >
                {data.config?.name || data.label}
              </Typography>
              {/* Show connected tools */}
              {(() => {
                // Flow: Tool (source) → Connector Instance (target)
                const connectedTools = storeEdges
                  .filter((e: any) => {
                    const sourceNode = storeNodes.find((n: any) => n.id === e.source);
                    const sourceType = sourceNode?.data?.type;
                    return (
                      e.target === data.id &&
                      typeof sourceType === 'string' &&
                      sourceType.startsWith('tool-')
                    );
                  })
                  .map((e: any) => {
                    const toolNode = storeNodes.find((n: any) => n.id === e.source);
                    return toolNode?.data as any;
                  })
                  .filter(Boolean);

                if (connectedTools.length > 0) {
                  return (
                    <Box sx={{ mt: 1.5, display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                      {connectedTools.slice(0, 3).map((tool: any, index: number) => (
                        <Chip
                          key={index}
                          label={
                            tool.label.length > 12 ? `${tool.label.slice(0, 12)}...` : tool.label
                          }
                          size="small"
                          sx={{
                            height: 20,
                            fontSize: '0.65rem',
                            fontWeight: 600,
                            backgroundColor: isDark
                              ? alpha(colors.info, 0.9)
                              : alpha(colors.info, 0.1),
                            color: colors.info,
                            border: `1px solid ${alpha(colors.info, 0.3)}`,
                            '&:hover': {
                              backgroundColor: isDark
                                ? alpha(colors.info, 0.2)
                                : alpha(colors.info, 0.2),
                              borderColor: colors.info,
                              transform: 'scale(1.05)',
                            },
                            transition: 'all 0.2s ease',
                            '& .MuiChip-label': { px: 0.75 },
                          }}
                        />
                      ))}
                      {connectedTools.length > 3 && (
                        <Chip
                          label={`+${connectedTools.length - 3}`}
                          size="small"
                          sx={{
                            height: 20,
                            fontSize: '0.65rem',
                            fontWeight: 600,
                            backgroundColor: isDark
                              ? alpha(colors.text.secondary, 0.1)
                              : alpha(colors.text.secondary, 0.1),
                            color: colors.text.secondary,
                            border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                            '&:hover': {
                              backgroundColor: isDark
                                ? alpha(colors.text.secondary, 0.2)
                                : alpha(colors.text.secondary, 0.2),
                            },
                            transition: 'all 0.2s ease',
                            '& .MuiChip-label': { px: 0.75 },
                          }}
                        />
                      )}
                    </Box>
                  );
                }
                return (
                  <Typography
                    sx={{
                      fontSize: '0.65rem',
                      color: colors.text.secondary,
                      fontWeight: 500,
                      mt: 0.5,
                      fontStyle: 'italic',
                    }}
                  >
                    No tools connected
                  </Typography>
                );
              })()}
            </Box>
          </Box>
        )}

        {/* App Memory Group Section */}
        {data.type === 'app-group' && (
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.75rem',
                mb: 1.5,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <Icon icon={cloudIcon} width={12} height={12} style={{ color: colors.info }} />
              Apps
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  mb: 1.5,
                }}
              >
                <Typography
                  sx={{
                    fontSize: '0.75rem',
                    color: colors.text.primary,
                    fontWeight: 600,
                    lineHeight: 1.3,
                  }}
                >
                  Connected Applications
                </Typography>
                <Chip
                  label={`${data.config?.apps?.length || 0}`}
                  size="small"
                  sx={{
                    height: 20,
                    fontSize: '0.65rem',
                    fontWeight: 600,
                    backgroundColor: alpha(colors.info, 0.15),
                    color: colors.info,
                    '& .MuiChip-label': {
                      px: 1,
                    },
                  }}
                />
              </Box>

              {data.config?.apps && data.config.apps.length > 0 ? (
                <Box>
                  {data.config.apps.slice(0, 3).map((app: any, index: number) => (
                    <Box
                      key={index}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1.5,
                        mt: index > 0 ? 1.5 : 0,
                      }}
                    >
                      <Box
                        sx={{
                          width: 24,
                          height: 24,
                          borderRadius: 1.5,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          backgroundColor: alpha(colors.info, 0.1),
                          boxShadow: `0 2px 4px ${alpha(colors.info, 0.2)}`,
                        }}
                      >
                        <img
                          src={
                            app.iconPath ||
                            `/assets/icons/connectors/${(app.type || app.name || '').replace(/\s+/g, '').toLowerCase()}.svg`
                          }
                          alt={app.name || app.type}
                          width={14}
                          height={14}
                          style={{
                            objectFit: 'contain',
                          }}
                          onError={(e) => {
                            e.currentTarget.src = '/assets/icons/connectors/collections-gray.svg';
                          }}
                        />
                      </Box>
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography
                          sx={{
                            fontSize: '0.85rem',
                            fontWeight: 600,
                            color: colors.text.primary,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {normalizeDisplayName(
                            app.displayName || app.name || app.type || 'Unknown'
                          )}
                        </Typography>
                        <Typography
                          sx={{
                            fontSize: '0.7rem',
                            color: colors.text.secondary,
                            fontWeight: 500,
                            mt: 0.25,
                          }}
                        >
                          {app.scope === 'team' ? 'Team' : 'Personal'} • {app.type || 'App'}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                  {data.config.apps.length > 3 && (
                    <Chip
                      label={`+${data.config.apps.length - 3} more`}
                      size="small"
                      sx={{
                        height: 22,
                        fontSize: '0.7rem',
                        fontWeight: 600,
                        backgroundColor: isDark
                          ? alpha('#ffffff', 0.2)
                          : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        mt: 1.5,
                        '&:hover': {
                          backgroundColor: isDark
                            ? alpha('#ffffff', 0.2)
                            : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          borderColor: isDark
                            ? alpha(colors.text.secondary, 0.2)
                            : alpha(colors.text.secondary, 0.2),
                        },
                        transition: 'all 0.2s ease',
                      }}
                    />
                  )}
                </Box>
              ) : (
                <Typography
                  sx={{
                    fontSize: '0.7rem',
                    color: colors.text.secondary,
                    fontStyle: 'italic',
                    mt: 1,
                  }}
                >
                  No applications connected
                </Typography>
              )}
            </Box>
          </Box>
        )}

        {/* App Memory Section for app memory nodes */}
        {data.type.startsWith('app-') && !data.type.startsWith('app-group') && (
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.75rem',
                mb: 1.5,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <img src={data.config?.iconPath} alt={data.label} width={12} height={12} />
              App
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                minHeight: 45,
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 600,
                  lineHeight: 1.3,
                }}
              >
                {data.config?.appDisplayName || data.label}
              </Typography>
            </Box>
          </Box>
        )}

        {/* Knowledge Base Group Section */}
        {data.type === 'kb-group' && (
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.75rem',
                mb: 1.5,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <Icon icon={collectionIcon} width={12} height={12} style={{ color: colors.warning }} />
              Collection Group
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                minHeight: 45,
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 600,
                  lineHeight: 1.3,
                }}
              >
                All Collections
              </Typography>
              <Typography
                sx={{
                  fontSize: '0.65rem',
                  color: colors.text.secondary,
                  fontWeight: 500,
                  mt: 0.5,
                }}
              >
                {data.config?.knowledgeBases?.length || 0} collection(s) available
              </Typography>
            </Box>
          </Box>
        )}

        {/* Memory/KB Section for individual memory nodes */}
        {data.type.startsWith('kb-') && !data.type.startsWith('kb-group') && (
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.75rem',
                mb: 1.5,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <Icon icon={collectionIcon} width={12} height={12} style={{ color: colors.warning }} />
              Collection
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                minHeight: 45,
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 600,
                  lineHeight: 1.3,
                  wordBreak: 'break-word',
                  whiteSpace: 'normal',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                }}
              >
                {data.config?.kbName || data.config?.name || data.label}
              </Typography>
            </Box>
          </Box>
        )}

        {/* LLM Details for LLM nodes */}
        {data.type.startsWith('llm-') && (
          <Box sx={{ mb: 2 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color: colors.text.primary,
                fontSize: '0.75rem',
                mb: 1.5,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <Icon icon={brainIcon} width={12} height={12} style={{ color: colors.primary }} />
              Model Details
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `2px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                minHeight: 45,
                '&:hover': {
                  backgroundColor: colors.background.hover,
                  borderColor: colors.border.main,
                },
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  color: colors.text.primary,
                  fontWeight: 600,
                  lineHeight: 1.3,
                }}
              >
                {formattedProvider(data.config?.provider || 'AI Provider')}
              </Typography>
              {data.config?.modelName && (
                <Typography
                  sx={{
                    fontSize: '0.65rem',
                    color: colors.text.secondary,
                    fontWeight: 500,
                    mt: 0.5,
                  }}
                >
                  {getModelDisplayName(data.config) || data.label}
                </Typography>
              )}
            </Box>
          </Box>
        )}

      </Box>

      {/* Handles - uses modular NodeHandles component */}
      <NodeHandles data={data} />
    </Card>
  );
};

export default FlowNode;

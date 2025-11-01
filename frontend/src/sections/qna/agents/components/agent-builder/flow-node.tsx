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
} from '@mui/material';
import { Icon } from '@iconify/react';
import brainIcon from '@iconify-icons/mdi/brain';
import toolIcon from '@iconify-icons/mdi/tools';
import databaseIcon from '@iconify-icons/mdi/database';
import closeIcon from '@iconify-icons/eva/close-outline';
import scriptIcon from '@iconify-icons/mdi/script-text';
import pencilIcon from '@iconify-icons/mdi/pencil';
import messageTextIcon from '@iconify-icons/mdi/message-text';
import informationIcon from '@iconify-icons/mdi/information';
import packageIcon from '@iconify-icons/mdi/package-variant';
import cogIcon from '@iconify-icons/mdi/cog';
import cloudIcon from '@iconify-icons/mdi/cloud-outline';
import tuneIcon from '@iconify-icons/mdi/tune';
import { formattedProvider, normalizeDisplayName } from '../../utils/agent';

interface FlowNodeData extends Record<string, unknown> {
  id: string;
  type: string;
  label: string;
  config: Record<string, any>;
  description?: string;
  icon?: any;
  inputs?: string[];
  outputs?: string[];
  isConfigured?: boolean;
}

interface FlowNodeProps {
  data: FlowNodeData;
  selected: boolean;
}

const FlowNode: React.FC<FlowNodeProps> = ({ data, selected }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const storeNodes = useStore((s) => s.nodes);
  const storeEdges = useStore((s) => s.edges);
  const { setNodes } = useReactFlow();
  const [lastClickTime, setLastClickTime] = useState(0);

  // Editing state
  const [promptDialogOpen, setPromptDialogOpen] = useState(false);
  const [systemPromptValue, setSystemPromptValue] = useState(
    data.config?.systemPrompt || 'You are a helpful assistant.'
  );
  const [startMessageValue, setStartMessageValue] = useState(
    data.config?.startMessage || 'Hello! How can I help you today?'
  );
  const [descriptionValue, setDescriptionValue] = useState(
    data.config?.description || 'AI agent for task automation and assistance'
  );

  const updateNodeConfig = useCallback(
    (key: string, value: string) => {
      setNodes((nodes: any[]) =>
        nodes.map((node: any) =>
          node.id === data.id
            ? {
                ...node,
                data: {
                  ...node.data,
                  config: {
                    ...node.data.config,
                    [key]: value,
                  },
                },
              }
            : node
        )
      );
    },
    [data.id, setNodes]
  );

  const handlePromptDialogOpen = useCallback(() => {
    setSystemPromptValue(data.config?.systemPrompt || 'You are a helpful assistant.');
    setStartMessageValue(data.config?.startMessage || 'Hello! How can I help you today?');
    setDescriptionValue(data.config?.description || 'AI agent for task automation and assistance');
    setPromptDialogOpen(true);
  }, [data.config?.systemPrompt, data.config?.startMessage, data.config?.description]);

  const handlePromptDialogSave = useCallback(() => {
    updateNodeConfig('systemPrompt', systemPromptValue);
    updateNodeConfig('startMessage', startMessageValue);
    updateNodeConfig('description', descriptionValue);
    setPromptDialogOpen(false);
  }, [systemPromptValue, startMessageValue, descriptionValue, updateNodeConfig]);

  const handlePromptDialogCancel = useCallback(() => {
    setSystemPromptValue(data.config?.systemPrompt || 'You are a helpful assistant.');
    setStartMessageValue(data.config?.startMessage || 'Hello! How can I help you today?');
    setDescriptionValue(data.config?.description || 'AI agent for task automation and assistance');
    setPromptDialogOpen(false);
  }, [data.config?.systemPrompt, data.config?.startMessage, data.config?.description]);


  // Sync local state with node data changes
  useEffect(() => {
    setSystemPromptValue(data.config?.systemPrompt || 'You are a helpful assistant.');
  }, [data.config?.systemPrompt]);

  useEffect(() => {
    setStartMessageValue(data.config?.startMessage || 'Hello! How can I help you today?');
  }, [data.config?.startMessage]);

  useEffect(() => {
    setDescriptionValue(data.config?.description || 'AI agent for task automation and assistance');
  }, [data.config?.description]);

  const connectedNodesByHandle = React.useMemo(() => {
    if (data.type !== 'agent-core') return {} as Record<string, any[]>;
    const incoming = storeEdges.filter((e) => e.target === data.id);
    const map: Record<string, any[]> = { input: [], actions: [], knowledge: [], llms: [] };
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

  // Professional color scheme
  const colors = {
    primary: isDark ? '#6366f1' : '#4f46e5',
    secondary: isDark ? '#8b5cf6' : '#7c3aed',
    success: isDark ? '#10b981' : '#059669',
    warning: isDark ? '#f59e0b' : '#d97706',
    info: isDark ? '#06b6d4' : '#0891b2',
    background: {
      card: isDark ? '#1e1e1e' : '#ffffff',
      section: isDark ? '#2a2a2a' : '#f8fafc',
      hover: isDark ? '#333333' : '#f1f5f9',
    },
    border: {
      main: isDark ? '#3a3a3a' : '#e2e8f0',
      subtle: isDark ? '#2a2a2a' : '#f1f5f9',
      focus: isDark ? '#6366f1' : '#4f46e5',
    },
    text: {
      primary: isDark ? '#f8fafc' : '#0f172a',
      secondary: isDark ? '#cbd5e1' : '#64748b',
      muted: isDark ? '#94a3b8' : '#94a3b8',
    },
  };

  // Enhanced Agent node
  // Enhanced Agent node
  if (data.type === 'agent-core') {
    return (
      <Card
        sx={{
          width: 420, // Increased from 380 for better width
          minHeight: 650, // Balanced height - not too compact, not too tall
          border: selected ? `2px solid ${colors.primary}` : `1px solid ${colors.border.main}`,
          borderRadius: 3,
          backgroundColor: colors.background.card,
          boxShadow: selected
            ? `0 0 0 4px ${alpha(colors.primary, 0.1)}, 0 8px 25px ${alpha(isDark ? '#000' : colors.primary, isDark ? 0.4 : 0.15)}`
            : `0 4px 6px ${alpha(isDark ? '#000' : '#0f172a', isDark ? 0.3 : 0.05)}, 0 1px 3px ${alpha(isDark ? '#000' : '#0f172a', isDark ? 0.2 : 0.1)}`,
          cursor: 'pointer',
          transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
          position: 'relative',
          overflow: 'visible',
          backdropFilter: isDark ? 'blur(10px)' : 'none',
          '&:hover': {
            transform: 'translateY(-2px)',
            boxShadow: selected
              ? `0 0 0 4px ${alpha(colors.primary, 0.15)}, 0 12px 35px ${alpha(isDark ? '#000' : colors.primary, isDark ? 0.5 : 0.2)}`
              : `0 8px 25px ${alpha(isDark ? '#000' : '#0f172a', isDark ? 0.4 : 0.1)}, 0 4px 6px ${alpha(isDark ? '#000' : '#0f172a', isDark ? 0.3 : 0.05)}`,
            borderColor: selected ? colors.primary : colors.border.focus,
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
        {/* Header with gradient */}
        <Box
          sx={{
            p: 2.5, // Keep comfortable padding
            borderBottom: `1px solid ${colors.border.subtle}`,
            background: isDark
              ? `linear-gradient(135deg, ${alpha('#2a2a2a', 0.8)} 0%, ${alpha('#1e1e1e', 0.9)} 100%)`
              : `linear-gradient(135deg, ${alpha('#f8fafc', 0.8)} 0%, ${alpha('#ffffff', 0.9)} 100%)`,
            borderRadius: '12px 12px 0 0',
            position: 'relative',
          }}
        >
          <Box
            sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }} // Keep comfortable margin
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              {' '}
              {/* Keep comfortable gap */}
              <Box
                sx={{
                  width: 32, // Keep comfortable size
                  height: 32, // Keep comfortable size
                  borderRadius: 2,
                  background: `linear-gradient(135deg, ${colors.primary} 0%, ${colors.secondary} 100%)`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  boxShadow: `0 4px 8px ${alpha(colors.primary, 0.3)}`,
                }}
              >
                <Icon icon={brainIcon} width={18} height={18} style={{ color: '#ffffff' }} />{' '}
                {/* Keep comfortable size */}
              </Box>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 700,
                  fontSize: '1.1rem', // Keep comfortable size
                  color: colors.text.primary,
                  letterSpacing: '-0.025em',
                }}
              >
                Agent
              </Typography>
            </Box>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Typography
              variant="body2"
              sx={{
                color: colors.text.secondary,
                fontSize: '0.875rem', // Keep comfortable size
                lineHeight: 1.5, // Keep comfortable line height
                fontWeight: 500,
                flex: 1,
              }}
            >
              Define the agent&apos;s instructions, then enter a task to complete using tools.
            </Typography>
          </Box>
        </Box>

        {/* Agent Configuration Section */}
        <Box
          sx={{
            px: 2.5, // Keep comfortable padding
            py: 2, // Keep comfortable padding
            borderBottom: `1px solid ${colors.border.subtle}`,
            background: isDark
              ? `linear-gradient(135deg, ${alpha('#1a1a1a', 0.5)} 0%, ${alpha('#2a2a2a', 0.3)} 100%)`
              : `linear-gradient(135deg, ${alpha('#f8fafc', 0.8)} 0%, ${alpha('#e2e8f0', 0.3)} 100%)`,
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
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 1.5,
                border: `1px solid ${colors.border.subtle}`,
                minHeight: 60, // Keep comfortable height
                maxHeight: 80, // Keep comfortable height
                overflow: 'auto',
                transition: 'all 0.2s ease',
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem', // Keep comfortable size
                  color: colors.text.primary,
                  fontWeight: 500,
                  lineHeight: 1.4, // Keep comfortable line height
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
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 1.5,
                border: `1px solid ${colors.border.subtle}`,
                minHeight: 40, // Keep comfortable height
                maxHeight: 60, // Keep comfortable height
                overflow: 'auto',
                transition: 'all 0.2s ease',
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem', // Keep comfortable size
                  color: colors.text.primary,
                  fontWeight: 500,
                  lineHeight: 1.4, // Keep comfortable line height
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {data.config?.startMessage || 'Hello! How can I help you today?'}
              </Typography>
            </Box>
          </Box>

          {/* Description */}
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
                  icon={informationIcon}
                  width={12}
                  height={12} // Keep comfortable size
                  style={{ color: colors.info }}
                />
                Description
              </Typography>
            </Box>
            <Box
              sx={{
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 1.5,
                border: `1px solid ${colors.border.subtle}`,
                minHeight: 40, // Keep comfortable height
                maxHeight: 60, // Keep comfortable height
                overflow: 'auto',
                transition: 'all 0.2s ease',
              }}
            >
              <Typography
                sx={{
                  fontSize: '0.75rem', // Keep comfortable size
                  color: colors.text.primary,
                  fontWeight: 500,
                  lineHeight: 1.4, // Keep comfortable line height
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {data.config?.description || 'AI agent for task automation and assistance'}
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
                border: `1px solid ${colors.border.subtle}`,
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
                  left: -7,
                  background: `linear-gradient(135deg, ${colors.primary} 0%, ${colors.secondary} 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.primary, 0.4)}`,
                  zIndex: 10,
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
                          {llmNode.config?.modelName || llmNode.label}
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
                        backgroundColor: isDark ? alpha('#ffffff', 0.2) : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        mt: 1, // Keep comfortable margin
                        '&:hover': {
                          backgroundColor: isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          color: isDark ? colors.text.secondary : colors.text.secondary,
                          borderColor: isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2),
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
                border: `1px solid ${colors.border.subtle}`,
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
                  left: -7,
                  background: `linear-gradient(135deg, ${colors.warning} 0%, #f59e0b 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.warning, 0.4)}`,
                  zIndex: 10,
                }}
              />
              {connectedNodesByHandle.knowledge?.length > 0 ? (
                <Box>
                  {connectedNodesByHandle.knowledge.slice(0, 2).map((knowledgeNode, index) => (
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
                          background: `linear-gradient(135deg, ${colors.warning} 0%, #f59e0b 100%)`,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          boxShadow: `0 2px 4px ${alpha(colors.warning, 0.3)}`,
                        }}
                      >
                        <Icon
                          icon={databaseIcon}
                          width={12}
                          height={12} // Keep comfortable size
                          style={{ color: '#ffffff' }}
                        />
                      </Box>
                      <Box>
                        <Typography
                          sx={{ fontSize: '0.85rem', fontWeight: 600, color: colors.text.primary }} // Keep comfortable size
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
                            ? 'Knowledge Base'
                            : knowledgeNode.type.startsWith('knowledge-hub')
                              ? 'Knowledge Hub'
                              : 'Knowledge'}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                  {connectedNodesByHandle.knowledge.length > 2 && (
                    <Chip
                      label={`+${connectedNodesByHandle.knowledge.length - 2} more`}
                      size="small"
                      sx={{
                        height: 22, // Keep comfortable size
                        fontSize: '0.7rem', // Keep comfortable size
                        fontWeight: 600,
                        backgroundColor: isDark ? alpha('#ffffff', 0.2) : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        mt: 1, // Keep comfortable margin
                        '&:hover': {
                          backgroundColor: isDark ? alpha('#ffffff', 0.2) : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          color: isDark ? colors.text.secondary : colors.text.secondary,
                          borderColor: isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2),
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
              Actions
            </Typography>
            <Box
              sx={{
                p: 1.5, // Keep comfortable padding
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `1px solid ${colors.border.subtle}`,
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
                id="actions"
                style={{
                  top: '50%',
                  left: -7,
                  background: `linear-gradient(135deg, ${colors.info} 0%, #06b6d4 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.info, 0.4)}`,
                  zIndex: 10,
                }}
              />
              {connectedNodesByHandle.actions?.length > 0 ? (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                  {' '}
                  {/* Keep comfortable gap */}
                  {connectedNodesByHandle.actions.slice(0, 3).map((actionNode, index) => (
                    <Chip
                      key={index}
                      label={
                        actionNode.label.length > 12
                          ? `${actionNode.label.slice(0, 12)}...`
                          : actionNode.label
                      }
                      size="small"
                      sx={{
                        height: 24, // Keep comfortable size
                        fontSize: '0.7rem', // Keep comfortable size
                        fontWeight: 600,
                        backgroundColor: isDark ? alpha(colors.info, 0.9) : alpha(colors.info, 0.1),
                        color: colors.info,
                        border: `1px solid ${alpha(colors.info, 0.3)}`,
                        '&:hover': {
                          backgroundColor: isDark ? alpha(colors.info, 0.2) : alpha(colors.info, 0.2),
                          borderColor: colors.info,
                          transform: 'scale(1.05)',
                          boxShadow: `0 2px 8px ${alpha(colors.info, 0.3)}`,
                          color: isDark ? colors.info : colors.info,
                        },
                        transition: 'all 0.2s ease',
                        '& .MuiChip-label': { px: 1 }, // Keep comfortable padding
                      }}
                    />
                  ))}
                  {connectedNodesByHandle.actions.length > 3 && (
                    <Chip
                      label={`+${connectedNodesByHandle.actions.length - 3}`}
                      size="small"
                      sx={{
                        height: 24, // Keep comfortable size
                        fontSize: '0.7rem', // Keep comfortable size
                        fontWeight: 600,
                        backgroundColor: isDark ? alpha(colors.text.secondary, 0.1) : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        '&:hover': {
                            backgroundColor: isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          color: isDark ? colors.text.secondary : colors.text.secondary,
                          borderColor: isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2),
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
                  No actions connected
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
                border: `1px solid ${colors.border.subtle}`,
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
                  left: -7,
                  background: `linear-gradient(135deg, ${colors.secondary} 0%, #a855f7 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.secondary, 0.4)}`,
                  zIndex: 10,
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
                        backgroundColor: isDark ? alpha(colors.secondary, 0.8) : alpha(colors.secondary, 0.1),
                        color: colors.secondary,
                        border: `1px solid ${alpha(colors.secondary, 0.3)}`,
                        '&:hover': {
                          backgroundColor: isDark ? alpha('#ffffff', 0.2) : alpha(colors.secondary, 0.2),
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
                        backgroundColor: isDark ? alpha('#ffffff', 0.2) : alpha(colors.text.secondary, 0.1),
                        color: colors.text.secondary,
                        border: `1px solid ${isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2)}`,
                        '&:hover': {
                          backgroundColor: isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2),
                          transform: 'scale(1.05)',
                          color: isDark ? colors.text.secondary : colors.text.secondary,
                          borderColor: isDark ? alpha(colors.text.secondary, 0.2) : alpha(colors.text.secondary, 0.2),
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
                border: `1px solid ${colors.border.subtle}`,
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
                  right: -7,
                  background: `linear-gradient(135deg, ${colors.success} 0%, #16a34a 100%)`,
                  width: 14,
                  height: 14,
                  border: `2px solid ${colors.background.card}`,
                  borderRadius: '50%',
                  boxShadow: `0 2px 8px ${alpha(colors.success, 0.4)}`,
                  zIndex: 10,
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
            }}
          >
            <Box sx={{ mb: 3 }}>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Define the agent&apos;s behavior and initial greeting message for users.
              </Typography>

              <Box sx={{ mb: 3 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  System Prompt
                </Typography>
                <TextField
                  fullWidth
                  multiline
                  rows={4}
                  value={systemPromptValue}
                  onChange={(e) => setSystemPromptValue(e.target.value)}
                  placeholder="Define the agent&apos;s role, capabilities, and behavior instructions..."
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: isDark ? theme.palette.primary.main : theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    },
                  }}
                />
              </Box>

              <Box sx={{ mb: 3 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  Description
                </Typography>
                <TextField
                  fullWidth
                  multiline
                  rows={2}
                  value={descriptionValue}
                  onChange={(e) => setDescriptionValue(e.target.value)}
                  placeholder="Enter a brief description for the agent&apos;s behavior..."
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: isDark ? theme.palette.primary.main : theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    },
                  }}
                />
              </Box>

              <Box>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  Starting Message
                </Typography>
                <TextField
                  fullWidth
                  multiline
                  rows={2}
                  value={startMessageValue}
                  onChange={(e) => setStartMessageValue(e.target.value)}
                  placeholder="Enter the agent&apos;s greeting message to users..."
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: isDark ? theme.palette.primary.main : theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    },
                  }}
                />
              </Box>
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

  return (
    <Card
      sx={{
        width,
        minHeight,
        border: selected ? `2px solid ${colors.primary}` : `1px solid ${colors.border.main}`,
        borderRadius: 3,
        backgroundColor: colors.background.card,
        boxShadow: selected
          ? `0 0 0 4px ${alpha(colors.primary, 0.1)}, 0 8px 25px ${alpha(isDark ? '#000' : colors.primary, isDark ? 0.4 : 0.15)}`
          : `0 4px 6px ${alpha(isDark ? '#000' : '#0f172a', isDark ? 0.3 : 0.05)}, 0 1px 3px ${alpha(isDark ? '#000' : '#0f172a', isDark ? 0.2 : 0.1)}`,
        cursor: 'pointer',
        transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
        position: 'relative',
        overflow: 'visible',
        backdropFilter: isDark ? 'blur(10px)' : 'none',
        '&:hover': {
          transform: 'translateY(-2px)',
          boxShadow: selected
            ? `0 0 0 4px ${alpha(colors.primary, 0.15)}, 0 12px 35px ${alpha(isDark ? '#000' : colors.primary, isDark ? 0.5 : 0.2)}`
            : `0 8px 25px ${alpha(isDark ? '#000' : '#0f172a', isDark ? 0.4 : 0.1)}, 0 4px 6px ${alpha(isDark ? '#000' : '#0f172a', isDark ? 0.3 : 0.05)}`,
          borderColor: selected ? colors.primary : colors.border.focus,
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
      {/* Header */}
      <Box
        sx={{
          p: 2.5,
          borderBottom: `1px solid ${colors.border.subtle}`,
          background: isDark
            ? `linear-gradient(135deg, ${alpha('#2a2a2a', 0.8)} 0%, ${alpha('#1e1e1e', 0.9)} 100%)`
            : `linear-gradient(135deg, ${alpha('#f8fafc', 0.8)} 0%, ${alpha('#ffffff', 0.9)} 100%)`,
          borderRadius: '12px 12px 0 0',
          position: 'relative',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                width: 28,
                height: 28,
                borderRadius: 2,
                background: `linear-gradient(135deg, ${colors.info} 0%, ${colors.primary} 100%)`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: `0 2px 8px ${alpha(colors.info, 0.3)}`,
              }}
            >
              <Icon
                icon={data.icon || toolIcon}
                width={14}
                height={14}
                style={{ color: '#ffffff' }}
              />
            </Box>
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
              {normalizeDisplayName(data.label)}
            </Typography>
          </Box>
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
              <Icon
                icon={packageIcon}
                width={12}
                height={12}
                style={{ color: colors.info }}
              />
              Tool Bundle
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `1px solid ${colors.border.subtle}`,
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
                border: `1px solid ${colors.border.subtle}`,
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

        {/* App Memory Section for app memory nodes */}
        {data.type.startsWith('app-') && (
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
              <Icon
                icon={cloudIcon}
                width={12}
                height={12}
                style={{ color: colors.warning }}
              />
              App
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `1px solid ${colors.border.subtle}`,
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
              <Icon
                icon={cloudIcon}
                width={12}
                height={12}
                style={{ color: colors.info }}
              />
              Apps
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `1px solid ${colors.border.subtle}`,
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
                Connected Applications
              </Typography>
              <Typography
                sx={{
                  fontSize: '0.65rem',
                  color: colors.text.secondary,
                  fontWeight: 500,
                  mt: 0.5,
                }}
              >
                {data.config?.apps?.length || 0} apps available
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
              <Icon
                icon={databaseIcon}
                width={12}
                height={12}
                style={{ color: colors.warning }}
              />
              Knowledge Base Group
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `1px solid ${colors.border.subtle}`,
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
                All Knowledge Bases
              </Typography>
              <Typography
                sx={{
                  fontSize: '0.65rem',
                  color: colors.text.secondary,
                  fontWeight: 500,
                  mt: 0.5,
                }}
              >
                {data.config?.knowledgeBases?.length || 0} KBs available
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
              <Icon icon={databaseIcon} width={12} height={12} style={{ color: colors.warning }} />
              Knowledge Base
            </Typography>
            <Box
              sx={{
                p: 2,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `1px solid ${colors.border.subtle}`,
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
                border: `1px solid ${colors.border.subtle}`,
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
            </Box>
          </Box>
        )}

        {/* Toolset Badge */}
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              px: 1.5,
              py: 0.75,
              background: `linear-gradient(135deg, ${alpha(colors.info, 0.1)} 0%, ${alpha(colors.primary, 0.1)} 100%)`,
              border: `1px solid ${alpha(colors.info, 0.2)}`,
              borderRadius: 2,
              fontSize: '0.7rem',
              fontWeight: 700,
              color: colors.info,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              transition: 'all 0.2s ease',
              '&:hover': {
                background: `linear-gradient(135deg, ${alpha(colors.info, 0.2)} 0%, ${alpha(colors.primary, 0.2)} 100%)`,
                borderColor: colors.info,
                transform: 'scale(1.05)',
                boxShadow: `0 2px 8px ${alpha(colors.info, 0.3)}`,
              },
            }}
          >
            Toolset
            <Icon icon={tuneIcon} width={12} height={12} />
          </Box>
        </Box>
      </Box>

      {/* Enhanced Input Handles */}
      {data.inputs?.map((input, index) => (
        <Handle
          key={`input-${index}`}
          type="target"
          position={Position.Left}
          id={input}
          style={{
            top: `${45 + index * 25}%`,
            left: -7,
            background: `linear-gradient(135deg, ${colors.info} 0%, ${colors.primary} 100%)`,
            width: 14,
            height: 14,
            border: `2px solid ${colors.background.card}`,
            borderRadius: '50%',
            boxShadow: `0 2px 8px ${alpha(colors.info, 0.4)}`,
            zIndex: 10,
            transition: 'all 0.2s ease',
          }}
        />
      ))}

      {/* Enhanced Output Handles */}
      {data.outputs?.map((output, index) => (
        <Handle
          key={`output-${index}`}
          type="source"
          position={Position.Right}
          id={output}
          style={{
            top: `${45 + index * 25}%`,
            right: -7,
            background: `linear-gradient(135deg, ${colors.success} 0%, #16a34a 100%)`,
            width: 14,
            height: 14,
            border: `2px solid ${colors.background.card}`,
            borderRadius: '50%',
            boxShadow: `0 2px 8px ${alpha(colors.success, 0.4)}`,
            zIndex: 10,
            transition: 'all 0.2s ease',
          }}
        />
      ))}
    </Card>
  );
};

export default FlowNode;

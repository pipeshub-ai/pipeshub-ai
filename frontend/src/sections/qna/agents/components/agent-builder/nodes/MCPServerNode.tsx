/**
 * MCPServerNode Component
 * MCP server node that displays tools as a list with add/remove functionality.
 * Uses a purple/indigo color scheme to differentiate from toolset nodes.
 */

import React, { useState, useCallback } from 'react';
import {
  Box,
  Card,
  Typography,
  useTheme,
  alpha,
  IconButton,
  List,
  ListItem,
  Divider,
  Tooltip,
  Menu,
  MenuItem,
} from '@mui/material';
import { Icon } from '@iconify/react';
import { useReactFlow } from '@xyflow/react';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import { normalizeDisplayName } from '../../../utils/agent';
import { NodeHandles } from './NodeHandles';

const MCP_ICON = 'solar:server-bold-duotone';

interface MCPTool {
  name: string;
  namespacedName: string;
  description?: string;
}

interface MCPServerNodeProps {
  data: {
    id: string;
    type: string;
    label: string;
    category?: string;
    description?: string;
    config: {
      name?: string;
      displayName?: string;
      instanceId?: string;
      instanceName?: string;
      serverType?: string;
      tools?: MCPTool[];
      selectedTools?: string[];
      availableTools?: MCPTool[];
      iconPath?: string;
      [key: string]: any;
    };
  };
  selected: boolean;
  onDelete?: (nodeId: string) => void;
}

export const MCPServerNode: React.FC<MCPServerNodeProps> = ({
  data,
  selected,
  onDelete,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const { setNodes } = useReactFlow();

  const [addToolAnchorEl, setAddToolAnchorEl] = useState<null | HTMLElement>(
    null
  );
  const addToolMenuOpen = Boolean(addToolAnchorEl);

  const displayName =
    data.config?.displayName || data.config?.name || data.label;
  const tools = data.config?.tools || [];
  const availableTools = data.config?.availableTools || [];

  const purpleAccent = isDark ? '#a78bfa' : '#7c3aed';
  const purpleAccentMuted = isDark ? '#8b5cf6' : '#6d28d9';

  const colors = {
    primary: isDark ? '#9ca3af' : '#6b6b6b',
    secondary: isDark ? '#a8b2c0' : '#808080',
    accent: purpleAccent,
    accentMuted: purpleAccentMuted,
    background: {
      card: theme.palette.background.paper,
      section: isDark
        ? alpha('#2a2a2a', 0.6)
        : alpha(theme.palette.background.default, 0.7),
      hover: theme.palette.action.hover,
    },
    border: {
      main: isDark ? '#4a4a4a' : '#d0d0d0',
      subtle: isDark ? '#3a3a3a' : '#e0e0e0',
      focus: isDark ? purpleAccent : purpleAccentMuted,
    },
    text: {
      primary: theme.palette.text.primary,
      secondary: theme.palette.text.secondary,
      muted: isDark
        ? alpha(theme.palette.text.secondary, 0.75)
        : alpha(theme.palette.text.secondary, 0.65),
    },
  };

  const handleAddToolClick = (event: React.MouseEvent<HTMLElement>) => {
    event.stopPropagation();
    setAddToolAnchorEl(event.currentTarget);
  };

  const handleAddToolClose = () => {
    setAddToolAnchorEl(null);
  };

  const handleAddTool = useCallback(
    (tool: MCPTool) => {
      setNodes((nodes: any[]) =>
        nodes.map((node: any) =>
          node.id === data.id
            ? {
                ...node,
                data: {
                  ...node.data,
                  config: {
                    ...node.data.config,
                    tools: [...(node.data.config?.tools || []), tool],
                    selectedTools: [
                      ...(node.data.config?.selectedTools || []),
                      tool.name,
                    ],
                  },
                },
              }
            : node
        )
      );
      handleAddToolClose();
    },
    [data.id, setNodes]
  );

  const handleRemoveTool = useCallback(
    (toolName: string) => {
      setNodes((nodes: any[]) =>
        nodes.map((node: any) =>
          node.id === data.id
            ? {
                ...node,
                data: {
                  ...node.data,
                  config: {
                    ...node.data.config,
                    tools:
                      node.data.config?.tools?.filter(
                        (t: MCPTool) => t.name !== toolName
                      ) || [],
                    selectedTools:
                      node.data.config?.selectedTools?.filter(
                        (t: string) => t !== toolName
                      ) || [],
                  },
                },
              }
            : node
        )
      );
    },
    [data.id, setNodes]
  );

  const handleDelete = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (onDelete) {
        onDelete(data.id);
      }
    },
    [data.id, onDelete]
  );

  const toolsToAdd = availableTools.filter(
    (availableTool) => !tools.some((t: MCPTool) => t.name === availableTool.name)
  );

  return (
    <Card
      sx={{
        width: 380,
        minHeight: 200,
        border: selected
          ? `2px solid ${colors.border.focus}`
          : `2px solid ${colors.border.main}`,
        borderRadius: 2,
        backgroundColor: colors.background.card,
        boxShadow: selected
          ? isDark
            ? `0 6px 16px rgba(0, 0, 0, 0.4), 0 0 0 1px ${alpha(purpleAccent, 0.3)}`
            : `0 6px 16px rgba(0, 0, 0, 0.15), 0 0 0 1px ${alpha(purpleAccentMuted, 0.2)}`
          : isDark
            ? `0 2px 8px rgba(0, 0, 0, 0.2)`
            : `0 2px 8px rgba(0, 0, 0, 0.1)`,
        cursor: 'pointer',
        transition: 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
        position: 'relative',
        overflow: 'visible',
        '&:hover': {
          borderColor: colors.border.focus,
          boxShadow: isDark
            ? `0 8px 20px rgba(0, 0, 0, 0.3)`
            : `0 8px 20px rgba(0, 0, 0, 0.15)`,
          transform: 'translateY(-2px)',
        },
      }}
      onClick={(e) => {
        e.stopPropagation();
      }}
    >
      <NodeHandles data={data} />

      {/* Header */}
      <Box
        sx={{
          p: 2.5,
          borderBottom: `1px solid ${colors.border.main}`,
          backgroundColor: isDark
            ? alpha('#1f1f1f', 0.6)
            : alpha(theme.palette.background.default, 0.6),
          borderTopLeftRadius: 2,
          borderTopRightRadius: 2,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            mb: data.description ? 1.5 : 0,
          }}
        >
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1.75,
              flex: 1,
              minWidth: 0,
            }}
          >
            <Icon
              icon={MCP_ICON}
              width={26}
              height={26}
              style={{ color: colors.accent }}
            />

            <Typography
              variant="h6"
              sx={{
                fontWeight: 700,
                fontSize: '1rem',
                color: colors.text.primary,
                lineHeight: 1.3,
                letterSpacing: '-0.02em',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {normalizeDisplayName(displayName)}
            </Typography>
          </Box>
          {onDelete && (
            <IconButton
              size="small"
              onClick={handleDelete}
              sx={{
                width: 32,
                height: 32,
                ml: 1,
                backgroundColor: alpha(theme.palette.error.main, 0.08),
                border: `1px solid ${alpha(theme.palette.error.main, 0.15)}`,
                '&:hover': {
                  backgroundColor: alpha(theme.palette.error.main, 0.15),
                  borderColor: alpha(theme.palette.error.main, 0.3),
                  transform: 'scale(1.08)',
                  color: theme.palette.error.main,
                },
                transition: 'all 0.2s ease',
              }}
            >
              <Icon icon={deleteIcon} width={18} height={18} />
            </IconButton>
          )}
        </Box>
        {data.description && (
          <Box>
            <Typography
              variant="body2"
              sx={{
                color: colors.text.secondary,
                fontSize: '0.8rem',
                lineHeight: 1.5,
                fontWeight: 400,
                wordBreak: 'break-word',
                whiteSpace: 'normal',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
              }}
            >
              {data.description}
            </Typography>
          </Box>
        )}
      </Box>

      {/* Content Section */}
      <Box sx={{ p: 3 }}>
        {/* Tools Section Header */}
        <Box sx={{ mb: 2 }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              mb: 1.75,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
              <Icon
                icon={MCP_ICON}
                width={14}
                height={14}
                style={{ color: colors.accent, opacity: 0.8 }}
              />
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 700,
                  color: colors.text.primary,
                  fontSize: '0.8rem',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                }}
              >
                Tools
              </Typography>
              <Box
                sx={{
                  px: 1,
                  py: 0.25,
                  borderRadius: 1,
                  backgroundColor: alpha(colors.accent, 0.1),
                  border: `1px solid ${alpha(colors.accent, 0.2)}`,
                }}
              >
                <Typography
                  sx={{
                    fontSize: '0.7rem',
                    fontWeight: 600,
                    color: colors.accent,
                  }}
                >
                  {tools.length}
                </Typography>
              </Box>
            </Box>
            {toolsToAdd.length > 0 && (
              <Tooltip title="Add tool" arrow>
                <IconButton
                  size="small"
                  onClick={handleAddToolClick}
                  sx={{
                    width: 28,
                    height: 28,
                    backgroundColor: alpha(colors.accent, 0.1),
                    border: `1px solid ${alpha(colors.accent, 0.2)}`,
                    color: colors.accent,
                    '&:hover': {
                      backgroundColor: alpha(colors.accent, 0.2),
                      borderColor: alpha(colors.accent, 0.4),
                      transform: 'scale(1.08)',
                    },
                    transition: 'all 0.2s ease',
                  }}
                >
                  <Icon icon="mdi:plus" width={16} height={16} />
                </IconButton>
              </Tooltip>
            )}
          </Box>

          {tools.length === 0 ? (
            <Box
              sx={{
                p: 3,
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `1.5px dashed ${colors.border.subtle}`,
                textAlign: 'center',
                transition: 'all 0.2s ease',
                '&:hover': {
                  borderColor: colors.border.main,
                  backgroundColor: alpha(colors.background.section, 1.1),
                },
              }}
            >
              <Icon
                icon={MCP_ICON}
                width={32}
                height={32}
                style={{
                  color: colors.text.muted,
                  opacity: 0.4,
                  marginBottom: 8,
                }}
              />
              <Typography
                sx={{
                  fontSize: '0.8rem',
                  color: colors.text.muted,
                  fontWeight: 500,
                }}
              >
                No tools selected
              </Typography>
              {toolsToAdd.length > 0 && (
                <Typography
                  sx={{
                    fontSize: '0.7rem',
                    color: colors.text.muted,
                    mt: 0.5,
                    opacity: 0.7,
                  }}
                >
                  Click + to add tools
                </Typography>
              )}
            </Box>
          ) : (
            <Box
              sx={{
                backgroundColor: colors.background.section,
                borderRadius: 2,
                border: `1px solid ${colors.border.subtle}`,
                transition: 'all 0.2s ease',
                minHeight: 60,
                maxHeight: 320,
                overflowY: 'auto',
                overflowX: 'hidden',
                '&:hover': {
                  borderColor: colors.border.main,
                },
                '&::-webkit-scrollbar': {
                  width: 6,
                },
                '&::-webkit-scrollbar-track': {
                  backgroundColor: 'transparent',
                },
                '&::-webkit-scrollbar-thumb': {
                  backgroundColor: alpha(theme.palette.text.secondary, 0.2),
                  borderRadius: 3,
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.text.secondary, 0.35),
                  },
                },
              }}
              onWheel={(e) => {
                e.stopPropagation();
              }}
              onMouseDown={(e) => {
                e.stopPropagation();
              }}
            >
              <List dense sx={{ py: 1.5, px: 1 }}>
                {tools.map((tool: MCPTool, index: number) => (
                  <React.Fragment key={tool.name || index}>
                    {index > 0 && (
                      <Divider
                        sx={{
                          my: 1.25,
                          borderColor: colors.border.subtle,
                        }}
                      />
                    )}
                    <ListItem
                      sx={{
                        px: 1.5,
                        py: 1.25,
                        borderRadius: 1.5,
                        mb: index < tools.length - 1 ? 0.5 : 0,
                        transition: 'all 0.15s ease',
                        '&:hover': {
                          backgroundColor: alpha(colors.accent, 0.08),
                        },
                      }}
                    >
                      <Box sx={{ flex: 1, minWidth: 0, pr: 2 }}>
                        <Typography
                          sx={{
                            fontSize: '0.8rem',
                            fontWeight: 600,
                            color: colors.text.primary,
                            lineHeight: 1.4,
                            mb: tool.description ? 0.5 : 0,
                          }}
                        >
                          {tool.name
                            .replace(/_/g, ' ')
                            .split(' ')
                            .map(
                              (word: string) =>
                                word.charAt(0).toUpperCase() + word.slice(1)
                            )
                            .join(' ')}
                        </Typography>
                        {tool.description && (
                          <Typography
                            sx={{
                              fontSize: '0.7rem',
                              color: colors.text.secondary,
                              fontWeight: 400,
                              lineHeight: 1.4,
                              display: '-webkit-box',
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: 'vertical',
                              overflow: 'hidden',
                            }}
                          >
                            {tool.description}
                          </Typography>
                        )}
                      </Box>
                      <Tooltip title="Remove tool" arrow>
                        <IconButton
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRemoveTool(tool.name);
                          }}
                          sx={{
                            width: 28,
                            height: 28,
                            ml: 1,
                            color: theme.palette.error.main,
                            backgroundColor: alpha(
                              theme.palette.error.main,
                              0.08
                            ),
                            border: `1px solid ${alpha(theme.palette.error.main, 0.15)}`,
                            '&:hover': {
                              backgroundColor: alpha(
                                theme.palette.error.main,
                                0.15
                              ),
                              borderColor: alpha(
                                theme.palette.error.main,
                                0.3
                              ),
                              transform: 'scale(1.08)',
                            },
                            transition: 'all 0.2s ease',
                          }}
                        >
                          <Icon icon="mdi:minus" width={16} height={16} />
                        </IconButton>
                      </Tooltip>
                    </ListItem>
                  </React.Fragment>
                ))}
              </List>
            </Box>
          )}
        </Box>

        {/* MCP Server Badge */}
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              px: 1.5,
              py: 0.75,
              background: `linear-gradient(135deg, ${alpha(colors.accent, 0.1)} 0%, ${alpha(colors.accentMuted, 0.1)} 100%)`,
              border: `1px solid ${alpha(colors.accent, 0.2)}`,
              borderRadius: 2,
              fontSize: '0.7rem',
              fontWeight: 700,
              color: colors.accent,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              transition: 'all 0.2s ease',
              '&:hover': {
                background: `linear-gradient(135deg, ${alpha(colors.accent, 0.2)} 0%, ${alpha(colors.accentMuted, 0.2)} 100%)`,
                borderColor: colors.accent,
                transform: 'scale(1.05)',
                boxShadow: `0 2px 8px ${alpha(colors.accent, 0.3)}`,
              },
            }}
          >
            MCP Server
            <Icon icon={MCP_ICON} width={12} height={12} />
          </Box>
        </Box>
      </Box>

      {/* Add Tool Menu */}
      <Menu
        anchorEl={addToolAnchorEl}
        open={addToolMenuOpen}
        onClose={handleAddToolClose}
        onClick={(e) => e.stopPropagation()}
        PaperProps={{
          sx: {
            maxHeight: 320,
            width: 280,
            mt: 1,
            borderRadius: 2,
            boxShadow: isDark
              ? `0 8px 24px rgba(0, 0, 0, 0.4)`
              : `0 8px 24px rgba(0, 0, 0, 0.15)`,
            border: `1px solid ${colors.border.main}`,
          },
        }}
        MenuListProps={{
          sx: { py: 1 },
        }}
      >
        {toolsToAdd.length === 0 ? (
          <MenuItem disabled sx={{ py: 2, justifyContent: 'center' }}>
            <Typography variant="body2" sx={{ color: colors.text.muted }}>
              All tools added
            </Typography>
          </MenuItem>
        ) : (
          toolsToAdd.map((tool: MCPTool) => (
            <MenuItem
              key={tool.name}
              onClick={(e) => {
                e.stopPropagation();
                handleAddTool(tool);
              }}
              sx={{
                flexDirection: 'column',
                alignItems: 'flex-start',
                py: 1.5,
                px: 2,
                transition: 'all 0.15s ease',
                '&:hover': {
                  backgroundColor: colors.background.hover,
                },
              }}
            >
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  color: colors.text.primary,
                  mb: tool.description ? 0.5 : 0,
                }}
              >
                {tool.name}
              </Typography>
              {tool.description && (
                <Typography
                  variant="caption"
                  sx={{
                    color: colors.text.secondary,
                    fontSize: '0.75rem',
                    lineHeight: 1.4,
                  }}
                >
                  {tool.description}
                </Typography>
              )}
            </MenuItem>
          ))
        )}
      </Menu>
    </Card>
  );
};

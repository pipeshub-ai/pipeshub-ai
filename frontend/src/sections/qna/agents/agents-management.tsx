// Enhanced AgentsManagement component with template edit/delete management
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Grid,
  Card,
  CardContent,
  Typography,
  Button,
  TextField,
  InputAdornment,
  Chip,
  IconButton,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Alert,
  CircularProgress,
  useTheme,
  alpha,
  Avatar,
  Tooltip,
  Stack,
  Divider,
  Paper,
  Container,
  LinearProgress,
  Fade,
} from '@mui/material';
import { Icon } from '@iconify/react';
import searchIcon from '@iconify-icons/mdi/magnify';
import plusIcon from '@iconify-icons/mdi/plus';
import moreVertIcon from '@iconify-icons/mdi/dots-vertical';
import editIcon from '@iconify-icons/mdi/pencil';
import deleteIcon from '@iconify-icons/mdi/delete';
import chatIcon from '@iconify-icons/mdi/chat';
import templateIcon from '@iconify-icons/mdi/file-document';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import timeIcon from '@iconify-icons/mdi/clock-outline';
import clearIcon from '@iconify-icons/mdi/close';
import folderIcon from '@iconify-icons/mdi/folder-multiple';
import databaseIcon from '@iconify-icons/mdi/database';
import flowIcon from '@iconify-icons/mdi/graph';
import permissionsIcon from '@iconify-icons/mdi/account-key';

import type { Agent, AgentTemplate, AgentFilterOptions } from 'src/types/agent';
import { paths } from 'src/routes/paths';
import AgentApiService from './services/api';
import { filterAgents, sortAgents, formatTimestamp } from './utils/agent';
import TemplateBuilder from './components/template-builder';
import TemplateSelector from './components/template-selector';
import AgentPermissionsDialog from './components/agent-builder/agent-permissions-dialog';

interface AgentsManagementProps {
  onAgentSelect?: (agent: Agent) => void;
}

const AgentsManagement: React.FC<AgentsManagementProps> = ({ onAgentSelect }) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const isDark = theme.palette.mode === 'dark';

  // State management
  const [agents, setAgents] = useState<Agent[]>([]);
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // UI State
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [sortBy, setSortBy] = useState('updatedAt');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Dialog states
  const [showTemplateBuilder, setShowTemplateBuilder] = useState(false);
  const [showTemplateSelector, setShowTemplateSelector] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<AgentTemplate | null>(null);
  const [editingTemplate, setEditingTemplate] = useState<AgentTemplate | null>(null);

  // Menu states
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const [activeAgent, setActiveAgent] = useState<Agent | null>(null);

  // Delete confirmation dialogs
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; agent: Agent | null }>({
    open: false,
    agent: null,
  });

  const [deleteTemplateDialog, setDeleteTemplateDialog] = useState<{
    open: boolean;
    template: AgentTemplate | null;
  }>({
    open: false,
    template: null,
  });

  // Permissions dialog state
  const [permissionsDialog, setPermissionsDialog] = useState<{ open: boolean; agent: Agent | null }>({
    open: false,
    agent: null,
  });

  // Enhanced color scheme
  const bgPaper = isDark ? '#1F2937' : '#ffffff';
  const bgHeader = isDark ? 'rgba(255, 255, 255, 0.02)' : 'rgba(0, 0, 0, 0.02)';
  const borderColor = isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.06)';
  const textPrimary = isDark ? '#ffffff' : '#1f2937';
  const textSecondary = isDark ? '#9ca3af' : '#6b7280';

  const loadAgents = useCallback(async () => {
    try {
      setLoading(true);
      const response = await AgentApiService.getAgents();
      setAgents(response || []);
      setError(null);
    } catch (err) {
      setError('Failed to load agents');
      console.error('Error loading agents:', err);
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTemplates = useCallback(async () => {
    try {
      const response = await AgentApiService.getTemplates();
      setTemplates(response || []);
    } catch (err) {
      console.error('Error loading templates:', err);
      setTemplates([]);
    }
  }, []);

  // Load data
  useEffect(() => {
    loadAgents();
    loadTemplates();
  }, [loadAgents, loadTemplates]);

  // Filter and sort agents with safe array handling
  const filteredAndSortedAgents = useMemo(() => {
    if (!Array.isArray(agents)) {
      return [];
    }

    const filters: AgentFilterOptions = {
      searchQuery,
      tags: selectedTags,
    };

    const filtered = filterAgents(agents, filters);
    return sortAgents(filtered, sortBy, sortOrder);
  }, [agents, searchQuery, selectedTags, sortBy, sortOrder]);

  // Get all available tags with safe array handling
  const availableTags = useMemo(() => {
    if (!Array.isArray(agents)) {
      return [];
    }

    const tags = new Set<string>();
    agents.forEach((agent) => {
      if (Array.isArray(agent.tags)) {
        agent.tags.forEach((tag) => tags.add(tag));
      }
    });
    return Array.from(tags).sort();
  }, [agents]);

  // Agent Event handlers
  const handleCreateAgent = useCallback(async (agent: Agent) => {
    setAgents((prev) => (Array.isArray(prev) ? [agent, ...prev] : [agent]));
    setSelectedTemplate(null);
  }, []);

  const handleEditAgent = useCallback(
    (agent: Agent) => {
      navigate(paths.dashboard.agent.edit(agent._key));
    },
    [navigate]
  );

  const handleDeleteAgent = useCallback(async (agent: Agent) => {
    try {
      await AgentApiService.deleteAgent(agent._key);
      setAgents((prev) => (Array.isArray(prev) ? prev.filter((a) => a._key !== agent._key) : []));
      setDeleteDialog({ open: false, agent: null });
    } catch (err) {
      setError('Failed to delete agent');
      console.error('Error deleting agent:', err);
    }
  }, []);

  const handleChatWithAgent = useCallback(
    (agent: Agent) => {
      navigate(`/agents/${agent._key}`);
    },
    [navigate]
  );

  // Template Event handlers
  const handleCreateTemplate = useCallback(async (template: AgentTemplate) => {
    setTemplates((prev) => (Array.isArray(prev) ? [template, ...prev] : [template]));
    setShowTemplateBuilder(false);
    setEditingTemplate(null);
  }, []);

  const handleEditTemplate = useCallback((template: AgentTemplate) => {
    setEditingTemplate(template);
    setShowTemplateBuilder(true);
    setShowTemplateSelector(false);
  }, []);

  const handleDeleteTemplate = useCallback((template: AgentTemplate) => {
    setDeleteTemplateDialog({ open: true, template });
    setShowTemplateSelector(false);
  }, []);

  const confirmDeleteTemplate = useCallback(async (template: AgentTemplate) => {
    try {
      await AgentApiService.deleteTemplate(template._key);
      setTemplates((prev) =>
        Array.isArray(prev) ? prev.filter((t) => t._key !== template._key) : []
      );
      setDeleteTemplateDialog({ open: false, template: null });
    } catch (err) {
      setError('Failed to delete template');
      console.error('Error deleting template:', err);
    }
  }, []);

  const handleMenuOpen = useCallback((event: React.MouseEvent<HTMLButtonElement>, agent: Agent) => {
    setAnchorEl(event.currentTarget);
    setActiveAgent(agent);
  }, []);

  const handleMenuClose = () => {
    setAnchorEl(null);
    setActiveAgent(null);
  };

  const handleOpenPermissions = (agent: Agent) => {
    setPermissionsDialog({ open: true, agent });
    handleMenuClose();
  };

  const handleTemplateSelect = useCallback(
    (template: AgentTemplate) => {
      setSelectedTemplate(template);
      setShowTemplateSelector(false);
      navigate(paths.dashboard.agent.new);
    },
    [navigate]
  );

  const handleTagToggle = useCallback((tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  }, []);

  const handleClearSearch = useCallback(() => {
    setSearchQuery('');
    setSelectedTags([]);
  }, []);

  // Enhanced compact agent card component
  const renderAgentCard = useCallback(
    (agent: Agent) => {
      if (!agent || !agent._key) {
        return null;
      }

      const cardBg = isDark ? 'rgba(32, 30, 30, 0.5)' : '#ffffff';
      const cardBorder = isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)';
      const statsBg = isDark ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.03)';

      return (
        <Grid item xs={12} sm={6} md={4} lg={3} key={agent._key}>
          <Card
            sx={{
              height: '100%',
              minHeight: '280px',
              display: 'flex',
              flexDirection: 'column',
              borderRadius: '10px',
              bgcolor: cardBg,
              border: `1px solid ${cardBorder}`,
              transition: 'all 0.2s ease-in-out',
              position: 'relative',
              overflow: 'hidden',
              '&:hover': {
                boxShadow: `0 4px 20px ${alpha(theme.palette.primary.main, 0.15)}`,
                borderColor: alpha(theme.palette.primary.main, 0.3),
                transform: 'translateY(-2px)',
              },
            }}
          >
            {/* Compact Header Section */}
            <Box
              sx={{
                p: 2,
                borderBottom: `1px solid ${cardBorder}`,
                backgroundColor: bgHeader,
                position: 'relative',
              }}
            >
              {/* Agent Icon & Name - More compact */}
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1.5,
                }}
              >
                <Avatar
                  sx={{
                    width: 40,
                    height: 40,
                    bgcolor: alpha(theme.palette.primary.main, 0.1),
                    border: `2px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                  }}
                >
                  <Icon
                    icon={sparklesIcon}
                    width={20}
                    height={20}
                    color={theme.palette.primary.main}
                  />
                </Avatar>

                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    variant="subtitle1"
                    sx={{
                      fontWeight: 600,
                      color: textPrimary,
                      fontSize: '0.875rem',
                      lineHeight: 1.2,
                      mb: 0.5,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {agent.name || 'Unnamed Agent'}
                  </Typography>
                </Box>
              </Box>
            </Box>

            {/* Compact Content Section */}
            <CardContent
              sx={{
                p: 2,
                display: 'flex',
                flexDirection: 'column',
                flex: 1,
                gap: 1.5,
              }}
            >
              {/* Description - More compact */}
              <Typography
                variant="body2"
                sx={{
                  color: textSecondary,
                  fontSize: '0.75rem',
                  lineHeight: 1.3,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  minHeight: '2em',
                }}
              >
                {agent.description || 'No description available'}
              </Typography>

              {/* Tags - More compact */}
              {Array.isArray(agent.tags) && agent.tags.length > 0 && (
                <Box
                  sx={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 0.5,
                  }}
                >
                  {agent.tags.slice(0, 3).map((tag) => (
                    <Chip
                      key={tag}
                      label={tag}
                      size="small"
                      sx={{
                        height: 16,
                        fontSize: '0.6rem',
                        fontWeight: 400,
                        bgcolor: isDark ? 'rgba(228, 214, 214, 0.85)' : 'rgba(0, 0, 0, 0.05)',
                        color: theme.palette.secondary.main,
                        border: 'none',
                        '&:hover': {
                          backgroundColor: isDark
                            ? 'rgba(228, 214, 214, 0.85)'
                            : 'rgba(0, 0, 0, 0.05)',
                        },
                      }}
                    />
                  ))}
                  {agent.tags.length > 3 && (
                    <Chip
                      label={`+${agent.tags.length - 3}`}
                      size="small"
                      sx={{
                        height: 16,
                        fontSize: '0.6rem',
                        bgcolor: statsBg,
                        color: textSecondary,
                        border: 'none',
                      }}
                    />
                  )}
                </Box>
              )}

              {/* Compact Stats Section */}
              <Box
                sx={{
                  mt: 'auto',
                  pt: 1.5,
                  borderTop: `1px solid ${cardBorder}`,
                }}
              >
                <Box
                  sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    mb: 1.5,
                  }}
                >
                  <Tooltip
                    title={`Updated ${formatTimestamp(agent.updatedAtTimestamp || new Date().toISOString())}`}
                  >
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 0.5,
                        px: 0.75,
                        py: 0.25,
                        borderRadius: '4px',
                        bgcolor: statsBg,
                      }}
                    >
                      <Icon icon={timeIcon} width={10} height={10} color={textSecondary} />
                      <Typography
                        variant="caption"
                        sx={{
                          color: textSecondary,
                          fontSize: '0.65rem',
                        }}
                      >
                        {formatTimestamp(agent.updatedAtTimestamp || new Date().toISOString())}
                      </Typography>
                    </Box>
                  </Tooltip>
                </Box>

                {/* Compact Action Buttons */}
                <Box
                  sx={{
                    display: 'flex',
                    gap: 0.75,
                  }}
                >
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => handleChatWithAgent(agent)}
                    startIcon={<Icon icon={chatIcon} width={12} height={12} />}
                    sx={{
                      flex: 1,
                      height: 28,
                      fontSize: '0.7rem',
                      fontWeight: 500,
                      borderRadius: '6px',
                      textTransform: 'none',
                      minWidth: 'auto',
                      borderColor: alpha(theme.palette.primary.main, 0.3),
                      color: theme.palette.primary.main,
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.primary.main, 0.05),
                        borderColor: theme.palette.primary.main,
                      },
                    }}
                  >
                    Chat
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => handleEditAgent(agent)}
                    startIcon={<Icon icon={editIcon} width={12} height={12} />}
                    sx={{
                      flex: 1,
                      height: 28,
                      fontSize: '0.7rem',
                      fontWeight: 500,
                      borderRadius: '6px',
                      textTransform: 'none',
                      minWidth: 'auto',
                      borderColor: alpha(theme.palette.primary.main, 0.3),
                      color: theme.palette.primary.main,
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.primary.main, 0.05),
                        borderColor: theme.palette.primary.main,
                      },
                    }}
                  >
                    Edit
                  </Button>
                  <IconButton
                    size="small"
                    onClick={(e) => handleMenuOpen(e, agent)}
                    sx={{
                      width: 28,
                      height: 28,
                      borderRadius: '6px',
                      border: `1px solid ${cardBorder}`,
                      color: textSecondary,
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.primary.main, 0.05),
                        borderColor: alpha(theme.palette.primary.main, 0.3),
                        color: theme.palette.primary.main,
                      },
                    }}
                  >
                    <Icon icon={moreVertIcon} width={14} height={14} />
                  </IconButton>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>
      );
    },
    [
      handleMenuOpen,
      handleChatWithAgent,
      handleEditAgent,
      theme,
      isDark,
      textPrimary,
      textSecondary,
      bgHeader,
    ]
  );

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 400 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ height: '90vh', display: 'flex', flexDirection: 'column' }}>
      {/* Enhanced Header */}
      <Box
        sx={{
          borderBottom: `1px solid ${borderColor}`,
          backgroundColor: 'background.paper',
          px: { xs: 2, sm: 3 },
          py: 2.5,
        }}
      >
        {loading && (
          <LinearProgress
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 2,
              backgroundColor: 'transparent',
            }}
          />
        )}

        <Stack
          direction="row"
          alignItems="center"
          spacing={3}
          sx={{
            width: '100%',
            minHeight: 56,
          }}
        >
          <Box sx={{ flexShrink: 0 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 0.5 }}>
              <Avatar
                sx={{
                  width: 32,
                  height: 32,
                  bgcolor: alpha(theme.palette.primary.main, 0.1),
                  border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                }}
              >
                <Icon icon={folderIcon} width={18} height={18} color={theme.palette.primary.main} />
              </Avatar>
              <Typography
                variant="h5"
                sx={{
                  fontSize: '1.5rem',
                  fontWeight: 700,
                  color: textPrimary,
                  letterSpacing: '-0.02em',
                  lineHeight: 1.2,
                }}
              >
                Agent Repository
              </Typography>
            </Box>
            <Typography
              variant="body2"
              sx={{
                fontSize: '0.875rem',
                color: textSecondary,
                fontWeight: 400,
                ml: 5.5,
              }}
            >
              Manage and deploy your AI agents
              {Array.isArray(agents) && agents.length > 0 && (
                <Box component="span" sx={{ ml: 1 }}>
                  • {agents.length} agent{agents.length !== 1 ? 's' : ''}
                </Box>
              )}
            </Typography>
          </Box>

          <Box sx={{ flexGrow: 1 }} />

          {/* Enhanced Search */}
          <Box sx={{ flexShrink: 0, minWidth: 300 }}>
            <TextField
              placeholder="Search agents..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              size="small"
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Icon icon={searchIcon} fontSize={18} color="action.active" />
                  </InputAdornment>
                ),
                endAdornment: searchQuery && (
                  <InputAdornment position="end">
                    <Box
                      component="button"
                      onClick={handleClearSearch}
                      sx={{
                        width: 20,
                        height: 20,
                        borderRadius: 0.5,
                        border: 'none',
                        backgroundColor: 'transparent',
                        color: 'action.active',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        cursor: 'pointer',
                        '&:hover': {
                          backgroundColor: 'action.hover',
                          color: 'text.primary',
                        },
                      }}
                    >
                      <Icon icon={clearIcon} fontSize={14} />
                    </Box>
                  </InputAdornment>
                ),
              }}
              sx={{
                width: '100%',
                '& .MuiOutlinedInput-root': {
                  height: 36,
                  borderRadius: 1.5,
                  backgroundColor: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.02)',
                  border: `1px solid ${borderColor}`,
                  fontSize: '0.875rem',
                  '&:hover': {
                    borderColor: alpha(theme.palette.primary.main, 0.3),
                  },
                  '&.Mui-focused': {
                    borderColor: theme.palette.primary.main,
                    backgroundColor: bgPaper,
                  },
                  '& .MuiOutlinedInput-notchedOutline': {
                    border: 'none',
                  },
                },
                '& .MuiInputBase-input::placeholder': {
                  color: textSecondary,
                  opacity: 0.7,
                  fontSize: '0.875rem',
                },
              }}
            />
            {searchQuery && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  mt: 0.5,
                  display: 'block',
                  fontSize: '0.7rem',
                }}
              >
                {Array.isArray(filteredAndSortedAgents) ? filteredAndSortedAgents.length : 0} of{' '}
                {Array.isArray(agents) ? agents.length : 0} results
              </Typography>
            )}
          </Box>

          {/* Action Buttons with better labels */}
          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            <Button
              variant="outlined"
              startIcon={<Icon icon={databaseIcon} fontSize={14} />}
              onClick={() => setShowTemplateBuilder(true)}
              sx={{
                height: 32,
                px: 1.5,
                borderRadius: 1,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                borderColor,
                color: textSecondary,
                '&:hover': {
                  borderColor: alpha(theme.palette.primary.main, 0.3),
                  backgroundColor: alpha(theme.palette.primary.main, 0.05),
                  color: theme.palette.primary.main,
                },
              }}
            >
              <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>New Template</Box>
            </Button>

            <Button
              variant="outlined"
              startIcon={<Icon icon={plusIcon} fontSize={14} />}
              onClick={() => navigate(paths.dashboard.agent.new)}
              sx={{
                height: 32,
                px: 1.5,
                borderRadius: 1,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                borderColor: 'primary.main',
                color: 'primary.main',
                '&:hover': {
                  backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                  borderColor: 'primary.dark',
                },
              }}
            >
              <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>New Agent</Box>
            </Button>
          </Stack>
        </Stack>

        {/* Tag Filters */}
        {Array.isArray(availableTags) && availableTags.length > 0 && (
          <Box sx={{ mt: 2 }}>
            <Typography
              variant="caption"
              sx={{
                color: textSecondary,
                fontSize: '0.75rem',
                fontWeight: 500,
                mb: 1,
                display: 'block',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              Tags:
            </Typography>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
              {availableTags.map((tag) => (
                <Chip
                  key={tag}
                  label={tag}
                  size="small"
                  variant={selectedTags.includes(tag) ? 'filled' : 'outlined'}
                  onClick={() => handleTagToggle(tag)}
                  sx={{
                    fontSize: '0.7rem',
                    height: 24,
                    borderColor,
                    color: selectedTags.includes(tag) ? '#ffffff' : textSecondary,
                    backgroundColor: selectedTags.includes(tag)
                      ? theme.palette.secondary.main
                      : 'transparent',
                    '&:hover': {
                      backgroundColor: selectedTags.includes(tag)
                        ? theme.palette.secondary.dark
                        : alpha(theme.palette.secondary.main, 0.05),
                      borderColor: theme.palette.secondary.main,
                    },
                  }}
                />
              ))}
            </Box>
          </Box>
        )}
      </Box>

      {/* Content Area */}
      <Box
        sx={{
          flex: 1,
          overflow: 'auto',
          display: 'flex',
          flexDirection: 'column',
          '&::-webkit-scrollbar': {
            width: '6px',
          },
          '&::-webkit-scrollbar-track': {
            background: 'transparent',
          },
          '&::-webkit-scrollbar-thumb': {
            background: alpha(theme.palette.grey[500], 0.3),
            borderRadius: '3px',
            '&:hover': {
              background: alpha(theme.palette.grey[500], 0.5),
            },
          },
        }}
      >
        <Container
          maxWidth="xl"
          sx={{
            py: { xs: 2, sm: 3 },
            px: { xs: 2, sm: 3 },
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {error && (
            <Alert severity="error" sx={{ mb: 3, borderRadius: 2 }}>
              {error}
            </Alert>
          )}

          {/* Results Info */}
          <Box
            sx={{ mb: 3, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
          >
            <Typography variant="body2" sx={{ color: textSecondary, fontSize: '0.875rem' }}>
              {Array.isArray(filteredAndSortedAgents) ? filteredAndSortedAgents.length : 0} agent
              {(Array.isArray(filteredAndSortedAgents) ? filteredAndSortedAgents.length : 0) !== 1
                ? 's'
                : ''}{' '}
              found
            </Typography>
            {(searchQuery || selectedTags.length > 0) && (
              <Button
                variant="outlined"
                size="small"
                onClick={handleClearSearch}
                sx={{
                  borderRadius: 1,
                  textTransform: 'none',
                  fontSize: '0.8125rem',
                  borderColor,
                  color: textSecondary,
                  '&:hover': {
                    borderColor: theme.palette.primary.main,
                    backgroundColor: alpha(theme.palette.primary.main, 0.05),
                  },
                }}
              >
                Clear Filters
              </Button>
            )}
          </Box>

          <Fade in timeout={300}>
            <Box sx={{ flex: 1 }}>
              {/* Agents Grid */}
              {!Array.isArray(filteredAndSortedAgents) || filteredAndSortedAgents.length === 0 ? (
                <Paper
                  sx={{
                    p: 6,
                    textAlign: 'center',
                    bgcolor: alpha(theme.palette.primary.main, 0.04),
                    border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                    borderRadius: 2,
                  }}
                >
                  <Icon
                    icon={sparklesIcon}
                    width={64}
                    height={64}
                    color={theme.palette.text.disabled}
                  />
                  <Typography variant="h6" sx={{ mt: 2, mb: 1, color: textPrimary }}>
                    {searchQuery || selectedTags.length > 0
                      ? 'No agents found'
                      : 'No agents in repository'}
                  </Typography>
                  <Typography variant="body2" sx={{ color: textSecondary, mb: 3 }}>
                    {searchQuery || selectedTags.length > 0
                      ? 'Try adjusting your search criteria or filters'
                      : 'Create your first AI agent to get started with automation'}
                  </Typography>
                  {!searchQuery && !selectedTags.length && (
                    <Stack direction="row" spacing={2} justifyContent="center">
                      <Button
                        variant="outlined"
                        startIcon={<Icon icon={flowIcon} width={16} height={16} />}
                        onClick={() => navigate(paths.dashboard.agent.new)}
                        sx={{
                          borderRadius: 1.5,
                          textTransform: 'none',
                          fontSize: '0.875rem',
                          fontWeight: 500,
                        }}
                      >
                         Create New Agent
                      </Button>
                    </Stack>
                  )}
                </Paper>
              ) : (
                <Grid container spacing={2.5}>
                  {filteredAndSortedAgents.map(renderAgentCard).filter(Boolean)}
                </Grid>
              )}
            </Box>
          </Fade>
        </Container>
      </Box>

      {/* Context Menu */}
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={handleMenuClose}
        TransitionComponent={Fade}
        transitionDuration={200}
        PaperProps={{
          sx: {
            borderRadius: 2,
            minWidth: 220,
            border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
            bgcolor: alpha(theme.palette.background.paper, 0.95),
            backdropFilter: 'blur(8px)',
            boxShadow: '0 8px 32px rgba(0, 0, 0, 0.12)',
            overflow: 'hidden',
            transform: 'scale(0.95)',
            transition: 'transform 0.2s ease',
            '&.MuiMenu-paper': {
              transform: 'scale(1)',
            },
          },
        }}
        MenuListProps={{
          sx: {
            py: 0.5,
          },
        }}
      >
        <MenuItem
          onClick={() => {
            if (activeAgent) handleEditAgent(activeAgent);
            handleMenuClose();
          }}
          sx={{
            py: 1.5,
            px: 2,
            mx: 0.5,
            borderRadius: 1,
            '&:hover': {
              bgcolor: alpha(theme.palette.primary.main, 0.08),
              transform: 'translateX(2px)',
            },
            transition: 'all 0.15s ease',
          }}
        >
          <ListItemIcon sx={{ minWidth: 36 }}>
            <Icon icon={editIcon} width={18} height={18} />
          </ListItemIcon>
          <ListItemText 
            primary="Edit Agent"
            primaryTypographyProps={{
              sx: { fontSize: '0.875rem', fontWeight: 500 }
            }}
          />
        </MenuItem>
        <MenuItem
          onClick={() => {
            if (activeAgent) handleChatWithAgent(activeAgent);
            handleMenuClose();
          }}
          sx={{
            py: 1.5,
            px: 2,
            mx: 0.5,
            borderRadius: 1,
            '&:hover': {
              bgcolor: alpha(theme.palette.primary.main, 0.08),
              transform: 'translateX(2px)',
            },
            transition: 'all 0.15s ease',
          }}
        >
          <ListItemIcon sx={{ minWidth: 36 }}>
            <Icon icon={chatIcon} width={18} height={18} />
          </ListItemIcon>
          <ListItemText 
            primary="Start Chat"
            primaryTypographyProps={{
              sx: { fontSize: '0.875rem', fontWeight: 500 }
            }}
          />
        </MenuItem>
        <Divider sx={{ my: 0.5, borderColor: alpha(theme.palette.divider, 0.08) }} />
        <MenuItem
          onClick={() => {
            if (activeAgent) handleOpenPermissions(activeAgent);
            handleMenuClose();
          }}
          sx={{
            py: 1.5,
            px: 2,
            mx: 0.5,
            borderRadius: 1,
            '&:hover': {
              bgcolor: alpha(theme.palette.info.main, 0.08),
              transform: 'translateX(2px)',
            },
            transition: 'all 0.15s ease',
          }}
        >
          <ListItemIcon sx={{ minWidth: 36 }}>
            <Icon icon={permissionsIcon} width={18} height={18} />
          </ListItemIcon>
          <ListItemText 
            primary="Manage Permissions"
            primaryTypographyProps={{
              sx: { fontSize: '0.875rem', fontWeight: 500 }
            }}
          />
        </MenuItem>
        <Divider sx={{ my: 0.5, borderColor: alpha(theme.palette.divider, 0.08) }} />
        <MenuItem
          onClick={() => {
            if (activeAgent) setDeleteDialog({ open: true, agent: activeAgent });
            handleMenuClose();
          }}
          sx={{ 
            color: 'error.main',
            py: 1.5,
            px: 2,
            mx: 0.5,
            borderRadius: 1,
            '&:hover': {
              bgcolor: alpha(theme.palette.error.main, 0.08),
              transform: 'translateX(2px)',
            },
            transition: 'all 0.15s ease',
          }}
        >
          <ListItemIcon sx={{ minWidth: 36 }}>
            <Icon icon={deleteIcon} width={18} height={18} color={theme.palette.error.main} />
          </ListItemIcon>
          <ListItemText 
            primary="Delete Agent"
            primaryTypographyProps={{
              sx: { fontSize: '0.875rem', fontWeight: 500 }
            }}
          />
        </MenuItem>
      </Menu>

      {/* Delete Agent Confirmation Dialog */}
      <Dialog
        open={deleteDialog.open}
        onClose={() => setDeleteDialog({ open: false, agent: null })}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2,
            border: `1px solid ${borderColor}`,
            bgcolor: bgPaper,
          },
        }}
      >
        <DialogTitle sx={{ color: textPrimary }}>Delete Agent</DialogTitle>
        <DialogContent>
          <Typography sx={{ color: textSecondary }}>
            Are you sure you want to delete <strong>&quot;{deleteDialog.agent?.name}&quot;</strong>?
            This action cannot be undone and will remove all associated conversations and data.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setDeleteDialog({ open: false, agent: null })}
            sx={{ textTransform: 'none' }}
          >
            Cancel
          </Button>
          <Button
            onClick={() => deleteDialog.agent && handleDeleteAgent(deleteDialog.agent)}
            color="error"
            variant="contained"
            sx={{ textTransform: 'none' }}
          >
            Delete Agent
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Template Confirmation Dialog */}
      <Dialog
        open={deleteTemplateDialog.open}
        onClose={() => setDeleteTemplateDialog({ open: false, template: null })}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2,
            border: `1px solid ${borderColor}`,
            bgcolor: bgPaper,
          },
        }}
      >
        <DialogTitle sx={{ color: textPrimary }}>Delete Template</DialogTitle>
        <DialogContent>
          <Typography sx={{ color: textSecondary }}>
            Are you sure you want to delete template{' '}
            <strong>&quot;{deleteTemplateDialog.template?.name}&quot;</strong>? This action cannot
            be undone and will remove the template permanently.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setDeleteTemplateDialog({ open: false, template: null })}
            sx={{ textTransform: 'none' }}
          >
            Cancel
          </Button>
          <Button
            onClick={() =>
              deleteTemplateDialog.template && confirmDeleteTemplate(deleteTemplateDialog.template)
            }
            color="error"
            variant="contained"
            sx={{ textTransform: 'none' }}
          >
            Delete Template
          </Button>
        </DialogActions>
      </Dialog>

      {/* Template Builder Dialog */}
      <TemplateBuilder
        open={showTemplateBuilder}
        onClose={() => {
          setShowTemplateBuilder(false);
          setEditingTemplate(null);
        }}
        onSuccess={handleCreateTemplate}
        editingTemplate={editingTemplate}
      />

      {/* Template Selector Dialog */}
      <TemplateSelector
        open={showTemplateSelector}
        onClose={() => setShowTemplateSelector(false)}
        onSelect={handleTemplateSelect}
        onEdit={handleEditTemplate}
        onDelete={handleDeleteTemplate}
        templates={Array.isArray(templates) ? templates : []}
      />

      <AgentPermissionsDialog
        open={permissionsDialog.open}
        onClose={() => setPermissionsDialog({ open: false, agent: null })}
        agentId={permissionsDialog.agent?._key || ''}
        agentName={permissionsDialog.agent?.name || ''}
      />
    </Box>
  );
};

export default AgentsManagement;

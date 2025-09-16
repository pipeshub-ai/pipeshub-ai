import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Icon } from '@iconify/react';
import arrowUpIcon from '@iconify-icons/mdi/arrow-up';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import plusIcon from '@iconify-icons/mdi/plus';
import closeIcon from '@iconify-icons/mdi/close';
import searchIcon from '@iconify-icons/mdi/magnify';
import toolIcon from '@iconify-icons/mdi/tools';
import databaseIcon from '@iconify-icons/mdi/database';
import sparklesIcon from '@iconify-icons/mdi/star-four-points';
import cogIcon from '@iconify-icons/mdi/cog';
import checkIcon from '@iconify-icons/mdi/check';
import {
  Box,
  Paper,
  IconButton,
  useTheme,
  alpha,
  Menu,
  MenuItem,
  Typography,
  Chip,
  Tooltip,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  InputAdornment,
  Grid,
  Button,
  Badge,
  Autocomplete,
  Tabs,
  Tab
} from '@mui/material';
import { Connector } from 'src/sections/accountdetails/connectors/types/types';
import { KnowledgeBase } from '../services/api';

export interface Model {
  provider: string;
  modelName: string;
}

export interface ChatMode {
  id: string;
  name: string;
  description: string;
}

export type ChatInputProps = {
  onSubmit: (
    message: string,
    modelKey?: string,
    modelName?: string,
    chatMode?: string,
    selectedTools?: string[],
    selectedKBs?: string[],
    selectedApps?: string[]
  ) => Promise<void>;
  isLoading: boolean;
  disabled?: boolean;
  placeholder?: string;
  selectedModel: Model | null;
  selectedChatMode: ChatMode | null;
  onModelChange: (model: Model) => void;
  onChatModeChange: (mode: ChatMode) => void;
  availableModels: Model[];
  availableKBs: KnowledgeBase[];
  agent?: any;
  activeConnectors: Connector[];
};

// Define chat modes with compact styling
const CHAT_MODES: ChatMode[] = [
  {
    id: 'standard',
    name: 'Standard',
    description: 'Standard responses',
  },
  {
    id: 'quick',
    name: 'Quick',
    description: 'Fast responses with minimal context',
  },
];

interface ToolOption {
  id: string; // This will be app_name.tool_name format
  label: string;
  displayName: string;
  app_name: string;
  tool_name: string;
  description: string;
}

interface KBOption {
  id: string; // This will be the KB ID
  name: string;
  description?: string;
}

interface AppOption {
  id: string; // This will be the app name
  name: string;
  displayName: string;
}

// Utility function to normalize names
const normalizeDisplayName = (name: string): string =>
  name
    .split('_')
    .map((word) => {
      const upperWord = word.toUpperCase();
      if (
        [
          'ID',
          'URL',
          'API',
          'UI',
          'DB',
          'AI',
          'ML',
          'KB',
          'PDF',
          'CSV',
          'JSON',
          'XML',
          'HTML',
          'CSS',
          'JS',
          'GCP',
          'AWS',
        ].includes(upperWord)
      ) {
        return upperWord;
      }
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(' ');

const AgentChatInput: React.FC<ChatInputProps> = ({
  onSubmit,
  isLoading,
  disabled = false,
  placeholder = 'Type your message...',
  selectedModel,
  selectedChatMode,
  onModelChange,
  onChatModeChange,
  availableModels,
  availableKBs,
  agent,
  activeConnectors,
}) => {
  const [localValue, setLocalValue] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [hasText, setHasText] = useState(false);
  const [modelMenuAnchor, setModelMenuAnchor] = useState<null | HTMLElement>(null);

  // Persistent selected items - these will remain selected throughout the conversation
  const [selectedTools, setSelectedTools] = useState<string[]>([]); // app_name.tool_name format
  const [selectedKBs, setSelectedKBs] = useState<string[]>([]); // KB IDs
  const [selectedApps, setSelectedApps] = useState<string[]>([]); // App names
  const [initialized, setInitialized] = useState(false);

  // Dialog states
  const [selectionDialogOpen, setSelectionDialogOpen] = useState(false);
  const [dialogTab, setDialogTab] = useState(0);
  const [toolSearchTerm, setToolSearchTerm] = useState('');
  const [kbSearchTerm, setKbSearchTerm] = useState('');
  const [appSearchTerm, setAppSearchTerm] = useState('');

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const resizeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // Set defaults for model and chat mode
  useEffect(() => {
    if (!selectedChatMode && CHAT_MODES.length > 0) {
      onChatModeChange(CHAT_MODES[0]);
    }
    if (!selectedModel && availableModels.length > 0) {
      onModelChange(availableModels[0]);
    }
  }, [selectedChatMode, onChatModeChange, selectedModel, availableModels, onModelChange]);

  // Initialize selections from agent defaults (only once)
  useEffect(() => {
    if (agent && !initialized) {
      // Initialize with agent's default tools
      if (agent.tools && Array.isArray(agent.tools)) {
        setSelectedTools([...agent.tools]);
      }

      // Initialize with agent's default KBs
      if (agent.kb && Array.isArray(agent.kb)) {
        setSelectedKBs([...agent.kb]);
      }

      // Initialize with agent's default apps
      if (agent.apps && Array.isArray(agent.apps)) {
        setSelectedApps([...agent.apps]);
      }

      setInitialized(true);
      console.log('Initialized selections from agent:', {
        tools: agent.tools,
        kb: agent.kb,
        apps: agent.apps,
      });
    }
  }, [agent, initialized]);

  // Convert agent tools to tool options
  const agentToolOptions: ToolOption[] = useMemo(() => {
    if (!agent?.tools) return [];

    return agent.tools.map((toolId: string) => {
      const [app_name, tool_name] = toolId.split('.');
      return {
        id: toolId, // Keep full app_name.tool_name format for API
        label: toolId,
        displayName: `${normalizeDisplayName(app_name || '')} • ${normalizeDisplayName(tool_name || '')}`,
        app_name: app_name || '',
        tool_name: tool_name || '',
        description: `${normalizeDisplayName(app_name || '')} ${normalizeDisplayName(tool_name || '')} tool`,
      };
    });
  }, [agent?.tools]);

  // Convert available KBs to KB options (filter by agent's KB list)
  const agentKBOptions: KBOption[] = useMemo(() => {
    if (!agent?.kb || !availableKBs) return [];

    return availableKBs
      .filter((kb) => agent.kb.includes(kb.id))
      .map((kb) => ({
        id: kb.id, // Use KB ID for API
        name: kb.name,
        description: `Knowledge Base: ${kb.name}`,
      }));
  }, [availableKBs, agent?.kb]);

  // Convert agent apps to app options
  const agentAppOptions: AppOption[] = useMemo(() => {
    if (!agent?.apps) return [];

    return agent.apps.map((appName: string) => ({
      id: appName, // Use app name for API
      name: appName,
      displayName: normalizeDisplayName(appName),
    }));
  }, [agent?.apps]);

  // All available apps for autocomplete
  const allAppOptions: AppOption[] = activeConnectors.map((app) => ({
    id: app.name, // Use app name for API
    name: app.name,
    displayName: normalizeDisplayName(app.name),
  }));

  // Filtered options
  const filteredTools = useMemo(() => {
    if (!toolSearchTerm) return agentToolOptions;
    return agentToolOptions.filter(
      (tool) =>
        tool.displayName.toLowerCase().includes(toolSearchTerm.toLowerCase()) ||
        tool.description.toLowerCase().includes(toolSearchTerm.toLowerCase())
    );
  }, [agentToolOptions, toolSearchTerm]);

  const filteredKBs = useMemo(() => {
    if (!kbSearchTerm) return agentKBOptions;
    return agentKBOptions.filter(
      (kb) =>
        kb.name.toLowerCase().includes(kbSearchTerm.toLowerCase()) ||
        (kb.description && kb.description.toLowerCase().includes(kbSearchTerm.toLowerCase()))
    );
  }, [agentKBOptions, kbSearchTerm]);

  const filteredApps = useMemo(() => {
    const appsToFilter = agentAppOptions.length > 0 ? agentAppOptions : allAppOptions;
    if (!appSearchTerm) return appsToFilter;
    return appsToFilter.filter(
      (app) =>
        app.displayName.toLowerCase().includes(appSearchTerm.toLowerCase()) ||
        app.name.toLowerCase().includes(appSearchTerm.toLowerCase())
    );
  }, [agentAppOptions, allAppOptions, appSearchTerm]);

  const autoResizeTextarea = useCallback(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      const newHeight = Math.min(Math.max(inputRef.current.scrollHeight, 40), 100);
      inputRef.current.style.height = `${newHeight}px`;
    }
  }, []);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value;
      setLocalValue(value);
      setHasText(!!value.trim());

      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current);
      }
      resizeTimeoutRef.current = setTimeout(autoResizeTextarea, 50);
    },
    [autoResizeTextarea]
  );

  const handleSubmit = useCallback(async () => {
    const trimmedValue = localValue.trim();
    if (!trimmedValue || isLoading || isSubmitting || disabled) {
      return;
    }

    setIsSubmitting(true);

    try {
      // Clear only the input text, keep all selections persistent
      setLocalValue('');
      setHasText(false);

      if (inputRef.current) {
        setTimeout(() => {
          if (inputRef.current) {
            inputRef.current.style.height = '40px';
          }
        }, 50);
      }

      console.log('Submitting with persistent selections:', {
        tools: selectedTools,
        kbs: selectedKBs,
        apps: selectedApps,
        chatMode: selectedChatMode?.id,
      });   
      // Pass the persistent selected items with correct IDs/names for API
      await onSubmit(
        trimmedValue,
        selectedModel?.modelName,        
        selectedModel?.modelName,        
        selectedChatMode?.id,            
        selectedTools,                  
        selectedKBs,                     
        selectedApps                     
      );
    } catch (error) {
      console.error('Failed to send message:', error);
      setLocalValue(trimmedValue);
      setHasText(true);
    } finally {
      setIsSubmitting(false);
      if (inputRef.current) {
        inputRef.current.focus();
      }
    }
  }, [
    localValue,
    isLoading,
    isSubmitting,
    disabled,
    onSubmit,
    selectedModel,
    selectedChatMode,
    selectedTools, // These remain persistent
    selectedKBs, // These remain persistent
    selectedApps, // These remain persistent
  ]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit]
  );

  const handleModelMenuOpen = (event: React.MouseEvent<HTMLElement>) => {
    setModelMenuAnchor(event.currentTarget);
  };

  const handleModelMenuClose = () => {
    setModelMenuAnchor(null);
  };

  const handleModelSelect = (model: Model) => {
    onModelChange(model);
    handleModelMenuClose();
  };

  const handleChatModeSelect = (mode: ChatMode) => {
    onChatModeChange(mode);
    console.log('Chat mode changed to:', mode.id);
  };

  // Toggle functions - using the correct IDs for API - these are persistent
  const handleToolToggle = (toolId: string) => {
    setSelectedTools((prev) => {
      const newSelection = prev.includes(toolId)
        ? prev.filter((id) => id !== toolId)
        : [...prev, toolId];
      console.log('Tools selection updated:', newSelection);
      return newSelection;
    });
  };

  const handleKBToggle = (kbId: string) => {
    setSelectedKBs((prev) => {
      const newSelection = prev.includes(kbId) ? prev.filter((id) => id !== kbId) : [...prev, kbId];
      console.log('KBs selection updated:', newSelection);
      return newSelection;
    });
  };

  const handleAppToggle = (appId: string) => {
    setSelectedApps((prev) => {
      const newSelection = prev.includes(appId)
        ? prev.filter((id) => id !== appId)
        : [...prev, appId];
      console.log('Apps selection updated:', newSelection);
      return newSelection;
    });
  };

  const handleDialogClose = () => {
    setSelectionDialogOpen(false);
    setDialogTab(0);
    setToolSearchTerm('');
    setKbSearchTerm('');
    setAppSearchTerm('');
  };

  // Reset all selections to agent defaults
  const handleResetToDefaults = useCallback(() => {
    if (agent) {
      setSelectedTools(agent.tools ? [...agent.tools] : []);
      setSelectedKBs(agent.kb ? [...agent.kb] : []);
      setSelectedApps(agent.apps ? [...agent.apps] : []);
      console.log('Reset selections to agent defaults');
    }
  }, [agent]);

  // Clear all selections
  const handleClearAll = useCallback(() => {
    setSelectedTools([]);
    setSelectedKBs([]);
    setSelectedApps([]);
    console.log('Cleared all selections');
  }, []);

  const isInputDisabled = disabled || isSubmitting || isLoading;
  const canSubmit = hasText && !isInputDisabled;
  const totalSelectedItems = selectedTools.length + selectedKBs.length + selectedApps.length;

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();
    }
    return () => {
      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current);
      }
    };
  }, []);

  return (
    <>
      <style>
        {`
          @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }
        `}
      </style>

      <Box sx={{ p: 0.5, width: '100%', maxWidth: '800px', mx: 'auto' }}>
        {/* Main Input Container */}
        <Paper
          elevation={0}
          sx={{
            display: 'flex',
            flexDirection: 'column',
            borderRadius: '16px',
            backgroundColor: isDark ? alpha('#000', 0.2) : alpha('#f8f9fa', 0.8),
            border: '1px solid',
            borderColor: isDark ? alpha('#fff', 0.1) : alpha('#000', 0.08),
            boxShadow: isDark ? '0 4px 16px rgba(0, 0, 0, 0.2)' : '0 2px 8px rgba(0, 0, 0, 0.06)',
            transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
            '&:hover': {
              borderColor: isDark ? alpha('#fff', 0.15) : alpha('#000', 0.12),
              boxShadow: isDark
                ? '0 6px 20px rgba(0, 0, 0, 0.3)'
                : '0 4px 12px rgba(0, 0, 0, 0.08)',
            },
            '&:focus-within': {
              borderColor: theme.palette.primary.main,
              boxShadow: `0 0 0 2px ${alpha(theme.palette.primary.main, 0.1)}`,
            },
          }}
        >
          {/* Text Input Area */}
          <Box sx={{ px: 2, py: 1, display: 'flex', alignItems: 'flex-end', gap: 1.5 }}>
            <Box sx={{ flex: 1 }}>
              <textarea
                ref={inputRef}
                placeholder={placeholder}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                value={localValue}
                disabled={isInputDisabled}
                style={{
                  width: '100%',
                  border: 'none',
                  outline: 'none',
                  background: 'transparent',
                  color: isDark ? alpha('#fff', 0.95).toString() : alpha('#000', 0.9).toString(),
                  fontSize: '15px',
                  lineHeight: 1.4,
                  minHeight: '20px',
                  maxHeight: '80px',
                  resize: 'none',
                  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                  overflowY: 'auto',
                  cursor: 'text',
                  opacity: isInputDisabled ? 0.6 : 1,
                }}
              />
            </Box>

            {/* Send Button */}
            <IconButton
              onClick={handleSubmit}
              disabled={!canSubmit}
              sx={{
                backgroundColor: canSubmit
                  ? theme.palette.primary.main
                  : alpha(theme.palette.action.disabled, 0.2),
                width: 32,
                height: 32,
                borderRadius: '16px',
                color: canSubmit ? '#fff' : theme.palette.action.disabled,
                transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
                '&:hover': canSubmit
                  ? {
                      backgroundColor: theme.palette.primary.dark,
                      transform: 'scale(1.05)',
                    }
                  : {},
                '&:active': {
                  transform: canSubmit ? 'scale(0.95)' : 'none',
                },
                '&.Mui-disabled': {
                  backgroundColor: alpha(theme.palette.action.disabled, 0.1),
                },
              }}
            >
              {isSubmitting ? (
                <Box
                  component="span"
                  sx={{
                    width: 14,
                    height: 14,
                    border: '2px solid transparent',
                    borderTop: '2px solid currentColor',
                    borderRadius: '50%',
                    animation: 'spin 1s linear infinite',
                  }}
                />
              ) : (
                <Icon icon={arrowUpIcon} width={16} height={16} />
              )}
            </IconButton>
          </Box>

          {/* Bottom Bar - Chat Modes & Controls */}
          <Box
            sx={{
              px: 2,
              py: 0.5,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderTop: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
            }}
          >
            {/* Chat Mode Buttons */}
            <Box sx={{ display: 'flex', gap: 1 }}>
              {CHAT_MODES.map((mode) => (
                <Chip
                  key={mode.id}
                  label={mode.name}
                  onClick={() => handleChatModeSelect(mode)}
                  size="small"
                  variant={selectedChatMode?.id === mode.id ? 'filled' : 'outlined'}
                  icon={<Icon icon={sparklesIcon} width={12} height={12} />}
                  sx={{
                    height: 24,
                    fontSize: '0.7rem',
                    fontWeight: 500,
                    borderRadius: '12px',
                    cursor: 'pointer',
                    '& .MuiChip-icon': { width: 12, height: 12 },
                    color: selectedChatMode?.id === mode.id ? '#fff' : theme.palette.text.secondary,
                    bgcolor:
                      selectedChatMode?.id === mode.id ? theme.palette.primary.main : 'transparent',
                    borderColor:
                      selectedChatMode?.id === mode.id
                        ? theme.palette.primary.main
                        : alpha(theme.palette.divider, 0.5),
                    '&:hover': {
                      borderColor: theme.palette.primary.main,
                      bgcolor:
                        selectedChatMode?.id === mode.id
                          ? theme.palette.primary.dark
                          : alpha(theme.palette.primary.main, 0.1),
                    },
                  }}
                />
              ))}
            </Box>

            {/* Right Controls */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              {/* Resources Button */}
              <Tooltip title="Add tools, knowledge & apps">
                <Badge badgeContent={totalSelectedItems} color="primary" max={99}>
                  <IconButton
                    onClick={() => setSelectionDialogOpen(true)}
                    size="small"
                    sx={{
                      width: 28,
                      height: 28,
                      bgcolor:
                        totalSelectedItems > 0
                          ? alpha(theme.palette.primary.main, 0.1)
                          : 'transparent',
                      border: '1px solid',
                      borderColor:
                        totalSelectedItems > 0
                          ? theme.palette.primary.main
                          : alpha(theme.palette.divider, 0.5),
                      color:
                        totalSelectedItems > 0
                          ? theme.palette.primary.main
                          : theme.palette.text.secondary,
                      '&:hover': {
                        bgcolor: alpha(theme.palette.primary.main, 0.1),
                        borderColor: theme.palette.primary.main,
                      },
                    }}
                  >
                    <Icon icon={plusIcon} width={14} height={14} />
                  </IconButton>
                </Badge>
              </Tooltip>

              {/* Model Selector */}
              <Tooltip title={`Model: ${selectedModel?.modelName || 'Select'}`}>
                <Button
                  onClick={handleModelMenuOpen}
                  size="small"
                  endIcon={<Icon icon={chevronDownIcon} width={12} height={12} />}
                  sx={{
                    minWidth: 'auto',
                    height: 28,
                    px: 1.5,
                    fontSize: '0.7rem',
                    fontWeight: 500,
                    color: theme.palette.text.secondary,
                    border: '1px solid',
                    borderColor: alpha(theme.palette.divider, 0.5),
                    borderRadius: '8px',
                    textTransform: 'none',
                    '&:hover': {
                      borderColor: theme.palette.primary.main,
                      bgcolor: alpha(theme.palette.primary.main, 0.05),
                    },
                  }}
                >
                  {selectedModel?.modelName?.slice(0, 16) || 'Model'}
                </Button>
              </Tooltip>
            </Box>
          </Box>
        </Paper>
      </Box>

      {/* Model Selection Menu */}
      <Menu
        anchorEl={modelMenuAnchor}
        open={Boolean(modelMenuAnchor)}
        onClose={handleModelMenuClose}
        PaperProps={{
          sx: {
            maxHeight: 240,
            minWidth: 200,
            borderRadius: '8px',
            border: `1px solid ${isDark ? alpha('#fff', 0.1) : alpha('#000', 0.1)}`,
            boxShadow: isDark ? '0 4px 16px rgba(0, 0, 0, 0.3)' : '0 4px 16px rgba(0, 0, 0, 0.1)',
          },
        }}
      >
        <Box sx={{ p: 1 }}>
          <Typography
            variant="caption"
            sx={{ px: 1, pb: 0.5, color: 'text.secondary', display: 'block' }}
          >
            AI Models
          </Typography>
          <Divider sx={{ mb: 0.5 }} />
          {availableModels.map((model) => (
            <MenuItem
              key={`${model.provider}-${model.modelName}`}
              onClick={() => handleModelSelect(model)}
              selected={selectedModel?.modelName === model.modelName}
              sx={{ borderRadius: '6px', mb: 0.5, py: 0.5 }}
            >
              <Box>
                <Typography variant="body2" fontWeight="medium" sx={{ fontSize: '0.8rem' }}>
                  {model.modelName}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                  {normalizeDisplayName(model.provider)}
                </Typography>
              </Box>
            </MenuItem>
          ))}
        </Box>
      </Menu>

      {/* Compact Resources Selection Dialog */}
      <Dialog
        open={selectionDialogOpen}
        onClose={handleDialogClose}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2,
            maxHeight: '80vh',
          },
        }}
      >
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            pb: 1,
          }}
        >
          <Typography variant="h6" sx={{ fontSize: '1.1rem' }}>
            Select Resources
          </Typography>
          <IconButton onClick={handleDialogClose} size="small">
            <Icon icon={closeIcon} width={18} height={18} />
          </IconButton>
        </DialogTitle>

        <DialogContent sx={{ pt: 0, pb: 1 }}>
          {/* Tabs for different resource types */}
          <Tabs
            value={dialogTab}
            onChange={(_, newValue) => setDialogTab(newValue)}
            variant="fullWidth"
            sx={{ mb: 2 }}
          >
            <Tab
              label={`Tools ${selectedTools.length > 0 ? `(${selectedTools.length})` : ''}`}
              icon={<Icon icon={toolIcon} width={16} height={16} />}
              iconPosition="start"
              sx={{ minHeight: 'auto', py: 1, fontSize: '0.8rem' }}
            />
            <Tab
              label={`Knowledge ${selectedKBs.length > 0 ? `(${selectedKBs.length})` : ''}`}
              icon={<Icon icon={databaseIcon} width={16} height={16} />}
              iconPosition="start"
              sx={{ minHeight: 'auto', py: 1, fontSize: '0.8rem' }}
            />
            <Tab
              label={`Apps ${selectedApps.length > 0 ? `(${selectedApps.length})` : ''}`}
              icon={<Icon icon={cogIcon} width={16} height={16} />}
              iconPosition="start"
              sx={{ minHeight: 'auto', py: 1, fontSize: '0.8rem' }}
            />
          </Tabs>

          {/* Tools Tab */}
          {dialogTab === 0 && (
            <Box>
              {agentToolOptions.length > 0 ? (
                <>
                  <TextField
                    fullWidth
                    placeholder="Search tools..."
                    value={toolSearchTerm}
                    onChange={(e) => setToolSearchTerm(e.target.value)}
                    size="small"
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <Icon icon={searchIcon} width={14} height={14} />
                        </InputAdornment>
                      ),
                    }}
                    sx={{ mb: 1.5 }}
                  />

                  <Box sx={{ maxHeight: 300, overflow: 'auto' }}>
                    <Grid container spacing={1}>
                      {filteredTools.map((tool) => (
                        <Grid item xs={12} key={tool.id}>
                          <Paper
                            sx={{
                              p: 1.5,
                              cursor: 'pointer',
                              border: 1,
                              borderColor: selectedTools.includes(tool.id)
                                ? 'primary.main'
                                : 'divider',
                              bgcolor: selectedTools.includes(tool.id)
                                ? alpha(theme.palette.primary.main, 0.1)
                                : 'background.paper',
                              borderRadius: 1,
                              '&:hover': {
                                borderColor: 'primary.main',
                                bgcolor: alpha(theme.palette.primary.main, 0.05),
                              },
                            }}
                            onClick={() => handleToolToggle(tool.id)}
                          >
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <Icon icon={toolIcon} width={16} height={16} />
                              <Box sx={{ flex: 1 }}>
                                <Typography
                                  variant="body2"
                                  fontWeight="medium"
                                  sx={{ fontSize: '0.8rem' }}
                                >
                                  {tool.displayName}
                                </Typography>
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  sx={{ fontSize: '0.65rem' }}
                                >
                                  {tool.description}
                                </Typography>
                              </Box>
                              {selectedTools.includes(tool.id) && (
                                <Icon
                                  icon={checkIcon}
                                  width={16}
                                  height={16}
                                  color={theme.palette.primary.main}
                                />
                              )}
                            </Box>
                          </Paper>
                        </Grid>
                      ))}
                    </Grid>
                  </Box>
                </>
              ) : (
                <Box sx={{ textAlign: 'center', py: 3 }}>
                  <Typography variant="body2" color="text.secondary">
                    No tools configured for this agent
                  </Typography>
                </Box>
              )}
            </Box>
          )}

          {/* Knowledge Bases Tab */}
          {dialogTab === 1 && (
            <Box>
              {agentKBOptions.length > 0 ? (
                <>
                  <TextField
                    fullWidth
                    placeholder="Search knowledge bases..."
                    value={kbSearchTerm}
                    onChange={(e) => setKbSearchTerm(e.target.value)}
                    size="small"
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <Icon icon={searchIcon} width={14} height={14} />
                        </InputAdornment>
                      ),
                    }}
                    sx={{ mb: 1.5 }}
                  />

                  <Box sx={{ maxHeight: 300, overflow: 'auto' }}>
                    <Grid container spacing={1}>
                      {filteredKBs.map((kb) => (
                        <Grid item xs={12} key={kb.id}>
                          <Paper
                            sx={{
                              p: 1.5,
                              cursor: 'pointer',
                              border: 1,
                              borderColor: selectedKBs.includes(kb.id) ? 'info.main' : 'divider',
                              bgcolor: selectedKBs.includes(kb.id)
                                ? alpha(theme.palette.info.main, 0.1)
                                : 'background.paper',
                              borderRadius: 1,
                              '&:hover': {
                                borderColor: 'info.main',
                                bgcolor: alpha(theme.palette.info.main, 0.05),
                              },
                            }}
                            onClick={() => handleKBToggle(kb.id)}
                          >
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <Icon icon={databaseIcon} width={16} height={16} />
                              <Box sx={{ flex: 1 }}>
                                <Typography
                                  variant="body2"
                                  fontWeight="medium"
                                  sx={{ fontSize: '0.8rem' }}
                                >
                                  {kb.name}
                                </Typography>
                                {kb.description && (
                                  <Typography
                                    variant="caption"
                                    color="text.secondary"
                                    sx={{ fontSize: '0.65rem' }}
                                  >
                                    {kb.description}
                                  </Typography>
                                )}
                              </Box>
                              {selectedKBs.includes(kb.id) && (
                                <Icon
                                  icon={checkIcon}
                                  width={16}
                                  height={16}
                                  color={theme.palette.info.main}
                                />
                              )}
                            </Box>
                          </Paper>
                        </Grid>
                      ))}
                    </Grid>
                  </Box>
                </>
              ) : (
                <Box sx={{ textAlign: 'center', py: 3 }}>
                  <Typography variant="body2" color="text.secondary">
                    No knowledge bases configured for this agent
                  </Typography>
                </Box>
              )}
            </Box>
          )}

          {/* Applications Tab */}
          {dialogTab === 2 && (
            <Box>
              {agentAppOptions.length > 0 ? (
                <>
                  <TextField
                    fullWidth
                    placeholder="Search applications..."
                    value={appSearchTerm}
                    onChange={(e) => setAppSearchTerm(e.target.value)}
                    size="small"
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <Icon icon={searchIcon} width={14} height={14} />
                        </InputAdornment>
                      ),
                    }}
                    sx={{ mb: 1.5 }}
                  />

                  <Box sx={{ maxHeight: 300, overflow: 'auto' }}>
                    <Grid container spacing={1}>
                      {filteredApps.map((app) => (
                        <Grid item xs={12} sm={6} key={app.id}>
                          <Paper
                            sx={{
                              p: 1.5,
                              cursor: 'pointer',
                              border: 1,
                              borderColor: selectedApps.includes(app.id)
                                ? 'warning.main'
                                : 'divider',
                              bgcolor: selectedApps.includes(app.id)
                                ? alpha(theme.palette.warning.main, 0.1)
                                : 'background.paper',
                              borderRadius: 1,
                              '&:hover': {
                                borderColor: 'warning.main',
                                bgcolor: alpha(theme.palette.warning.main, 0.05),
                              },
                            }}
                            onClick={() => handleAppToggle(app.id)}
                          >
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <Icon icon={cogIcon} width={16} height={16} />
                              <Box sx={{ flex: 1 }}>
                                <Typography
                                  variant="body2"
                                  fontWeight="medium"
                                  sx={{ fontSize: '0.8rem' }}
                                >
                                  {app.displayName}
                                </Typography>
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  sx={{ fontSize: '0.65rem' }}
                                >
                                  {app.name}
                                </Typography>
                              </Box>
                              {selectedApps.includes(app.id) && (
                                <Icon
                                  icon={checkIcon}
                                  width={16}
                                  height={16}
                                  color={theme.palette.warning.main}
                                />
                              )}
                            </Box>
                          </Paper>
                        </Grid>
                      ))}
                    </Grid>
                  </Box>
                </>
              ) : (
                <>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    No apps configured. Add from available apps:
                  </Typography>

                  <Autocomplete
                    freeSolo
                    options={allAppOptions}
                    getOptionLabel={(option) =>
                      typeof option === 'string' ? option : option.displayName
                    }
                    value=""
                    onChange={(_, value) => {
                      if (value && typeof value === 'object') {
                        handleAppToggle(value.id);
                      }
                    }}
                    renderInput={(params) => (
                      <TextField
                        {...params}
                        placeholder="Search and add applications..."
                        size="small"
                        InputProps={{
                          ...params.InputProps,
                          startAdornment: (
                            <InputAdornment position="start">
                              <Icon icon={searchIcon} width={14} height={14} />
                            </InputAdornment>
                          ),
                        }}
                      />
                    )}
                    renderOption={(props, option) => (
                      <Box component="li" {...props}>
                        <Box>
                          <Typography
                            variant="body2"
                            fontWeight="medium"
                            sx={{ fontSize: '0.8rem' }}
                          >
                            {option.displayName}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontSize: '0.65rem' }}
                          >
                            {option.name}
                          </Typography>
                        </Box>
                      </Box>
                    )}
                    sx={{ mb: 2 }}
                  />

                  {selectedApps.length > 0 && (
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                      {selectedApps.map((appId) => {
                        const app = allAppOptions.find((a) => a.id === appId);
                        return (
                          <Chip
                            key={appId}
                            label={app?.displayName || appId}
                            onDelete={() => handleAppToggle(appId)}
                            size="small"
                            color="warning"
                            variant="outlined"
                            sx={{ height: 20, fontSize: '0.65rem' }}
                          />
                        );
                      })}
                    </Box>
                  )}
                </>
              )}
            </Box>
          )}
        </DialogContent>

        <DialogActions sx={{ px: 3, pb: 2, pt: 1 }}>
          <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
            {totalSelectedItems} items selected
          </Typography>
          <Button
            onClick={handleDialogClose}
            variant="contained"
            size="small"
            sx={{ borderRadius: 1.5 }}
          >
            Done
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default AgentChatInput;

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
  modelKey: string;
  modelName: string;
  modelFriendlyName?: string;
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

// Helper function to get model display name
const getModelDisplayName = (model: { modelName: string; modelFriendlyName?: string } | null): string => {
  if (!model) return '';
  return model.modelFriendlyName || model.modelName;
};

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

  // Note: Model and chat mode defaults are handled by the parent component (agent-chat.tsx)
  // The parent will set the model from conversation if available, or set defaults if not
  // This component just respects what's passed to it via props

  // Initialize selections from agent defaults (only once)
  // Uses only the new graph-based format (toolsets and knowledge)
  useEffect(() => {
    if (agent && !initialized) {
      // Initialize with agent's tools from toolsets
      const toolsList: string[] = [];
      if (agent.toolsets && Array.isArray(agent.toolsets)) {
        agent.toolsets.forEach((toolset: any) => {
          const tools = toolset.tools || [];
          const selectedToolsFromToolset = toolset.selectedTools || [];
          
          // Extract from expanded tools array
          if (tools.length > 0) {
            tools.forEach((tool: any) => {
              const fullName = tool.fullName || `${toolset.name}.${tool.name}`;
              toolsList.push(fullName);
            });
          } else if (selectedToolsFromToolset.length > 0) {
            // Fallback to selectedTools if no expanded tools
            selectedToolsFromToolset.forEach((toolName: string) => {
              const fullName = toolName.includes('.') ? toolName : `${toolset.name}.${toolName}`;
              toolsList.push(fullName);
            });
          }
        });
      }
      // Note: All tools selected by default means user can filter down
      if (toolsList.length > 0) {
        setSelectedTools(toolsList);
      }

      // Initialize with agent's knowledge sources (includes both apps and KBs)
      if (agent.knowledge && Array.isArray(agent.knowledge)) {
        const knowledgeIds: string[] = [];
        const kbIds: string[] = [];
        
        agent.knowledge.forEach((k: any) => {
          const connectorId = k.connectorId;
          if (connectorId) {
            // Parse filters to extract KB record groups
            const filters = k.filtersParsed || k.filters || {};
            let filtersParsed = filters;
            if (typeof filters === 'string') {
              try {
                filtersParsed = JSON.parse(filters);
              } catch {
                filtersParsed = {};
              }
            }
            
            const recordGroups = filtersParsed.recordGroups || [];
            
            // Check if this is a KB (has recordGroups)
            if (recordGroups.length > 0) {
              // Extract KB IDs from recordGroups
              kbIds.push(...recordGroups);
            } else {
              // This is an app connector
              knowledgeIds.push(connectorId);
            }
          }
        });
        
        if (knowledgeIds.length > 0) {
          setSelectedApps(knowledgeIds);
        }
        
        if (kbIds.length > 0) {
          setSelectedKBs([...new Set(kbIds)]); // Remove duplicates
        }
      }

      setInitialized(true);
    }
  }, [agent, initialized]);

  // Convert agent toolsets to tool options for the selection dialog
  // Uses only the new graph-based format (toolsets with nested tools)
  const agentToolOptions: ToolOption[] = useMemo(() => {
    const toolOptions: ToolOption[] = [];
    
    if (agent?.toolsets && Array.isArray(agent.toolsets)) {
      agent.toolsets.forEach((toolset: any) => {
        const toolsetName = toolset.name || '';
        const tools = toolset.tools || [];
        const selectedToolsFromToolset = toolset.selectedTools || [];
        
        // Extract from expanded tools array
        if (tools.length > 0) {
          tools.forEach((tool: any) => {
            const fullName = tool.fullName || `${toolsetName}.${tool.name}`;
            const parts = fullName.split('.');
            const app_name = parts[0] || toolsetName;
            const tool_name = parts.slice(1).join('.') || tool.name || 'tool';
            
            toolOptions.push({
              id: fullName,
              label: fullName,
              displayName: `${normalizeDisplayName(app_name)} • ${normalizeDisplayName(tool_name)}`,
              app_name,
              tool_name,
              description: tool.description || `${normalizeDisplayName(app_name)} ${normalizeDisplayName(tool_name)} tool`,
            });
          });
        } else if (selectedToolsFromToolset.length > 0) {
          // Fallback to selectedTools if no expanded tools
          selectedToolsFromToolset.forEach((toolName: string) => {
            const fullName = toolName.includes('.') ? toolName : `${toolsetName}.${toolName}`;
            const parts = fullName.split('.');
            const app_name = parts[0] || toolsetName;
            const tool_name = parts.slice(1).join('.') || toolName;
            
            toolOptions.push({
              id: fullName,
              label: fullName,
              displayName: `${normalizeDisplayName(app_name)} • ${normalizeDisplayName(tool_name)}`,
              app_name,
              tool_name,
              description: `${normalizeDisplayName(app_name)} ${normalizeDisplayName(tool_name)} tool`,
            });
          });
        }
      });
    }
    
    return toolOptions;
  }, [agent?.toolsets]);

  // Convert available KBs to KB options (extract from agent's knowledge array)
  const agentKBOptions: KBOption[] = useMemo(() => {
    if (!agent?.knowledge || !availableKBs) return [];
    
    // Extract KB IDs from knowledge array
    const kbIds: string[] = [];
    agent.knowledge.forEach((k: any) => {
      const filters = k.filtersParsed || k.filters || {};
      let filtersParsed = filters;
      if (typeof filters === 'string') {
        try {
          filtersParsed = JSON.parse(filters);
        } catch {
          filtersParsed = {};
        }
      }
      const recordGroups = filtersParsed.recordGroups || [];
      kbIds.push(...recordGroups);
    });

    return availableKBs
      .filter((kb) => kbIds.includes(kb.id))
      .map((kb) => ({
        id: kb.id, // Use KB ID for API
        name: kb.name,
        description: `Collection  : ${kb.name}`,
      }));
  }, [availableKBs, agent?.knowledge]);

  // Convert agent knowledge to app options for the selection dialog
  // Uses only the new graph-based format (knowledge with connectorId)
  // Filters out KBs (only shows app connectors)
  const agentAppOptions: AppOption[] = useMemo(() => {
    const appOptions: AppOption[] = [];
    
    if (agent?.knowledge && Array.isArray(agent.knowledge)) {
      agent.knowledge.forEach((k: any) => {
        // Parse filters to check if this is a KB
        const filters = k.filtersParsed || k.filters || {};
        let filtersParsed = filters;
        if (typeof filters === 'string') {
          try {
            filtersParsed = JSON.parse(filters);
          } catch {
            filtersParsed = {};
          }
        }
        
        const recordGroups = filtersParsed.recordGroups || [];
        
        // Only include app connectors (not KBs)
        // KBs have recordGroups, apps don't (or have empty recordGroups)
        if (recordGroups.length === 0) {
          const connectorId = k.connectorId || '';
          // Use displayName from backend if available, otherwise extract from connectorId
          const displayName = k.displayName || k.name || 
                            (connectorId.split('/').pop() || connectorId || 'Knowledge Source');
          
          appOptions.push({
            id: connectorId,
            name: displayName,
            displayName: normalizeDisplayName(displayName),
          });
        }
      });
    }
    
    return appOptions;
  }, [agent?.knowledge]);

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

      // Pass the persistent selected items with correct IDs/names for API
      await onSubmit(
        trimmedValue,
        selectedModel?.modelKey,        
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
  };

  // Toggle functions - using the correct IDs for API - these are persistent
  const handleToolToggle = (toolId: string) => {
    setSelectedTools((prev) => {
      const newSelection = prev.includes(toolId)
        ? prev.filter((id) => id !== toolId)
        : [...prev, toolId];
      return newSelection;
    });
  };

  const handleKBToggle = (kbId: string) => {
    setSelectedKBs((prev) => {
      const newSelection = prev.includes(kbId) ? prev.filter((id) => id !== kbId) : [...prev, kbId];
      return newSelection;
    });
  };

  const handleAppToggle = (appId: string) => {
    setSelectedApps((prev) => {
      const newSelection = prev.includes(appId)
        ? prev.filter((id) => id !== appId)
        : [...prev, appId];
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
  // Reset all selections to agent defaults
  // Uses only the new graph-based format (toolsets and knowledge)
  const handleResetToDefaults = useCallback(() => {
    if (agent) {
      // Reset tools from toolsets
      const toolsList: string[] = [];
      if (agent.toolsets && Array.isArray(agent.toolsets)) {
        agent.toolsets.forEach((toolset: any) => {
          const tools = toolset.tools || [];
          const selectedToolsFromToolset = toolset.selectedTools || [];
          
          if (tools.length > 0) {
            tools.forEach((tool: any) => {
              const fullName = tool.fullName || `${toolset.name}.${tool.name}`;
              toolsList.push(fullName);
            });
          } else if (selectedToolsFromToolset.length > 0) {
            selectedToolsFromToolset.forEach((toolName: string) => {
              const fullName = toolName.includes('.') ? toolName : `${toolset.name}.${toolName}`;
              toolsList.push(fullName);
            });
          }
        });
      }
      setSelectedTools(toolsList);
      
      // Extract KB IDs from knowledge array
      const kbIds: string[] = [];
      if (agent.knowledge && Array.isArray(agent.knowledge)) {
        agent.knowledge.forEach((k: any) => {
          const filters = k.filtersParsed || k.filters || {};
          let filtersParsed = filters;
          if (typeof filters === 'string') {
            try {
              filtersParsed = JSON.parse(filters);
            } catch {
              filtersParsed = {};
            }
          }
          const recordGroups = filtersParsed.recordGroups || [];
          kbIds.push(...recordGroups);
        });
      }
      setSelectedKBs([...new Set(kbIds)]); // Remove duplicates
      
      // Reset apps from knowledge (extract app connectors, not KBs)
      if (agent.knowledge && Array.isArray(agent.knowledge)) {
        const knowledgeIds: string[] = [];
        agent.knowledge.forEach((k: any) => {
          const connectorId = k.connectorId;
          if (connectorId) {
            // Parse filters to check if this is a KB
            const filters = k.filtersParsed || k.filters || {};
            let filtersParsed = filters;
            if (typeof filters === 'string') {
              try {
                filtersParsed = JSON.parse(filters);
              } catch {
                filtersParsed = {};
              }
            }
            
            // Only add if it's not a KB (no recordGroups or empty recordGroups)
            const recordGroups = filtersParsed.recordGroups || [];
            if (recordGroups.length === 0) {
              knowledgeIds.push(connectorId);
            }
          }
        });
        setSelectedApps(knowledgeIds);
      } else {
        setSelectedApps([]);
      }
    }
  }, [agent]);

  // Clear all selections
  const handleClearAll = useCallback(() => {
    setSelectedTools([]);
    setSelectedKBs([]);
    setSelectedApps([]);
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
              <Tooltip title={`Model: ${getModelDisplayName(selectedModel) || 'Select'} | ${selectedModel?.modelName.substring(0, 16) || ''}`}>
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
                  {getModelDisplayName(selectedModel)?.slice(0, 16) || 'Model'}
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
              key={`${model.provider || 'unknown'}-${model.modelName || 'model'}`}
              onClick={() => handleModelSelect(model)}
              selected={selectedModel?.modelName === model.modelName}
              sx={{ borderRadius: '6px', mb: 0.5, py: 0.5 }}
            >
              <Box>
                <Typography variant="body2" fontWeight="medium" sx={{ fontSize: '0.8rem' }}>
                  {getModelDisplayName(model)}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                  {`${normalizeDisplayName(model.provider || 'AI')} | ${model.modelName.substring(0, 16)}`}
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
                    placeholder="Search collections..."
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
                    No collections configured for this agent
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

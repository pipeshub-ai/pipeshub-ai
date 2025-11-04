import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Icon } from '@iconify/react';
import arrowUpIcon from '@iconify-icons/mdi/arrow-up';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import sparklesIcon from '@iconify-icons/mdi/star-four-points';
import filterIcon from '@iconify-icons/mdi/filter';

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
  Badge,
  Divider,
} from '@mui/material';
import axios from 'src/utils/axios';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';
import ChatBotFilters from './chat-bot-filters';

export interface Model {
  modelType: string;
  provider: string;
  modelName: string;
  modelKey: string;
  isMultimodal: boolean;
  isDefault: boolean;
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
    filters?: { apps: string[]; kb: string[] }
  ) => Promise<void>;
  isLoading: boolean;
  disabled?: boolean;
  placeholder?: string;
  selectedModel: Model | null;
  selectedChatMode: ChatMode | null;
  onModelChange: (model: Model) => void;
  onChatModeChange: (mode: ChatMode) => void;
  apps: Array<{ id: string; name: string; iconPath?: string }>;
  knowledgeBases: Array<{ id: string; name: string }>;
  initialSelectedApps?: string[];
  initialSelectedKbIds?: string[];
  onFiltersChange?: (filters: { apps: string[]; kb: string[] }) => void;
};

// Define chat modes locally in the frontend
const CHAT_MODES: ChatMode[] = [
  {
    id: 'quick',
    name: 'Quick',
    description: 'Quick responses with minimal context',
  },
  {
    id: 'standard',
    name: 'Standard',
    description: 'Balanced responses with moderate creativity',
  },
];

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

const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  azureOpenAI: 'Azure OpenAI',
  openAI: 'OpenAI',
  anthropic: 'Anthropic',
  gemini: 'Gemini',
  claude: 'Claude',
  ollama: 'Ollama',
  bedrock: 'AWS Bedrock',
  xai: 'xAI',
  together: 'Together',
  groq: 'Groq',
  fireworks: 'Fireworks',
  cohere: 'Cohere',
  openAICompatible: 'OpenAI API Compatible',
  mistral: 'Mistral',
  voyage: 'Voyage',
  jinaAI: 'Jina AI',
  sentenceTransformers: 'Default',
  default: 'Default',
};

export const formattedProvider = (provider: string): string =>
  PROVIDER_DISPLAY_NAMES[provider] || normalizeDisplayName(provider);

const ChatInput: React.FC<ChatInputProps> = ({
  onSubmit,
  isLoading,
  disabled = false,
  placeholder = 'Type your message...',
  selectedModel,
  selectedChatMode,
  onModelChange,
  onChatModeChange,
  apps,
  knowledgeBases,
  initialSelectedApps = [],
  initialSelectedKbIds = [],
  onFiltersChange,
}) => {
  const [localValue, setLocalValue] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [hasText, setHasText] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  const [modelMenuAnchor, setModelMenuAnchor] = useState<null | HTMLElement>(null);
  const [modeMenuAnchor, setModeMenuAnchor] = useState<null | HTMLElement>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  // Anchor for unified resources dropdown
  const [resourcesAnchor, setResourcesAnchor] = useState<null | HTMLElement>(null);
  const [selectedApps, setSelectedApps] = useState<string[]>(initialSelectedApps || []);
  const [selectedKbIds, setSelectedKbIds] = useState<string[]>(initialSelectedKbIds || []);
  // Unified dropdown states (single list: grouped sections)
  const [searchTerm, setSearchTerm] = useState('');

  const [showMoreApps, setShowMoreApps] = useState(false);
  const [showMoreKBs, setShowMoreKBs] = useState(false);
  const kbNameMap = useMemo(() => {
    const map = new Map<string, string>();
    knowledgeBases.forEach((kb) => map.set(kb.id, kb.name));
    return map;
  }, [knowledgeBases]);

  const [expandedSections, setExpandedSections] = useState({
    apps: true,
    kb: false,
  });

  const toggleSection = (section: 'apps' | 'kb') => {
    setExpandedSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  // Sync from parent only when props actually change
  useEffect(() => {
    const initialSet = new Set(initialSelectedApps || []);
    const currentSet = new Set(selectedApps);
    const same =
      initialSet.size === currentSet.size &&
      [...initialSet].every((value) => currentSet.has(value));
    if (!same) {
      setSelectedApps(initialSelectedApps || []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSelectedApps]);
  useEffect(() => {
    const initialSet = new Set(initialSelectedKbIds || []);
    const currentSet = new Set(selectedKbIds);
    const same =
      initialSet.size === currentSet.size &&
      [...initialSet].every((value) => currentSet.has(value));
    if (!same) {
      setSelectedKbIds(initialSelectedKbIds || []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSelectedKbIds]);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const resizeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const scrollableStyles = createScrollableContainerStyle(theme);
  const appItems = useMemo(() => apps || [], [apps]);
  // Prefetch app icons to avoid flicker when switching tabs
  useEffect(() => {
    try {
      (appItems || []).forEach((app) => {
        if (app?.iconPath) {
          const img = new Image();
          img.src = app.iconPath;
        }
      });
    } catch (e) {
      // ignore prefetch errors
    }
  }, [appItems]);

  // Memoized filtered lists for performance and stability
  const filteredApps = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (!term) return appItems;
    return appItems.filter((a) => (a?.name || '').toLowerCase().includes(term));
  }, [appItems, searchTerm]);

  const filteredKBs = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (!term) return knowledgeBases;
    return knowledgeBases.filter((kb) => (kb?.name || '').toLowerCase().includes(term));
  }, [knowledgeBases, searchTerm]);

  const fetchAvailableModels = async () => {
    try {
      setLoadingModels(true);
      const response = await axios.get('/api/v1/configurationManager/ai-models/available/llm');

      if (response.data.status === 'success') {
        setModels(response.data.models || []);

        // Set default model if not already selected
        if (!selectedModel && response.data.data && response.data.data.length > 0) {
          const defaultModel =
            response.data.data.find((model: Model) => model.isDefault) || response.data.data[0];
          onModelChange(defaultModel);
        }
      }
    } catch (error) {
      console.error('Failed to fetch available models:', error);
    } finally {
      setLoadingModels(false);
    }
  };

  // Set default chat mode if not already selected
  useEffect(() => {
    if (!selectedChatMode && CHAT_MODES.length > 0) {
      onChatModeChange(CHAT_MODES[0]); // Set first mode as default
    }
    if (!selectedModel && models.length > 0) {
      const defaultModel = models.find((model: Model) => model.isDefault) || models[0];
      onModelChange(defaultModel); // Set first model as default
    }
  }, [selectedChatMode, onChatModeChange, models, onModelChange, selectedModel]);

  useEffect(() => {
    fetchAvailableModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  
  const openResourcesMenu = (event: React.MouseEvent<HTMLElement>) =>
    setResourcesAnchor(event.currentTarget);
  const closeResourcesMenu = () => setResourcesAnchor(null);

  const toggleApp = (id: string) => {
    setSelectedApps((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]));
  };

  const toggleKb = (id: string) => {
    setSelectedKbIds((prev) => (prev.includes(id) ? prev.filter((k) => k !== id) : [...prev, id]));
  };

  // Notify parent about filters so first submit has correct filters
  const lastEmittedRef = useRef<{ apps: string[]; kb: string[] } | null>(null);
  useEffect(() => {
    if (!onFiltersChange) return;
    const payload = { apps: selectedApps, kb: selectedKbIds };
    const last = lastEmittedRef.current;
    const same =
      !!last &&
      last.apps.length === payload.apps.length &&
      last.kb.length === payload.kb.length &&
      last.apps.every((v, i) => v === payload.apps[i]) &&
      last.kb.every((v, i) => v === payload.kb[i]);
    if (!same) {
      lastEmittedRef.current = payload;
      onFiltersChange(payload);
    }
  }, [selectedApps, selectedKbIds, onFiltersChange]);

  useEffect(() => {
    if (inputRef.current) {
      const actuallyDisabled = inputRef.current.disabled;

      if (actuallyDisabled && !isLoading && !disabled && !isSubmitting) {
        inputRef.current.disabled = false;
      }
    }
  });

  // Auto-resize textarea with debounce
  const autoResizeTextarea = useCallback(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      const newHeight = Math.min(Math.max(inputRef.current.scrollHeight, 64), 300);
      inputRef.current.style.height = `${newHeight}px`;
    }
  }, []);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value;
      setLocalValue(value);
      setHasText(!!value.trim());

      // Debounce resize to prevent excessive calculations
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
      setLocalValue('');
      setHasText(false);

      // Reset textarea height
      if (inputRef.current) {
        setTimeout(() => {
          if (inputRef.current) {
            inputRef.current.style.height = '64px';
          }
        }, 50);
      }

      await onSubmit(
        trimmedValue,
        selectedModel?.modelKey,
        selectedModel?.modelName,
        selectedChatMode?.id,
        { apps: selectedApps, kb: selectedKbIds }
      );
    } catch (error) {
      console.error('Failed to send message:', error);
      // Restore message on error
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
    selectedApps,
    selectedKbIds,
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

  const handleModeMenuOpen = (event: React.MouseEvent<HTMLElement>) => {
    setModeMenuAnchor(event.currentTarget);
  };

  const handleModeMenuClose = () => {
    setModeMenuAnchor(null);
  };

  const handleModelSelect = (model: Model) => {
    onModelChange(model);
    handleModelMenuClose();
  };

  const handleModeSelect = (mode: ChatMode) => {
    onChatModeChange(mode);
    handleModeMenuClose();
  };

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();

      // Add scrollbar styles
      const styleId = 'chat-textarea-style';
      if (!document.getElementById(styleId)) {
        const style = document.createElement('style');
        style.id = styleId;
        style.textContent = isDark
          ? `
        textarea::-webkit-scrollbar {
          width: 6px;
          background-color: transparent;
        }
        textarea::-webkit-scrollbar-thumb {
          background-color: rgba(255, 255, 255, 0.2);
          border-radius: 10px;
        }
        textarea::-webkit-scrollbar-thumb:hover {
          background-color: rgba(255, 255, 255, 0.3);
        }
      `
          : `
        textarea::-webkit-scrollbar {
          width: 6px;
          background-color: transparent;
        }
        textarea::-webkit-scrollbar-thumb {
          background-color: rgba(0, 0, 0, 0.2);
          border-radius: 10px;
        }
        textarea::-webkit-scrollbar-thumb:hover {
          background-color: rgba(0, 0, 0, 0.3);
        }
      `;
        document.head.appendChild(style);
      }
    }

    return () => {
      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current);
      }
    };
  }, [isDark]);

  // Only disable input if THIS conversation is actively loading/submitting
  const isInputDisabled = disabled || isSubmitting || isLoading;
  const canSubmit = hasText && !isInputDisabled;

  // Format model name for display
  const getModelDisplayName = (model: Model | null) => {
    if (!model) return 'Model';
    return model.modelName || 'Model';
  };

  return (
    <>
      {/* Add keyframes for spinner animation */}
      <style>
        {`
          @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }
        `}
      </style>

      <Box
        sx={{
          p: 1,
          width: { xs: '90%', sm: '80%', md: '70%' },
          mx: 'auto',
          position: 'relative',
        }}
      >
        <Paper
          elevation={0}
          sx={{
            display: 'flex',
            alignItems: 'center',
            p: '12px 16px',
            borderRadius: '12px',
            backgroundColor: isDark ? alpha('#131417', 0.6) : alpha('#f8f9fa', 0.8),
            border: '1px solid',
            borderColor: isDark ? alpha('#fff', 0.08) : alpha('#000', 0.06),
            boxShadow: isDark ? '0 4px 16px rgba(0, 0, 0, 0.2)' : '0 2px 8px rgba(0, 0, 0, 0.06)',
            transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
            minHeight: '48px',
            gap: 1.5,
            '&:hover': {
              borderColor: isDark ? alpha('#fff', 0.12) : alpha('#000', 0.1),
              boxShadow: isDark
                ? '0 6px 20px rgba(0, 0, 0, 0.25)'
                : '0 4px 12px rgba(0, 0, 0, 0.1)',
              backgroundColor: isDark ? alpha('#131417', 0.8) : alpha('#fff', 0.95),
            },
            '&:focus-within': {
              borderColor: isDark ? alpha('#fff', 0.15) : alpha('#000', 0.12),
              boxShadow: isDark
                ? '0 6px 24px rgba(0, 0, 0, 0.3)'
                : '0 4px 16px rgba(0, 0, 0, 0.12)',
            },
          }}
        >
          <Box sx={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
            {/* Text Input */}
            <Box sx={{ flex: 1, minWidth: 0, pl: 1, pr: 1, pt: 0.5 }}>
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
                  fontSize: '0.95rem',
                  lineHeight: 1.4,
                  minHeight: '24px',
                  maxHeight: '60px',
                  resize: 'none',
                  fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
                  overflowY: 'auto',
                  overflowX: 'hidden',
                  transition: 'all 0.2s ease',
                  cursor: 'text',
                  opacity: isInputDisabled ? 0.6 : 1,
                }}
              />
            </Box>

            <Box
              sx={{
                display: 'flex',
                gap: 2,
                mx: 2,
                flexDirection: 'row',
                width: '100%',
                justifyContent: 'space-between',
              }}
            >
              {/* Chat Mode Selector */}
              <Box sx={{ display: 'flex', gap: 1 }}>
                {CHAT_MODES.map((mode) => (
                  <Chip
                    key={mode.id}
                    label={mode.name}
                    onClick={() => handleModeSelect(mode)}
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
                      color:
                        selectedChatMode?.id === mode.id ? '#fff' : theme.palette.text.secondary,
                      bgcolor:
                        selectedChatMode?.id === mode.id
                          ? theme.palette.primary.main
                          : 'transparent',
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
              <Box
                sx={{ display: 'flex', gap: 1, flexDirection: 'row', mr: 2, alignItems: 'center' }}
              >
                {/* Unified Resources selector with badge */}
                <Tooltip title="Select apps and knowledge bases">
                  <Badge
                    badgeContent={selectedApps.length + selectedKbIds.length}
                    color="primary"
                    max={99}
                  >
                    <IconButton
                      onClick={openResourcesMenu}
                      size="small"
                      sx={{
                        width: 28,
                        height: 28,
                        borderRadius: '50%',
                        bgcolor: 'transparent',
                        border: `1px solid ${isDark ? alpha('#fff', 0.1) : alpha('#000', 0.12)}`,
                        color: isDark ? alpha('#fff', 0.8) : alpha('#000', 0.7),
                        '&:hover': {
                          bgcolor: isDark ? alpha('#fff', 0.06) : alpha('#000', 0.06),
                          borderColor: isDark ? alpha('#fff', 0.2) : alpha('#000', 0.2),
                        },
                      }}
                    >
                      <Icon icon={filterIcon} width={14} height={14} />
                    </IconButton>
                  </Badge>
                </Tooltip>

                {/* Model Selector */}
                <Tooltip
                  title={`AI Model: ${selectedModel ? `${formattedProvider(selectedModel.provider)} - ${selectedModel.modelName}` : 'Select AI model'}`}
                >
                  <Box
                    onClick={handleModelMenuOpen}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 0.5,
                      p: '6px 10px',
                      borderRadius: '8px',
                      cursor: 'pointer',
                      color: isDark ? alpha('#fff', 0.7) : alpha('#000', 0.6),
                      fontSize: '0.8rem',
                      fontWeight: 500,
                      transition: 'all 0.15s ease',
                      backgroundColor: isDark ? alpha('#fff', 0.02) : alpha('#000', 0.02),
                      border: `1px solid ${isDark ? alpha('#fff', 0.05) : alpha('#000', 0.04)}`,
                      flexShrink: 0,
                      '&:hover': {
                        backgroundColor: isDark ? alpha('#fff', 0.06) : alpha('#000', 0.05),
                        borderColor: isDark ? alpha('#fff', 0.08) : alpha('#000', 0.06),
                      },
                    }}
                  >
                    <Typography
                      variant="body2"
                      sx={{ fontSize: '0.8rem', fontWeight: 500, minWidth: '60px' }}
                    >
                      {selectedModel?.modelName?.slice(0, 16) || ''}
                    </Typography>
                    <Icon icon={chevronDownIcon} width={12} height={12} />
                  </Box>
                </Tooltip>

                {/* Send Button */}
                <IconButton
                  size="small"
                  onClick={handleSubmit}
                  disabled={!canSubmit}
                  sx={{
                    backgroundColor: canSubmit
                      ? alpha(theme.palette.primary.main, 0.9)
                      : 'transparent',
                    width: 36,
                    height: 36,
                    borderRadius: '8px',
                    flexShrink: 0,
                    transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
                    color: canSubmit ? '#fff' : isDark ? alpha('#fff', 0.4) : alpha('#000', 0.3),
                    opacity: canSubmit ? 1 : 0.5,
                    border: canSubmit
                      ? 'none'
                      : `1px solid ${isDark ? alpha('#fff', 0.08) : alpha('#000', 0.06)}`,
                    '&:hover': !isInputDisabled
                      ? {
                          backgroundColor: canSubmit
                            ? theme.palette.primary.main
                            : isDark
                              ? alpha('#fff', 0.04)
                              : alpha('#000', 0.03),
                          transform: canSubmit ? 'scale(1.05)' : 'none',
                        }
                      : {},
                    '&:active': {
                      transform: canSubmit ? 'scale(0.98)' : 'none',
                    },
                    '&.Mui-disabled': {
                      opacity: 0.5,
                      backgroundColor: 'transparent',
                    },
                  }}
                >
                  {isSubmitting ? (
                    <Box
                      component="span"
                      sx={{
                        width: 16,
                        height: 16,
                        border: '2px solid transparent',
                        borderTop: `2px solid ${canSubmit ? '#fff' : isDark ? '#fff' : '#000'}`,
                        borderRadius: '50%',
                        animation: 'spin 1s linear infinite',
                        display: 'inline-block',
                      }}
                    />
                  ) : (
                    <Icon icon={arrowUpIcon} width={18} height={18} />
                  )}
                </IconButton>
              </Box>
            </Box>
          </Box>
        </Paper>

        {/* Model Selection Menu */}
        <Menu
          anchorEl={modelMenuAnchor}
          open={Boolean(modelMenuAnchor)}
          onClose={handleModelMenuClose}
          anchorOrigin={{
            vertical: 'top',
            horizontal: 'right',
          }}
          transformOrigin={{
            vertical: 'bottom',
            horizontal: 'right',
          }}
          PaperProps={{
            sx: {
              maxHeight: 320,
              minWidth: 280,
              mt: -0.5,
              borderRadius: '12px',
              border: `1px solid ${isDark ? alpha('#fff', 0.1) : alpha('#000', 0.1)}`,
              backgroundColor: isDark ? '#1e1e1e' : '#ffffff',
              boxShadow: isDark
                ? '0 8px 32px rgba(0, 0, 0, 0.4)'
                : '0 8px 32px rgba(0, 0, 0, 0.12)',
            },
          }}
        >
          <Box sx={{ p: 1.5 }}>
            <Typography
              variant="subtitle2"
              sx={{
                px: 1,
                pb: 1.5,
                color: 'text.secondary',
                fontWeight: 600,
                fontSize: '0.75rem',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              AI Models
            </Typography>
            <Divider sx={{ mb: 1.5 }} />
            {models.map((model) => (
              <MenuItem
                key={`${model.provider}-${model.modelName}`}
                onClick={() => handleModelSelect(model)}
                selected={
                  selectedModel?.provider === model.provider &&
                  selectedModel?.modelName === model.modelName
                }
                sx={{
                  borderRadius: '8px',
                  mb: 0.5,
                  p: '10px 12px',
                  minHeight: 'auto',
                  '&:last-child': { mb: 0 },
                  '&.Mui-selected': {
                    backgroundColor: isDark
                      ? alpha('#fff', 0.08)
                      : alpha(theme.palette.primary.main, 0.08),
                    '&:hover': {
                      backgroundColor: isDark
                        ? alpha('#fff', 0.12)
                        : alpha(theme.palette.primary.main, 0.12),
                    },
                  },
                  '&:hover': {
                    backgroundColor: isDark ? alpha('#fff', 0.04) : alpha('#000', 0.03),
                  },
                }}
              >
                <Box sx={{ width: '100%' }}>
                  <Typography
                    variant="body2"
                    fontWeight="medium"
                    sx={{ fontSize: '0.9rem', mb: 0.5 }}
                  >
                    {model.modelName}
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{
                      fontSize: '0.75rem',
                      lineHeight: 1.3,
                      opacity: 0.8,
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                    }}
                  >
                    {formattedProvider(model.provider)} {model.isMultimodal ? '• Multimodal' : ''}{' '}
                    {model.isDefault ? '• Default' : ''}
                  </Typography>
                </Box>
              </MenuItem>
            ))}
          </Box>
        </Menu>

        <ChatBotFilters
          resourcesAnchor={resourcesAnchor}
          closeResourcesMenu={closeResourcesMenu}
          searchTerm={searchTerm}
          setSearchTerm={setSearchTerm}
          selectedApps={selectedApps}
          selectedKbIds={selectedKbIds}
          expandedSections={expandedSections}
          toggleSection={toggleSection}
          toggleApp={toggleApp}
          toggleKb={toggleKb}
          filteredApps={filteredApps}
          filteredKBs={filteredKBs}
          showMoreApps={showMoreApps}
          showMoreKBs={showMoreKBs}
          setShowMoreApps={setShowMoreApps}
          setShowMoreKBs={setShowMoreKBs}
          setSelectedApps={setSelectedApps}
          setSelectedKbIds={setSelectedKbIds}
        />

        {/* Selected Filters Preview */}
        {(selectedApps.length > 0 || selectedKbIds.length > 0) && (
          <Box sx={{ mt: 1, display: 'flex', gap: 1, flexWrap: 'wrap', px: 0.5 }}>
            {selectedApps.slice(0, 3).map((id) => {
              const app = appItems.find((a) => a.id === id);
              const label = app ? app.name : id;
              return (
                <Chip
                  key={`app-${id}`}
                  size="small"
                  label={label}
                  onDelete={() => toggleApp(id)}
                  sx={{ height: 22, borderRadius: '12px' }}
                />
              );
            })}
            {selectedApps.length > 3 && (
              <Chip
                component="button"
                size="small"
                label={`+${selectedApps.length - 3} more`}
                sx={{ height: 22, borderRadius: '12px' }}
                onClick={openResourcesMenu}
              />
            )}
            {selectedKbIds.slice(0, 3).map((id) => {
              const label = kbNameMap.get(id) || id;
              return (
                <Chip
                  key={`kb-${id}`}
                  size="small"
                  label={label}
                  onDelete={() => toggleKb(id)}
                  variant="outlined"
                  sx={{ height: 22, borderRadius: '12px' }}
                />
              );
            })}
            {selectedKbIds.length > 3 && (
              <Chip
                component="button"
                size="small"
                label={`+${selectedKbIds.length - 3} more`}
                variant="outlined"
                sx={{ height: 22, borderRadius: '12px' }}
                onClick={openResourcesMenu}
              />
            )}
          </Box>
        )}
      </Box>
    </>
  );
};

export default ChatInput;

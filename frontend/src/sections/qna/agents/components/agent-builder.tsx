// src/sections/agents/components/enhanced-agent-builder.tsx
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Stepper,
  Step,
  StepLabel,
  Button,
  Typography,
  TextField,
  Grid,
  Chip,
  IconButton,
  Alert,
  CircularProgress,
  useTheme,
  alpha,
  Paper,
  Fade,
  Autocomplete,
  Card,
  CardContent,
  Stack,
  InputAdornment,
  Avatar,
} from '@mui/material';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/mdi/close';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import arrowRightIcon from '@iconify-icons/mdi/arrow-right';
import checkIcon from '@iconify-icons/mdi/check';
import deleteIcon from '@iconify-icons/mdi/delete';
import sparklesIcon from '@iconify-icons/mdi/star-four-points';
import toolIcon from '@iconify-icons/mdi/tools';
import databaseIcon from '@iconify-icons/mdi/database';
import brainIcon from '@iconify-icons/mdi/brain';
import searchIcon from '@iconify-icons/mdi/magnify';
import cogIcon from '@iconify-icons/mdi/cog';

import type { Agent, AgentTemplate, AgentFormData } from 'src/types/agent';
import AgentApiService from '../services/agent-api-service';
import { validateAgentForm, getInitialAgentFormData, APPS } from '../utils/agent-utils';
import { createScrollableContainerStyle } from '../../chatbot/utils/styles/scrollbar';

interface AgentBuilderProps {
  open: boolean;
  onClose: () => void;
  onSuccess: (agent: Agent) => void;
  selectedTemplate?: AgentTemplate | null;
  editingAgent?: Agent | null;
}

interface ToolOption {
  id: string;
  label: string;
  displayName: string;
  app_name: string;
  tool_name: string;
  appDisplayName: string;
  toolDisplayName: string;
  description: string;
}

interface KBOption {
  id: string;
  name: string;
  description?: string;
  userRole: string;
}

interface ModelOption {
  modelType: string;
  provider: string;
  modelName: string;
  modelKey: string;
  isMultimodal: boolean;
  isDefault: boolean;
  displayName: string;
}

const steps = ['Basic Information', 'AI Configuration', 'Tools & Integrations', 'Review & Create'];

// Utility function to normalize names (handle underscores and make readable)
const normalizeDisplayName = (name: string): string =>
  name
    .split('_')
    .map((word) => {
      // Handle common abbreviations
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
        ].includes(upperWord)
      ) {
        return upperWord;
      }
      // Capitalize first letter
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(' ');

// Utility function to get provider display name
const getProviderDisplayName = (provider: string): string => {
  const providerMap: Record<string, string> = {
    azureOpenAI: 'Azure OpenAI',
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    google: 'Google',
    cohere: 'Cohere',
    huggingface: 'Hugging Face',
  };
  return providerMap[provider] || normalizeDisplayName(provider);
};

const AgentBuilder: React.FC<AgentBuilderProps> = ({
  open,
  onClose,
  onSuccess,
  selectedTemplate,
  editingAgent,
}) => {
  const theme = useTheme();
  const scrollableContainerStyle = createScrollableContainerStyle(theme);
  const [activeStep, setActiveStep] = useState(0);
  const [formData, setFormData] = useState<AgentFormData>(getInitialAgentFormData());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isCreating, setIsCreating] = useState(false);
  const [loading, setLoading] = useState(false);

  // Available options
  const [availableTools, setAvailableTools] = useState<ToolOption[]>([]);
  const [availableKBs, setAvailableKBs] = useState<KBOption[]>([]);
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [kbSearchTerm, setKbSearchTerm] = useState('');
  const [toolSearchTerm, setToolSearchTerm] = useState('');
  const [modelSearchTerm, setModelSearchTerm] = useState('');

  // Initialize form data
  useEffect(() => {
    if (editingAgent) {
      setFormData({
        name: editingAgent.name || '',
        description: editingAgent.description || '',
        startMessage: editingAgent.startMessage || '',
        systemPrompt: editingAgent.systemPrompt || '',
        tools: Array.isArray(editingAgent.tools) ? [...editingAgent.tools] : [],
        models: Array.isArray(editingAgent.models) ? [...editingAgent.models] : [],
        apps: Array.isArray(editingAgent.apps) ? [...editingAgent.apps] : [],
        kb: Array.isArray(editingAgent.kb) ? [...editingAgent.kb] : [],
        vectorDBs: Array.isArray(editingAgent.vectorDBs) ? [...editingAgent.vectorDBs] : [],
        tags: Array.isArray(editingAgent.tags) ? [...editingAgent.tags] : [],
      });
    } else if (selectedTemplate) {
      setFormData({
        name: `${selectedTemplate.name || 'Template'} Agent`,
        description: selectedTemplate.description || '',
        startMessage: selectedTemplate.startMessage || '',
        systemPrompt: selectedTemplate.systemPrompt || '',
        tools: [],
        models: [],
        apps: [],
        kb: [],
        vectorDBs: [],
        tags: Array.isArray(selectedTemplate.tags) ? [...selectedTemplate.tags] : [],
        templateId: selectedTemplate._key,
      });
    } else {
      setFormData(getInitialAgentFormData());
    }
  }, [selectedTemplate, editingAgent]);

  // Reset form when dialog closes
  useEffect(() => {
    if (!open) {
      setActiveStep(0);
      setFormData(getInitialAgentFormData());
      setErrors({});
      setKbSearchTerm('');
      setToolSearchTerm('');
    }
  }, [open]);

  const loadAvailableResources = useCallback(async () => {
    try {
      setLoading(true);
      const [kbResponse, modelsResponse, toolsData] = await Promise.all([
        AgentApiService.getKnowledgeBases({ limit: 100 }),
        AgentApiService.getAvailableModels(),
        AgentApiService.getAvailableTools(),
      ]);

      // Process KBs
      const kbs = kbResponse.knowledgeBases || [];
      setAvailableKBs(
        kbs.map((kb: any) => ({
          id: kb.id,
          name: kb.name,
          description: `Role: ${kb.userRole}`,
          userRole: kb.userRole,
        }))
      );

      // Process models with provider information
      const models = modelsResponse || [];
      const processedModels = models.map((model: any) => ({
        ...model,
        displayName: `${getProviderDisplayName(model.provider)} - ${model.modelName}`,
      }));
      setAvailableModels(processedModels);

      // Process tools with normalized display names
      const tools = toolsData.map((tool: any) => {
        const appDisplayName = normalizeDisplayName(tool.app_name);
        const toolDisplayName = normalizeDisplayName(tool.tool_name);

        return {
          id: tool.full_name, // Use full_name as the ID (e.g., "calculator.calculate_single_operand")
          label: `${tool.app_name}.${tool.tool_name}`, // Technical name for backend
          displayName: `${appDisplayName} - ${toolDisplayName}`, // User-friendly display
          app_name: tool.app_name,
          tool_name: tool.tool_name,
          appDisplayName,
          toolDisplayName,
          description: tool.description || '',
        };
      });
      setAvailableTools(tools);
    } catch (error) {
      console.error('Error loading available resources:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load available resources
  useEffect(() => {
    if (open) {
      loadAvailableResources();
    }
  }, [open, loadAvailableResources]);

  const handleFormChange = useCallback(
    (field: keyof AgentFormData, value: any) => {
      setFormData((prev) => ({ ...prev, [field]: value }));
      if (errors[field]) {
        setErrors((prev) => ({ ...prev, [field]: '' }));
      }
    },
    [errors]
  );

  const handleBack = useCallback(() => {
    setActiveStep((prev) => prev - 1);
  }, []);

  const handleCreate = useCallback(async () => {
    const validationErrors = validateAgentForm(formData);
    setErrors(validationErrors);

    if (Object.keys(validationErrors).length > 0) {
      setActiveStep(0);
      return;
    }

    try {
      setIsCreating(true);
      const agent = editingAgent
        ? await AgentApiService.updateAgent(editingAgent._key, formData)
        : await AgentApiService.createAgent(formData);

      onSuccess(agent);
      onClose();
    } catch (error) {
      console.error('Error creating/updating agent:', error);
      setErrors({ general: 'Failed to save agent. Please try again.' });
    } finally {
      setIsCreating(false);
    }
  }, [formData, editingAgent, onSuccess, onClose]);

  const handleNext = useCallback(() => {
    if (activeStep === steps.length - 1) {
      handleCreate();
    } else {
      setActiveStep((prev) => prev + 1);
    }
  }, [activeStep, handleCreate]);

  // Filtered options
  const filteredKBs = useMemo(() => {
    if (!kbSearchTerm) return availableKBs;
    return availableKBs.filter((kb) => kb.name.toLowerCase().includes(kbSearchTerm.toLowerCase()));
  }, [availableKBs, kbSearchTerm]);

  const filteredTools = useMemo(() => {
    if (!toolSearchTerm) return availableTools;
    return availableTools.filter(
      (tool) =>
        tool.displayName.toLowerCase().includes(toolSearchTerm.toLowerCase()) ||
        tool.description.toLowerCase().includes(toolSearchTerm.toLowerCase()) ||
        tool.appDisplayName.toLowerCase().includes(toolSearchTerm.toLowerCase())
    );
  }, [availableTools, toolSearchTerm]);

  const filteredModels = useMemo(() => {
    if (!modelSearchTerm) return availableModels;
    return availableModels.filter(
      (model) =>
        model.displayName.toLowerCase().includes(modelSearchTerm.toLowerCase()) ||
        model.provider.toLowerCase().includes(modelSearchTerm.toLowerCase()) ||
        model.modelName.toLowerCase().includes(modelSearchTerm.toLowerCase())
    );
  }, [availableModels, modelSearchTerm]);

  const addArrayItem = useCallback(
    (field: keyof AgentFormData, value: string) => {
      if (!value.trim()) return;

      const currentArray = Array.isArray(formData[field]) ? (formData[field] as string[]) : [];
      if (!currentArray.includes(value.trim())) {
        handleFormChange(field, [...currentArray, value.trim()]);
      }
    },
    [formData, handleFormChange]
  );

  const insertArrayItem = useCallback(
    (field: keyof AgentFormData, value: any) => {
      const currentArray = Array.isArray(formData[field]) ? (formData[field] as any[]) : [];
      handleFormChange(field, [value, ...currentArray]);
    },
    [formData, handleFormChange]
  );

  const removeArrayItem = useCallback(
    (field: keyof AgentFormData, index: number) => {
      const currentArray = Array.isArray(formData[field]) ? (formData[field] as string[]) : [];
      handleFormChange(
        field,
        currentArray.filter((_, i) => i !== index)
      );
    },
    [formData, handleFormChange]
  );

  // Get display name for selected tools
  const getToolDisplayName = useCallback(
    (toolId: string) => {
      const tool = availableTools.find((t) => t.id === toolId);
      return tool
        ? `${tool.appDisplayName} • ${tool.toolDisplayName}`
        : toolId.split('.')[1] || toolId;
    },
    [availableTools]
  );

  // Get display name for selected models
  const getModelDisplayName = useCallback(
    (modelKey: string) => {
      const model = availableModels.find((m) => m.modelKey === modelKey);
      return model ? model.displayName : modelKey;
    },
    [availableModels]
  );

  const renderStepContent = () => {
    switch (activeStep) {
      case 0: // Basic Information
        return (
          <Stack spacing={2.5}>
            <TextField
              fullWidth
              label="Agent Name"
              placeholder="Enter a unique name for your agent"
              value={formData.name}
              onChange={(e) => handleFormChange('name', e.target.value)}
              error={!!errors.name}
              helperText={errors.name || 'Choose a descriptive name for your AI agent'}
              size="small"
            />

            <TextField
              fullWidth
              label="Description"
              placeholder="Describe what your agent does and its purpose"
              value={formData.description}
              onChange={(e) => handleFormChange('description', e.target.value)}
              error={!!errors.description}
              helperText={
                errors.description || "Provide a clear description of your agent's capabilities"
              }
              multiline
              rows={3}
              size="small"
            />

            <TextField
              fullWidth
              label="Welcome Message"
              placeholder="Hi! I'm your AI assistant. How can I help you today?"
              value={formData.startMessage}
              onChange={(e) => handleFormChange('startMessage', e.target.value)}
              error={!!errors.startMessage}
              helperText={
                errors.startMessage || 'This message appears when users start a new conversation'
              }
              multiline
              rows={2}
              size="small"
            />

            {/* Tags */}
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                Tags
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 1.5 }}>
                {(formData.tags || []).map((tag, index) => (
                  <Chip
                    key={index}
                    label={tag}
                    onDelete={() => removeArrayItem('tags', index)}
                    size="small"
                    variant="outlined"
                    deleteIcon={<Icon icon={deleteIcon} width={12} height={12} />}
                  />
                ))}
              </Box>
              <Autocomplete
                freeSolo
                options={[
                  'customer-support',
                  'content-creation',
                  'data-analysis',
                  'development',
                  'marketing',
                  'research',
                  'automation',
                ]}
                value=""
                onChange={(_, value) => {
                  if (value) {
                    addArrayItem('tags', value);
                  }
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    placeholder="Add tags to categorize your agent"
                    size="small"
                    onKeyPress={(e) => {
                      if (e.key === 'Enter' && (e.target as HTMLInputElement).value.trim()) {
                        e.preventDefault();
                        addArrayItem('tags', (e.target as HTMLInputElement).value.trim());
                        (e.target as HTMLInputElement).value = '';
                      }
                    }}
                  />
                )}
              />
            </Box>
          </Stack>
        );

      case 1: // AI Configuration
        return (
          <Stack spacing={2.5}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <Icon icon={brainIcon} width={20} height={20} />
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                AI Configuration
              </Typography>
            </Box>

            <TextField
              fullWidth
              label="System Prompt"
              placeholder="You are a helpful AI assistant. Be accurate, concise, and professional..."
              value={formData.systemPrompt}
              onChange={(e) => handleFormChange('systemPrompt', e.target.value)}
              error={!!errors.systemPrompt}
              helperText={
                errors.systemPrompt || 'Define how your agent should behave and respond to users'
              }
              multiline
              rows={5}
              size="small"
            />

            {/* AI Models */}
            <Box>
              <Card variant="outlined" sx={{ borderRadius: 1.5 }}>
                <CardContent sx={{ p: 2 }}>
                  <Typography
                    variant="subtitle2"
                    sx={{ mb: 1.5, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 1 }}
                  >
                    <Icon icon={brainIcon} width={16} height={16} />
                    AI Models
                  </Typography>

                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 1.5 }}>
                    {(formData.models || []).map((model, index) => (
                      <Chip
                        key={index}
                        label={`${model.provider} - ${model.modelName}`}
                        onDelete={() => removeArrayItem('models', index)}
                        size="small"
                        color="primary"
                        variant="filled"
                        deleteIcon={<Icon icon={deleteIcon} width={12} height={12} />}
                      />
                    ))}
                  </Box>

                  <TextField
                    fullWidth
                    placeholder="Search AI models..."
                    value={modelSearchTerm}
                    onChange={(e) => setModelSearchTerm(e.target.value)}
                    size="small"
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <Icon icon={searchIcon} width={16} height={16} />
                        </InputAdornment>
                      ),
                    }}
                    sx={{ mb: 1.5 }}
                  />

                  <Box sx={{ maxHeight: 180, overflow: 'auto' }}>
                    <Grid container spacing={1}>
                      {filteredModels.map((model) => (
                        <Grid item xs={12} sm={6} key={`${model.provider}-${model.modelName}`}>
                          <Paper
                            sx={{
                              p: 1.5,
                              cursor: 'pointer',
                              border: 1,
                              borderRadius: 1,
                              borderColor: (formData.models || []).includes({
                                provider: model.provider,
                                modelName: model.modelName,
                              })
                                ? 'primary.main'
                                : 'divider',
                              bgcolor: (formData.models || []).includes({
                                provider: model.provider,
                                modelName: model.modelName,
                              })
                                ? alpha(theme.palette.primary.main, 0.1)
                                : 'background.paper',
                              '&:hover': {
                                borderColor: 'primary.main',
                                bgcolor: alpha(theme.palette.primary.main, 0.05),
                              },
                            }}
                            onClick={() => {
                              if (
                                (formData.models || []).includes({
                                  provider: model.provider,
                                  modelName: model.modelName,
                                })
                              ) {
                                const index = formData.models.indexOf({
                                  provider: model.provider,
                                  modelName: model.modelName,
                                });
                                removeArrayItem('models', index);
                              } else {
                                insertArrayItem('models', {
                                  provider: model.provider,
                                  modelName: model.modelName,
                                });
                              }
                            }}
                          >
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                              <Avatar
                                sx={{
                                  width: 16,
                                  height: 16,
                                  fontSize: '0.6rem',
                                  bgcolor: 'primary.main',
                                }}
                              >
                                {model.provider.charAt(0).toUpperCase()}
                              </Avatar>
                              <Typography
                                variant="body2"
                                sx={{ fontWeight: 600, fontSize: '0.75rem' }}
                              >
                                {model.displayName}
                              </Typography>
                            </Box>

                            <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                              {model.isDefault && (
                                <Chip
                                  label="Default"
                                  size="small"
                                  color="primary"
                                  variant="outlined"
                                  sx={{ height: 16, fontSize: '0.6rem' }}
                                />
                              )}
                              {model.isMultimodal && (
                                <Chip
                                  label="Multimodal"
                                  size="small"
                                  variant="outlined"
                                  sx={{ height: 16, fontSize: '0.6rem' }}
                                />
                              )}
                            </Box>
                          </Paper>
                        </Grid>
                      ))}
                    </Grid>
                  </Box>

                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ mt: 1, display: 'block' }}
                  >
                    Choose AI models your agent will use for responses
                  </Typography>
                </CardContent>
              </Card>
            </Box>
          </Stack>
        );

      case 2: // Tools & Integrations
        return (
          <Stack spacing={3}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <Icon icon={toolIcon} width={20} height={20} />
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                Tools & Integrations
              </Typography>
            </Box>

            {/* Tools */}
            <Card variant="outlined" sx={{ borderRadius: 1.5 }}>
              <CardContent sx={{ p: 2 }}>
                <Typography
                  variant="subtitle2"
                  sx={{ mb: 1.5, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 1 }}
                >
                  <Icon icon={toolIcon} width={16} height={16} />
                  Available Tools
                </Typography>

                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 1.5 }}>
                  {(formData.tools || []).map((tool, index) => (
                    <Chip
                      key={index}
                      label={getToolDisplayName(tool)}
                      onDelete={() => removeArrayItem('tools', index)}
                      size="small"
                      color="info"
                      variant="filled"
                      deleteIcon={<Icon icon={deleteIcon} width={12} height={12} />}
                    />
                  ))}
                </Box>

                <TextField
                  fullWidth
                  placeholder="Search tools..."
                  value={toolSearchTerm}
                  onChange={(e) => setToolSearchTerm(e.target.value)}
                  size="small"
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Icon icon={searchIcon} width={16} height={16} />
                      </InputAdornment>
                    ),
                  }}
                  sx={{ mb: 1.5 }}
                />

                <Box sx={{ maxHeight: 180, overflow: 'auto', ...scrollableContainerStyle }}>
                  <Grid container spacing={1}>
                    {filteredTools.map((tool) => (
                      <Grid item xs={12} sm={6} key={tool.id}>
                        <Paper
                          sx={{
                            p: 1.5,
                            cursor: 'pointer',
                            border: 1,
                            borderRadius: 1,
                            borderColor: (formData.tools || []).includes(tool.id)
                              ? 'primary.main'
                              : 'divider',
                            bgcolor: (formData.tools || []).includes(tool.id)
                              ? alpha(theme.palette.primary.main, 0.1)
                              : 'background.paper',
                            '&:hover': {
                              borderColor: 'primary.main',
                              bgcolor: alpha(theme.palette.primary.main, 0.05),
                            },
                          }}
                          onClick={() => {
                            if ((formData.tools || []).includes(tool.id)) {
                              const index = formData.tools.indexOf(tool.id);
                              removeArrayItem('tools', index);
                            } else {
                              addArrayItem('tools', tool.id);
                            }
                          }}
                        >
                          <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.75rem' }}>
                            {tool.displayName}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontSize: '0.65rem', lineHeight: 1.2 }}
                          >
                            {tool.description.length > 60
                              ? `${tool.description.substring(0, 60)}...`
                              : tool.description}
                          </Typography>
                        </Paper>
                      </Grid>
                    ))}
                  </Grid>
                </Box>

                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mt: 1, display: 'block' }}
                >
                  Select tools your agent can use to assist users
                </Typography>
              </CardContent>
            </Card>

            {/* Knowledge Bases */}
            <Card variant="outlined" sx={{ borderRadius: 1.5 }}>
              <CardContent sx={{ p: 2 }}>
                <Typography
                  variant="subtitle2"
                  sx={{ mb: 1.5, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 1 }}
                >
                  <Icon icon={databaseIcon} width={16} height={16} />
                  Knowledge Bases
                </Typography>

                <Box
                  sx={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 0.5,
                    mb: 1.5,
                    ...scrollableContainerStyle,
                  }}
                >
                  {(formData.kb || []).map((kbId, index) => {
                    const kb = availableKBs.find((k) => k.id === kbId);
                    return (
                      <Chip
                        key={index}
                        label={kb?.name || kbId}
                        onDelete={() => removeArrayItem('kb', index)}
                        size="small"
                        color="info"
                        variant="filled"
                        deleteIcon={<Icon icon={deleteIcon} width={12} height={12} />}
                      />
                    );
                  })}
                </Box>

                <TextField
                  fullWidth
                  placeholder="Search knowledge bases..."
                  value={kbSearchTerm}
                  onChange={(e) => setKbSearchTerm(e.target.value)}
                  size="small"
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Icon icon={searchIcon} width={16} height={16} />
                      </InputAdornment>
                    ),
                  }}
                  sx={{ mb: 1.5 }}
                />

                <Box sx={{ maxHeight: 180, overflow: 'auto', ...scrollableContainerStyle }}>
                  <Grid container spacing={1}>
                    {filteredKBs.map((kb) => (
                      <Grid item xs={12} sm={6} key={kb.id}>
                        <Paper
                          sx={{
                            p: 1.5,
                            cursor: 'pointer',
                            border: 1,
                            borderRadius: 1,
                            borderColor: (formData.kb || []).includes(kb.id)
                              ? 'info.main'
                              : 'divider',
                            bgcolor: (formData.kb || []).includes(kb.id)
                              ? alpha(theme.palette.info.main, 0.1)
                              : 'background.paper',
                            '&:hover': {
                              borderColor: 'info.main',
                              bgcolor: alpha(theme.palette.info.main, 0.05),
                            },
                          }}
                          onClick={() => {
                            if ((formData.kb || []).includes(kb.id)) {
                              const index = formData.kb.indexOf(kb.id);
                              removeArrayItem('kb', index);
                            } else {
                              addArrayItem('kb', kb.id);
                            }
                          }}
                        >
                          <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.75rem' }}>
                            {kb.name}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontSize: '0.65rem' }}
                          >
                            {kb.description}
                          </Typography>
                        </Paper>
                      </Grid>
                    ))}
                  </Grid>
                </Box>

                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mt: 1, display: 'block' }}
                >
                  Select knowledge bases your agent can search and reference
                </Typography>
              </CardContent>
            </Card>

            {/* Applications */}
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                Applications
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 1.5 }}>
                {(formData.apps || []).map((app, index) => (
                  <Chip
                    key={index}
                    label={normalizeDisplayName(app)}
                    onDelete={() => removeArrayItem('apps', index)}
                    size="small"
                    color="warning"
                    variant="outlined"
                    deleteIcon={<Icon icon={deleteIcon} width={12} height={12} />}
                  />
                ))}
              </Box>
              <Autocomplete
                freeSolo
                options={Array.from(APPS).map((app) => ({
                  value: app,
                  label: normalizeDisplayName(app),
                }))}
                getOptionLabel={(option) => (typeof option === 'string' ? option : option.label)}
                value=""
                onChange={(_, value) => {
                  if (value) {
                    const appValue = typeof value === 'string' ? value : value.value;
                    addArrayItem('apps', appValue);
                  }
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    placeholder="Add applications to integrate with"
                    size="small"
                  />
                )}
              />
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mt: 0.5, display: 'block' }}
              >
                Connect applications your agent can integrate with
              </Typography>
            </Box>
          </Stack>
        );

      case 3: // Review
        return (
          <Stack spacing={3}>
            <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
              Review Your Agent
            </Typography>

            {/* Agent Basic Info */}
            <Paper
              sx={{
                p: 3,
                bgcolor: alpha(theme.palette.primary.main, 0.04),
                borderRadius: 1.5,
                border: '1px solid',
                borderColor: 'divider',
                ...scrollableContainerStyle,
              }}
            >
              <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
                {formData.name}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {formData.description}
              </Typography>

              {Array.isArray(formData.tags) && formData.tags.length > 0 && (
                <Box>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ mb: 1, display: 'block' }}
                  >
                    Tags
                  </Typography>
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                    {formData.tags.map((tag, index) => (
                      <Chip key={index} label={tag} size="small" variant="outlined" />
                    ))}
                  </Box>
                </Box>
              )}
            </Paper>

            {/* Configuration */}
            <Grid container spacing={2}>
              <Grid item xs={12} md={6}>
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                    Welcome Message
                  </Typography>
                  <Paper
                    sx={{
                      p: 2,
                      bgcolor: 'background.default',
                      borderRadius: 1,
                      border: '1px solid',
                      borderColor: 'divider',
                    }}
                  >
                    <Typography
                      variant="body2"
                      sx={{ fontSize: '0.875rem', color: 'text.secondary' }}
                    >
                      {formData.startMessage || 'No welcome message set'}
                    </Typography>
                  </Paper>
                </Box>
              </Grid>

              <Grid item xs={12} md={6}>
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                    System Prompt
                  </Typography>
                  <Paper
                    sx={{
                      p: 2,
                      bgcolor: 'background.default',
                      borderRadius: 1,
                      border: '1px solid',
                      borderColor: 'divider',
                    }}
                  >
                    <Typography
                      variant="body2"
                      sx={{
                        maxHeight: 120,
                        overflow: 'auto',
                        whiteSpace: 'pre-wrap',
                        fontSize: '0.875rem',
                        color: 'text.secondary',
                      }}
                    >
                      {formData.systemPrompt || 'No system prompt set'}
                    </Typography>
                  </Paper>
                </Box>
              </Grid>
            </Grid>

            {/* Capabilities */}
            {(formData.models.length > 0 ||
              formData.tools.length > 0 ||
              formData.kb.length > 0 ||
              formData.apps.length > 0) && (
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
                  Capabilities Summary
                </Typography>
                <Grid container spacing={2}>
                  {/* AI Models */}
                  {Array.isArray(formData.models) && formData.models.length > 0 && (
                    <Grid item xs={12} sm={6} md={3}>
                      <Paper
                        sx={{
                          p: 2,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: 'divider',
                          height: '100%',
                        }}
                      >
                        <Typography
                          variant="subtitle2"
                          sx={{
                            mb: 1.5,
                            fontWeight: 600,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1,
                            fontSize: '0.875rem',
                          }}
                        >
                          <Icon icon={brainIcon} width={16} height={16} />
                          AI Models ({formData.models.length})
                        </Typography>
                        <Stack spacing={0.5}>
                          {formData.models.slice(0, 2).map((model, index) => (
                            <Typography
                              key={index}
                              variant="caption"
                              sx={{
                                display: 'block',
                                p: 0.5,
                                bgcolor: alpha(theme.palette.grey[500], 0.08),
                                borderRadius: 0.5,
                                fontSize: '0.75rem',
                              }}
                            >
                              {`${model.provider} - ${model.modelName}`}
                            </Typography>
                          ))}
                          {formData.models.length > 2 && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontSize: '0.75rem' }}
                            >
                              +{formData.models.length - 2} more
                            </Typography>
                          )}
                        </Stack>
                      </Paper>
                    </Grid>
                  )}

                  {/* Tools */}
                  {Array.isArray(formData.tools) && formData.tools.length > 0 && (
                    <Grid item xs={12} sm={6} md={3}>
                      <Paper
                        sx={{
                          p: 2,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: 'divider',
                          height: '100%',
                        }}
                      >
                        <Typography
                          variant="subtitle2"
                          sx={{
                            mb: 1.5,
                            fontWeight: 600,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1,
                            fontSize: '0.875rem',
                          }}
                        >
                          <Icon icon={toolIcon} width={16} height={16} />
                          Tools ({formData.tools.length})
                        </Typography>
                        <Stack spacing={0.5}>
                          {formData.tools.slice(0, 2).map((toolId, index) => {
                            const tool = availableTools.find((t) => t.id === toolId);
                            const displayName = tool
                              ? `${tool.appDisplayName} - ${tool.toolDisplayName}`
                              : toolId
                                  .split('.')
                                  .map((part) => normalizeDisplayName(part))
                                  .join(' - ');

                            return (
                              <Typography
                                key={index}
                                variant="caption"
                                sx={{
                                  display: 'block',
                                  p: 0.5,
                                  bgcolor: alpha(theme.palette.grey[500], 0.08),
                                  borderRadius: 0.5,
                                  fontSize: '0.75rem',
                                }}
                              >
                                {displayName}
                              </Typography>
                            );
                          })}
                          {formData.tools.length > 2 && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontSize: '0.75rem' }}
                            >
                              +{formData.tools.length - 2} more
                            </Typography>
                          )}
                        </Stack>
                      </Paper>
                    </Grid>
                  )}

                  {/* Knowledge Bases */}
                  {Array.isArray(formData.kb) && formData.kb.length > 0 && (
                    <Grid item xs={12} sm={6} md={3}>
                      <Paper
                        sx={{
                          p: 2,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: 'divider',
                          height: '100%',
                        }}
                      >
                        <Typography
                          variant="subtitle2"
                          sx={{
                            mb: 1.5,
                            fontWeight: 600,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1,
                            fontSize: '0.875rem',
                          }}
                        >
                          <Icon icon={databaseIcon} width={16} height={16} />
                          Knowledge Bases ({formData.kb.length})
                        </Typography>
                        <Stack spacing={0.5}>
                          {formData.kb.slice(0, 2).map((kbId, index) => {
                            const kb = availableKBs.find((k) => k.id === kbId);
                            return (
                              <Typography
                                key={index}
                                variant="caption"
                                sx={{
                                  display: 'block',
                                  p: 0.5,
                                  bgcolor: alpha(theme.palette.grey[500], 0.08),
                                  borderRadius: 0.5,
                                  fontSize: '0.75rem',
                                }}
                              >
                                {kb?.name || kbId}
                              </Typography>
                            );
                          })}
                          {formData.kb.length > 2 && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontSize: '0.75rem' }}
                            >
                              +{formData.kb.length - 2} more
                            </Typography>
                          )}
                        </Stack>
                      </Paper>
                    </Grid>
                  )}

                  {/* Applications */}
                  {Array.isArray(formData.apps) && formData.apps.length > 0 && (
                    <Grid item xs={12} sm={6} md={3}>
                      <Paper
                        sx={{
                          p: 2,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: 'divider',
                          height: '100%',
                        }}
                      >
                        <Typography
                          variant="subtitle2"
                          sx={{
                            mb: 1.5,
                            fontWeight: 600,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1,
                            fontSize: '0.875rem',
                          }}
                        >
                          <Icon icon={cogIcon} width={16} height={16} />
                          Applications ({formData.apps.length})
                        </Typography>
                        <Stack spacing={0.5}>
                          {formData.apps.slice(0, 2).map((app, index) => (
                            <Typography
                              key={index}
                              variant="caption"
                              sx={{
                                display: 'block',
                                p: 0.5,
                                bgcolor: alpha(theme.palette.grey[500], 0.08),
                                borderRadius: 0.5,
                                fontSize: '0.75rem',
                              }}
                            >
                              {normalizeDisplayName(app)}
                            </Typography>
                          ))}
                          {formData.apps.length > 2 && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontSize: '0.75rem' }}
                            >
                              +{formData.apps.length - 2} more
                            </Typography>
                          )}
                        </Stack>
                      </Paper>
                    </Grid>
                  )}
                </Grid>
              </Box>
            )}

            {errors.general && (
              <Alert severity="error" sx={{ borderRadius: 1.5 }}>
                {errors.general}
              </Alert>
            )}
          </Stack>
        );
      default:
        return null;
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          minHeight: '75vh',
          display: 'flex',
          flexDirection: 'column',
        },
      }}
    >
      {/* Header */}
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          pb: 1.5,
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Icon icon={sparklesIcon} width={24} height={24} color={theme.palette.primary.main} />
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            {editingAgent
              ? 'Edit Agent'
              : selectedTemplate
                ? `Create from ${selectedTemplate.name}`
                : 'Create New Agent'}
          </Typography>
        </Box>
        <IconButton onClick={onClose} size="small">
          <Icon icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      {/* Stepper */}
      <Box sx={{ p: 3, pb: 1.5 }}>
        <Stepper activeStep={activeStep} alternativeLabel>
          {steps.map((label) => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>
      </Box>

      {/* Content */}
      <DialogContent sx={{ flexGrow: 1, pt: 1, pb: 1.5, ...scrollableContainerStyle }}>
        {loading ? (
          <Box
            sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 200 }}
          >
            <CircularProgress />
          </Box>
        ) : (
          <Fade in key={activeStep}>
            <Box>{renderStepContent()}</Box>
          </Fade>
        )}
      </DialogContent>

      {/* Actions */}
      <Box
        sx={{
          p: 3,
          pt: 1.5,
          borderTop: 1,
          borderColor: 'divider',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <Button
          onClick={handleBack}
          disabled={activeStep === 0 || isCreating}
          startIcon={<Icon icon={arrowLeftIcon} width={16} height={16} />}
          sx={{ borderRadius: 1.5 }}
        >
          Back
        </Button>

        <Button
          onClick={handleNext}
          disabled={isCreating || loading}
          variant="outlined"
          endIcon={
            isCreating ? (
              <CircularProgress size={16} color="inherit" />
            ) : activeStep === steps.length - 1 ? (
              <Icon icon={checkIcon} width={16} height={16} />
            ) : (
              <Icon icon={arrowRightIcon} width={16} height={16} />
            )
          }
          sx={{ borderRadius: 1.5, minWidth: 120, color: theme.palette.primary.main }}
        >
          {isCreating
            ? editingAgent
              ? 'Updating...'
              : 'Creating...'
            : activeStep === steps.length - 1
              ? editingAgent
                ? 'Update Agent'
                : 'Create Agent'
              : 'Next'}
        </Button>
      </Box>
    </Dialog>
  );
};

export default AgentBuilder;

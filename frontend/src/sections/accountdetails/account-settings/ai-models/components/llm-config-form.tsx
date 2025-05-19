import type { SubmitHandler } from 'react-hook-form';
import type { SelectChangeEvent } from '@mui/material';

import { z } from 'zod';
import keyIcon from '@iconify-icons/mdi/key';
import linkIcon from '@iconify-icons/mdi/link';
import closeIcon from '@iconify-icons/mdi/close';
import robotIcon from '@iconify-icons/mdi/robot';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import pencilIcon from '@iconify-icons/mdi/pencil';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm, Controller } from 'react-hook-form';
import cubeIcon from '@iconify-icons/mdi/cube-outline';
import infoIcon from '@iconify-icons/mdi/info-outline';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Link,
  Alert,
  Select,
  Button,
  MenuItem,
  TextField,
  Typography,
  InputLabel,
  IconButton,
  FormControl,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';
// LLM form values interfaces
interface LlmFormValues {
  modelType: 'openAI' | 'azureOpenAI' | 'gemini' | 'anthropic' | 'openAICompatible';
  apiKey: string;
  model: string;
  // clientId?: string;
  endpoint?: string;
  deploymentName?: string;
}

interface OpenAILlmFormValues {
  modelType: 'openAI';
  // clientId: string;
  apiKey: string;
  model: string;
}

interface GeminiLlmFormValues {
  modelType: 'gemini';
  apiKey: string;
  model: string;
}

interface AnthropicLlmFormValues {
  modelType: 'anthropic';
  apiKey: string;
  model: string;
}

interface AzureLlmFormValues {
  modelType: 'azureOpenAI';
  endpoint: string;
  apiKey: string;
  deploymentName: string;
  model: string;
}

interface OpenAICompatibleLlmFormValues {
  modelType: 'openAICompatible';
  endpoint: string;
  apiKey: string;
  model: string;
}

interface LlmConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  provider?: string;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface LlmConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

// Zod schema for OpenAI validation
const openaiSchema = z.object({
  modelType: z.literal('openAI'),
  // clientId: z.string().min(1, 'Client ID is required'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for Gemini validation
const geminiSchema = z.object({
  modelType: z.literal('gemini'),
  // clientId: z.string().min(1, 'Client ID is required'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for anthropic validation
const anthropicSchema = z.object({
  modelType: z.literal('anthropic'),
  // clientId: z.string().min(1, 'Client ID is required'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for Azure OpenAI validation
const azureSchema = z.object({
  modelType: z.literal('azureOpenAI'),
  endpoint: z
    .string()
    .min(1, 'Endpoint is required')
    .startsWith('https://', 'Endpoint must start with https://'),
  apiKey: z.string().min(1, 'API Key is required'),
  deploymentName: z.string().min(1, 'Deployment Name is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for  OpenAI API Compatible validation
const openAICompatibleSchema = z.object({
  modelType: z.literal('openAICompatible'),
  endpoint: z
    .string()
    .min(1, 'Endpoint is required')
    .startsWith('http', 'Endpoint must start with http:// or https://'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

// Combined schema using discriminated union
const llmSchema = z.discriminatedUnion('modelType', [
  openaiSchema,
  azureSchema,
  geminiSchema,
  anthropicSchema,
  openAICompatibleSchema,
]);

// Utility functions for API interaction
const getLlmConfig = async (): Promise<LlmFormValues | null> => {
  try {
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const { data } = response;

    // Check if LLM configuration exists
    if (data.llm && data.llm.length > 0) {
      const llmConfig = data.llm[0];
      const config = llmConfig.configuration;

      // Set the modelType based on the provider
      let modelType: 'openAI' | 'azureOpenAI' | 'gemini' | 'anthropic' | 'openAICompatible';
      if (llmConfig.provider === 'azureOpenAI') {
        modelType = 'azureOpenAI';
      } else if (llmConfig.provider === 'gemini') {
        modelType = 'gemini';
      } else if (llmConfig.provider === 'anthropic') {
        modelType = 'anthropic';
      } else if (llmConfig.provider === 'openAICompatible') {
        modelType = 'openAICompatible';
      } else {
        modelType = 'openAI';
      }
      // Return the configuration with the correct modelType
      return {
        ...config,
        modelType,
      };
    }

    return null;
  } catch (error) {
    console.error('Error fetching LLM configuration:', error);
    throw error;
  }
};

const updateLlmConfig = async (config: LlmFormValues, provider = 'openAI'): Promise<any> => {
  try {
    // First get the current configuration
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const currentConfig = response.data;

    // Remove modelType from the configuration before sending
    const { modelType, ...cleanConfig } = config;

    // Create the updated config object
    const updatedConfig = {
      ...currentConfig,
      llm: [
        {
          provider,
          configuration: cleanConfig,
        },
      ],
    };

    // Update the configuration
    const updateResponse = await axios.post(
      '/api/v1/configurationManager/aiModelsConfig',
      updatedConfig
    );
    return updateResponse;
  } catch (error) {
    console.error('Error updating LLM configuration:', error);
    throw error;
  }
};

const LlmConfigForm = forwardRef<LlmConfigFormRef, LlmConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, provider = 'openAI' }, ref) => {
    const theme = useTheme();
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [showOpenAIPassword, setShowOpenAIPassword] = useState(false);
    const [showGeminiPassword, setShowGeminiPassword] = useState(false);
    const [showAzurePassword, setShowAzurePassword] = useState(false);
    const [showOpenAICompatiblePassword, setShowOpenAICompatiblePassword] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [formSubmitSuccess, setFormSubmitSuccess] = useState(false);
    const [fetchError, setFetchError] = useState<boolean>(false);

    const [modelType, setModelType] = useState<
      'openAI' | 'azureOpenAI' | 'gemini' | 'anthropic' | 'openAICompatible'
    >(
      provider === 'Azure OpenAI'
        ? 'azureOpenAI'
        : provider === 'Gemini'
          ? 'gemini'
          : provider === 'Anthropic'
            ? 'anthropic'
            : provider === 'openAICompatible'
              ? 'openAICompatible'
              : 'openAI'
    );

    // Create separate forms for OpenAI and Azure
    const {
      control: openaiControl,
      handleSubmit: handleOpenAISubmit,
      reset: resetOpenAI,
      formState: { isValid: isOpenAIValid },
    } = useForm<OpenAILlmFormValues>({
      resolver: zodResolver(openaiSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'openAI',
        // clientId: '',
        apiKey: '',
        model: '',
      },
    });

    const {
      control: geminiControl,
      handleSubmit: handleGeminiSubmit,
      reset: resetGemini,
      formState: { isValid: isGeminiValid },
    } = useForm<GeminiLlmFormValues>({
      resolver: zodResolver(geminiSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'gemini',
        // clientId: '',
        apiKey: '',
        model: '',
      },
    });

    const {
      control: anthropicControl,
      handleSubmit: handleAnthropicSubmit,
      reset: resetAnthropic,
      formState: { isValid: isAnthropicValid },
    } = useForm<AnthropicLlmFormValues>({
      resolver: zodResolver(anthropicSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'anthropic',
        // clientId: '',
        apiKey: '',
        model: '',
      },
    });

    const {
      control: azureControl,
      handleSubmit: handleAzureSubmit,
      reset: resetAzure,
      formState: { isValid: isAzureValid },
    } = useForm<AzureLlmFormValues>({
      resolver: zodResolver(azureSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'azureOpenAI',
        endpoint: '',
        apiKey: '',
        deploymentName: '',
        model: '',
      },
    });

    const {
      control: openAICompatibleControl,
      handleSubmit: handleOpenAICompatibleSubmit,
      reset: resetOpenAICompatible,
      formState: { isValid: isOpenAICompatibleValid },
    } = useForm<OpenAICompatibleLlmFormValues>({
      resolver: zodResolver(openAICompatibleSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'openAICompatible',
        endpoint: '',
        apiKey: '',
        model: '',
      },
    });

    // OpenAI and Azure form submit handlers
    const onOpenAISubmit: SubmitHandler<OpenAILlmFormValues> = async (data) => {
      try {
        await updateLlmConfig(data, 'openAI');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        setIsEditing(false);
        setFormSubmitSuccess(true);
      } catch (error) {
        const errorMessage = error.response?.data?.message || 'Failed to save OpenAI configuration';
        setSaveError(errorMessage);
        console.error('Error saving OpenAI configuration:', error);
        setFormSubmitSuccess(false);
      }
    };

    const onGeminiSubmit: SubmitHandler<GeminiLlmFormValues> = async (data) => {
      try {
        await updateLlmConfig(data, 'gemini');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        setIsEditing(false);
        setFormSubmitSuccess(true);
      } catch (error) {
        const errorMessage = error.response?.data?.message || 'Failed to save Gemini configuration';
        setSaveError(errorMessage);
        console.error('Error saving Gemini configuration:', error);
        setFormSubmitSuccess(false);
      }
    };

    const onAnthropicSubmit: SubmitHandler<AnthropicLlmFormValues> = async (data) => {
      try {
        await updateLlmConfig(data, 'anthropic');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        setIsEditing(false);
        setFormSubmitSuccess(true);
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save Anthropic configuration';
        setSaveError(errorMessage);
        console.error('Error saving Anthropic configuration:', error);
        setFormSubmitSuccess(false);
      }
    };

    const onAzureSubmit: SubmitHandler<AzureLlmFormValues> = async (data) => {
      try {
        await updateLlmConfig(data, 'azureOpenAI');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        setIsEditing(false);
        setFormSubmitSuccess(true);
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save Azure OpenAI configuration';
        setSaveError(errorMessage);
        console.error('Error saving Azure OpenAI configuration:', error);
        setFormSubmitSuccess(false);
      }
    };

    const onOpenAICompatibleSubmit: SubmitHandler<AzureLlmFormValues> = async (data) => {
      try {
        await updateLlmConfig(data, 'openAICompatible');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        setIsEditing(false);
        setFormSubmitSuccess(true);
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save OpenAI API compatible configuration';
        setSaveError(errorMessage);
        console.error('Error saving OpenAI API comaptible configuration:', error);
        setFormSubmitSuccess(false);
      }
    };

    // Expose the handleSave method to the parent component
    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => {
        try {
          setIsSaving(true);
          setSaveError(null);
          setFormSubmitSuccess(false);

          // Create a promise that will resolve with the result of the form submission
          return await new Promise<SaveResult>((resolve) => {
            if (modelType === 'openAI') {
              // Execute the OpenAI form submission
              handleOpenAISubmit(async (data) => {
                try {
                  await updateLlmConfig(data, 'openAI');
                  if (onSaveSuccess) {
                    onSaveSuccess();
                  }
                  setIsEditing(false);
                  setFormSubmitSuccess(true);
                  resolve({ success: true });
                } catch (error) {
                  const errorMessage =
                    error.response?.data?.message || 'Failed to save OpenAI configuration';
                  setSaveError(errorMessage);
                  console.error('Error saving OpenAI configuration:', error);
                  setFormSubmitSuccess(false);
                  resolve({ success: false, error: errorMessage });
                } finally {
                  setIsSaving(false);
                }
              })();
            } else if (modelType === 'azureOpenAI') {
              // Execute the Azure form submission
              handleAzureSubmit(async (data) => {
                try {
                  await updateLlmConfig(data, 'azureOpenAI');
                  if (onSaveSuccess) {
                    onSaveSuccess();
                  }
                  setIsEditing(false);
                  setFormSubmitSuccess(true);
                  resolve({ success: true });
                } catch (error) {
                  const errorMessage =
                    error.response?.data?.message || 'Failed to save Azure OpenAI configuration';
                  setSaveError(errorMessage);
                  console.error('Error saving Azure OpenAI configuration:', error);
                  setFormSubmitSuccess(false);
                  resolve({ success: false, error: errorMessage });
                } finally {
                  setIsSaving(false);
                }
              })();
            } else if (modelType === 'openAICompatible') {
              // Execute the Azure form submission
              handleOpenAICompatibleSubmit(async (data) => {
                try {
                  await updateLlmConfig(data, 'openAICompatible');
                  if (onSaveSuccess) {
                    onSaveSuccess();
                  }
                  setIsEditing(false);
                  setFormSubmitSuccess(true);
                  resolve({ success: true });
                } catch (error) {
                  const errorMessage =
                    error.response?.data?.message ||
                    'Failed to save OpenAI API Compatible configuration';
                  setSaveError(errorMessage);
                  console.error('Error saving OpenAI compatible configuration:', error);
                  setFormSubmitSuccess(false);
                  resolve({ success: false, error: errorMessage });
                } finally {
                  setIsSaving(false);
                }
              })();
            } else if (modelType === 'gemini') {
              // Execute the Gemini form submission
              handleGeminiSubmit(async (data) => {
                try {
                  await updateLlmConfig(data, 'gemini');
                  if (onSaveSuccess) {
                    onSaveSuccess();
                  }
                  setIsEditing(false);
                  setFormSubmitSuccess(true);
                  resolve({ success: true });
                } catch (error) {
                  const errorMessage =
                    error.response?.data?.message || 'Failed to save Gemini configuration';
                  setSaveError(errorMessage);
                  console.error('Error saving Gemini configuration:', error);
                  setFormSubmitSuccess(false);
                  resolve({ success: false, error: errorMessage });
                } finally {
                  setIsSaving(false);
                }
              })();
            } else if (modelType === 'anthropic') {
              // Execute the Anthropic form submission
              handleAnthropicSubmit(async (data) => {
                try {
                  await updateLlmConfig(data, 'anthropic');
                  if (onSaveSuccess) {
                    onSaveSuccess();
                  }
                  setIsEditing(false);
                  setFormSubmitSuccess(true);
                  resolve({ success: true });
                } catch (error) {
                  const errorMessage =
                    error.response?.data?.message || 'Failed to save Anthropic configuration';
                  setSaveError(errorMessage);
                  console.error('Error saving Anthropic configuration:', error);
                  setFormSubmitSuccess(false);
                  resolve({ success: false, error: errorMessage });
                } finally {
                  setIsSaving(false);
                }
              })();
            }
          });
        } catch (error) {
          setIsSaving(false);
          console.error('Error in handleSave:', error);
          return {
            success: false,
            error: 'Unexpected error occurred during save operation',
          };
        }
      },
    }));
    // Load existing configuration on mount
    useEffect(() => {
      const fetchConfig = async () => {
        setIsLoading(true);
        try {
          const config = await getLlmConfig();
          setFetchError(false); // Reset fetch error flag on success

          if (config) {
            // Set the model type based on the configuration
            if (config.modelType) {
              setModelType(config.modelType);
            }

            // Reset the appropriate form based on the model type
            if (config.modelType === 'azureOpenAI') {
              // For Azure configuration
              resetAzure({
                modelType: 'azureOpenAI',
                endpoint: config.endpoint || '',
                apiKey: config.apiKey || '',
                deploymentName: config.deploymentName || '',
                model: config.model || '',
              });
            } else if (config.modelType === 'openAICompatible') {
              // For Azure configuration
              resetOpenAICompatible({
                modelType: 'openAICompatible',
                endpoint: config.endpoint || '',
                apiKey: config.apiKey || '',
                model: config.model || '',
              });
            } else if (config.modelType === 'gemini') {
              resetGemini({
                modelType: 'gemini',
                apiKey: config.apiKey || '',
                model: config.model || '',
              });
            } else if (config.modelType === 'anthropic') {
              resetAnthropic({
                modelType: 'anthropic',
                apiKey: config.apiKey || '',
                model: config.model || '',
              });
            } else {
              // Default to OpenAI configuration
              resetOpenAI({
                modelType: 'openAI',
                // clientId: config.clientId || '',
                apiKey: config.apiKey || '',
                model: config.model || '',
              });
            }
          } else {
            // If no configuration exists, still display the forms with default values
            resetOpenAI({
              modelType: 'openAI',
              // clientId: '',
              apiKey: '',
              model: '',
            });
            resetGemini({
              modelType: 'gemini',
              // clientId: '',
              apiKey: '',
              model: '',
            });
            resetAnthropic({
              modelType: 'anthropic',
              // clientId: '',
              apiKey: '',
              model: '',
            });
            resetAzure({
              modelType: 'azureOpenAI',
              endpoint: '',
              apiKey: '',
              deploymentName: '',
              model: '',
            });
            resetOpenAICompatible({
              modelType: 'openAICompatible',
              endpoint: '',
              apiKey: '',
              model: '',
            });
          }
        } catch (error) {
          console.error('Failed to load LLM configuration:', error);
          setFetchError(true); // Set fetch error flag on failure
          setSaveError('Failed to load configuration. View-only mode enabled.');

          // Still reset the forms with default values to display content
          resetOpenAI({
            modelType: 'openAI',
            // clientId: '',
            apiKey: '',
            model: '',
          });
          resetGemini({
            modelType: 'gemini',
            // clientId: '',
            apiKey: '',
            model: '',
          });
          resetAnthropic({
            modelType: 'anthropic',
            // clientId: '',
            apiKey: '',
            model: '',
          });
          resetAzure({
            modelType: 'azureOpenAI',
            endpoint: '',
            apiKey: '',
            deploymentName: '',
            model: '',
          });
          resetOpenAICompatible({
            modelType: 'openAICompatible',
            endpoint: '',
            apiKey: '',
            model: '',
          });
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, [resetOpenAI, resetAzure, resetGemini, resetAnthropic, resetOpenAICompatible]);

    // Reset saveError when it changes
    useEffect(() => {
      if (saveError) {
        // Clear save error after 5 seconds
        const timer = setTimeout(() => {
          setSaveError(null);
        }, 5000);
        return () => clearTimeout(timer);
      }

      // Add an explicit return for when saveError is falsy
      return undefined; // or return () => {}; for an empty cleanup function
    }, [saveError]);

    // Notify parent of validation status
    useEffect(() => {
      let isCurrentFormValid = false;

      if (modelType === 'openAI') {
        isCurrentFormValid = isOpenAIValid;
      } else if (modelType === 'azureOpenAI') {
        isCurrentFormValid = isAzureValid;
      } else if (modelType === 'gemini') {
        isCurrentFormValid = isGeminiValid; // Assuming you have a validation state for Gemini
      } else if (modelType === 'anthropic') {
        isCurrentFormValid = isAnthropicValid; // Assuming you have a validation state for anthropic
      } else if (modelType === 'openAICompatible') {
        isCurrentFormValid = isOpenAICompatibleValid; // Assuming you have a validation state for openai api compatible
      }

      onValidationChange(isCurrentFormValid && isEditing);
    }, [
      isOpenAIValid,
      isAzureValid,
      isGeminiValid,
      isAnthropicValid,
      isOpenAICompatibleValid,
      isEditing,
      modelType,
      onValidationChange,
    ]);

    // Handle model type change
    const handleModelTypeChange = (
      newType: 'openAI' | 'azureOpenAI' | 'gemini' | 'anthropic' | 'openAICompatible'
    ) => {
      setModelType(newType);
      // Don't reset forms - we keep separate state for each
    };

    // Handle edit mode toggle
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - reload current data
        getLlmConfig()
          .then((config) => {
            if (config) {
              // Make sure to set the model type correctly based on configuration
              if (config.modelType) {
                setModelType(config.modelType);
              }

              // Reset the appropriate form based on the model type
              if (config.modelType === 'azureOpenAI') {
                resetAzure({
                  modelType: 'azureOpenAI',
                  endpoint: config.endpoint || '',
                  apiKey: config.apiKey || '',
                  deploymentName: config.deploymentName || '',
                  model: config.model || '',
                });
              } else if (config.modelType === 'openAICompatible') {
                resetOpenAICompatible({
                  modelType: 'openAICompatible',
                  endpoint: config.endpoint || '',
                  apiKey: config.apiKey || '',
                  model: config.model || '',
                });
              } else if (modelType === 'gemini') {
                resetGemini({
                  modelType: 'gemini',
                  apiKey: config.apiKey || '',
                  model: config.model || '',
                });
              } else if (modelType === 'anthropic') {
                resetAnthropic({
                  modelType: 'anthropic',
                  apiKey: config.apiKey || '',
                  model: config.model || '',
                });
              } else {
                resetOpenAI({
                  modelType: 'openAI',
                  // clientId: config.clientId || '',
                  apiKey: config.apiKey || '',
                  model: config.model || '',
                });
              }
            } else {
              // Reset both forms to default values
              resetOpenAI({
                modelType: 'openAI',
                // clientId: '',
                apiKey: '',
                model: '',
              });
              resetGemini({
                modelType: 'gemini',
                // clientId: '',
                apiKey: '',
                model: '',
              });
              resetAnthropic({
                modelType: 'anthropic',
                // clientId: '',
                apiKey: '',
                model: '',
              });
              resetAzure({
                modelType: 'azureOpenAI',
                endpoint: '',
                apiKey: '',
                deploymentName: '',
                model: '',
              });
              resetOpenAICompatible({
                modelType: 'openAICompatible',
                endpoint: '',
                apiKey: '',
                model: '',
              });
            }
          })
          .catch((error) => {
            console.error('Error reloading configuration:', error);
            setFetchError(true);
            setSaveError('Failed to reload configuration. View-only mode enabled.');
          });
        setSaveError(null);
      }
      setIsEditing(!isEditing);
    };

    if (isLoading) {
      return (
        <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
          <CircularProgress size={24} />
        </Box>
      );
    }

    return (
      <>
        <Box
          sx={{
            mb: 3,
            p: 2,
            borderRadius: 1,
            bgcolor: alpha(theme.palette.info.main, 0.04),
            border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 1,
          }}
        >
          <Iconify
            icon={infoIcon}
            width={20}
            height={20}
            color={theme.palette.info.main}
            style={{ marginTop: 2 }}
          />
          <Box>
            <Typography variant="body2" color="text.secondary">
              Configure your LLM provider to enable AI capabilities in your application.
              {modelType === 'azureOpenAI'
                ? ' You need an active Azure subscription with Azure OpenAI Service enabled.'
                : modelType === 'gemini'
                  ? 'Enter your Gemini API credentials to get started.'
                  : ' Enter your OpenAI API credentials to get started.'}
              {fetchError && ' (View-only mode due to connection error)'}
            </Typography>
          </Box>
        </Box>

        {/* Only show Edit button if there was no fetch error */}
        {!fetchError && (
          <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
            <Button
              onClick={handleToggleEdit}
              startIcon={<Iconify icon={isEditing ? closeIcon : pencilIcon} />}
              color={isEditing ? 'error' : 'primary'}
              size="small"
            >
              {isEditing ? 'Cancel' : 'Edit'}
            </Button>
          </Box>
        )}

        <Grid container spacing={2.5} sx={{ mb: 2 }}>
          {/* Model Type selector - common for both forms */}
          <Grid item xs={12}>
            <FormControl fullWidth size="small" disabled={!isEditing || fetchError}>
              <InputLabel>Provider Type</InputLabel>
              <Select
                name="modelType"
                value={modelType}
                label="Provider Type"
                onChange={(e: SelectChangeEvent) => {
                  const newType = e.target.value as
                    | 'openAI'
                    | 'azureOpenAI'
                    | 'gemini'
                    | 'anthropic'
                    | 'openAICompatible';
                  handleModelTypeChange(newType);
                }}
              >
                <MenuItem value="openAI">OpenAI API</MenuItem>
                <MenuItem value="azureOpenAI">Azure OpenAI Service</MenuItem>
                <MenuItem value="gemini">Gemini API</MenuItem>
                <MenuItem value="anthropic">Anthropic API</MenuItem>
                <MenuItem value="openAICompatible">OpenAI API Compatible</MenuItem>
              </Select>
            </FormControl>
          </Grid>

          {/* OpenAI Form */}
          {modelType === 'openAI' && (
            <>
              {/* <Grid item xs={12} md={6}>
                <Controller
                  name="clientId"
                  control={openaiControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Client ID"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'Your OpenAI Client ID'}
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={idIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid> */}

              <Grid item xs={12} md={6}>
                <Controller
                  name="apiKey"
                  control={openaiControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="API Key"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'Your OpenAI API Key'}
                      required
                      disabled={!isEditing || fetchError}
                      type={showOpenAIPassword ? 'text' : 'password'}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={keyIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                        endAdornment: (
                          <InputAdornment position="end">
                            <IconButton
                              onClick={() => setShowOpenAIPassword(!showOpenAIPassword)}
                              edge="end"
                              size="small"
                              disabled={!isEditing || fetchError}
                            >
                              <Iconify
                                icon={showOpenAIPassword ? eyeOffIcon : eyeIcon}
                                width={16}
                                height={16}
                              />
                            </IconButton>
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <Controller
                  name="model"
                  control={openaiControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Model Name"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={
                        fieldState.error?.message || 'e.g., gpt-4o, gpt-4-turbo, gpt-3.5-turbo'
                      }
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={robotIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>
            </>
          )}

          {/* Gemini Form */}
          {modelType === 'gemini' && (
            <>
              <Grid item xs={12} md={6}>
                <Controller
                  name="apiKey"
                  control={geminiControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="API Key"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'Your Gemini API Key'}
                      required
                      disabled={!isEditing || fetchError}
                      type={showGeminiPassword ? 'text' : 'password'}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={keyIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                        endAdornment: (
                          <InputAdornment position="end">
                            <IconButton
                              onClick={() => setShowGeminiPassword(!showGeminiPassword)}
                              edge="end"
                              size="small"
                              disabled={!isEditing || fetchError}
                            >
                              <Iconify
                                icon={showGeminiPassword ? eyeOffIcon : eyeIcon}
                                width={16}
                                height={16}
                              />
                            </IconButton>
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <Controller
                  name="model"
                  control={geminiControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Model Name"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'e.g., gemini-2.0-flash'}
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={robotIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>
            </>
          )}
          {modelType === 'anthropic' && (
            <>
              <Grid item xs={12} md={6}>
                <Controller
                  name="apiKey"
                  control={anthropicControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="API Key"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'Your Anthropic API Key'}
                      required
                      disabled={!isEditing || fetchError}
                      type={showGeminiPassword ? 'text' : 'password'}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={keyIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                        endAdornment: (
                          <InputAdornment position="end">
                            <IconButton
                              onClick={() => setShowGeminiPassword(!showGeminiPassword)}
                              edge="end"
                              size="small"
                              disabled={!isEditing || fetchError}
                            >
                              <Iconify
                                icon={showGeminiPassword ? eyeOffIcon : eyeIcon}
                                width={16}
                                height={16}
                              />
                            </IconButton>
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <Controller
                  name="model"
                  control={anthropicControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Model Name"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'e.g., claude-3-7-sonnet-20250219'}
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={robotIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>
            </>
          )}

          {/* Azure OpenAI Form */}
          {modelType === 'azureOpenAI' && (
            <>
              <Grid item xs={12} md={6}>
                <Controller
                  name="endpoint"
                  control={azureControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Endpoint URL"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={
                        fieldState.error?.message || 'e.g., https://your-resource.openai.azure.com/'
                      }
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={linkIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <Controller
                  name="deploymentName"
                  control={azureControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Deployment Name"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'Your Azure OpenAI deployment name'}
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={cubeIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <Controller
                  name="apiKey"
                  control={azureControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="API Key"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'Your Azure OpenAI API Key'}
                      required
                      disabled={!isEditing || fetchError}
                      type={showAzurePassword ? 'text' : 'password'}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={keyIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                        endAdornment: (
                          <InputAdornment position="end">
                            <IconButton
                              onClick={() => setShowAzurePassword(!showAzurePassword)}
                              edge="end"
                              size="small"
                              disabled={!isEditing || fetchError}
                            >
                              <Iconify
                                icon={showAzurePassword ? eyeOffIcon : eyeIcon}
                                width={16}
                                height={16}
                              />
                            </IconButton>
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <Controller
                  name="model"
                  control={azureControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Model Name"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'e.g., gpt-4, gpt-35-turbo'}
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={robotIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>
            </>
          )}

          {/* OpenAI Compatible Form */}
          {modelType === 'openAICompatible' && (
            <>
              <Grid item xs={12} md={6}>
                <Controller
                  name="endpoint"
                  control={openAICompatibleControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Endpoint URL"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={
                        fieldState.error?.message || 'e.g., https://api.together.xyz/v1/'
                      }
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={linkIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <Controller
                  name="apiKey"
                  control={openAICompatibleControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="API Key"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'Your OpenAI API Compatible API Key'}
                      required
                      disabled={!isEditing || fetchError}
                      type={showOpenAICompatiblePassword ? 'text' : 'password'}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={keyIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                        endAdornment: (
                          <InputAdornment position="end">
                            <IconButton
                              onClick={() =>
                                setShowOpenAICompatiblePassword(!showOpenAICompatiblePassword)
                              }
                              edge="end"
                              size="small"
                              disabled={!isEditing || fetchError}
                            >
                              <Iconify
                                icon={showOpenAICompatiblePassword ? eyeOffIcon : eyeIcon}
                                width={16}
                                height={16}
                              />
                            </IconButton>
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <Controller
                  name="model"
                  control={openAICompatibleControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Model Name"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'e.g., deepseek-ai/DeepSeek-V3'}
                      required
                      disabled={!isEditing || fetchError}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={robotIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                      }}
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          '& fieldset': {
                            borderColor: alpha(theme.palette.text.primary, 0.15),
                          },
                        },
                      }}
                    />
                  )}
                />
              </Grid>
            </>
          )}
        </Grid>

        {saveError && (
          <Alert severity="error" sx={{ mt: 3 }}>
            {saveError}
          </Alert>
        )}

        {isSaving && (
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
            <CircularProgress size={24} />
          </Box>
        )}
        <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
          Refer to{' '}
          <Link href="https://docs.pipeshub.com/ai-models/overview" target="_blank" rel="noopener">
            the documentation
          </Link>{' '}
          for more information.
        </Alert>
      </>
    );
  }
);

export default LlmConfigForm;

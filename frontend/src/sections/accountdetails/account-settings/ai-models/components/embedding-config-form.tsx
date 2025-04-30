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
import infoIcon from '@iconify-icons/mdi/info-outline';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
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

// Embedding form values interfaces
interface EmbeddingFormValues {
  modelType: 'openAI' | 'azureOpenAI' | 'default';
  apiKey?: string;
  model?: string;
  endpoint?: string;
}

interface OpenAIEmbeddingFormValues {
  modelType: 'openAI';
  apiKey: string;
  model: string;
}

interface AzureEmbeddingFormValues {
  modelType: 'azureOpenAI';
  endpoint: string;
  apiKey: string;
  model: string;
}

interface DefaultEmbeddingFormValues {
  modelType: 'default';
}

interface EmbeddingConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  provider?: string;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface EmbeddingConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

// Zod schema for OpenAI validation
const openaiSchema = z.object({
  modelType: z.literal('openAI'),
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
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for Default option (no validation needed)
const defaultSchema = z.object({
  modelType: z.literal('default'),
});

// Combined schema using discriminated union
const embeddingSchema = z.discriminatedUnion('modelType', [
  openaiSchema,
  azureSchema,
  defaultSchema,
]);

// Utility functions for API interaction
const getEmbeddingConfig = async (): Promise<EmbeddingFormValues | null> => {
  try {
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const { data } = response;

    // Check if Embedding configuration exists
    if (data.embedding && data.embedding.length > 0) {
      const embeddingConfig = data.embedding[0];
      const config = embeddingConfig.configuration;

      // Set the modelType based on the provider
      let modelType: 'openAI' | 'azureOpenAI' | 'default';
      if (embeddingConfig.provider === 'azureOpenAI') {
        modelType = 'azureOpenAI';
      } else {
        modelType = 'openAI';
      }

      // Return the configuration with the correct modelType
      return {
        ...config,
        modelType,
      };
    }

    // If no embedding configuration is found, return default type
    return {
      modelType: 'default',
    };
  } catch (error) {
    console.error('Error fetching Embedding configuration:', error);
    throw error;
  }
};

const updateEmbeddingConfig = async (
  config: EmbeddingFormValues,
  provider = 'openAI'
): Promise<any> => {
  try {
    // First get the current configuration
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const currentConfig = response.data;

    // If using default, send an empty array for embeddings
    if (config.modelType === 'default') {
      const updatedConfig = {
        ...currentConfig,
        embedding: [], // Empty array for default
      };

      // Update the configuration
      const updateResponse = await axios.post(
        '/api/v1/configurationManager/aiModelsConfig',
        updatedConfig
      );
      return updateResponse;
    }

    // For OpenAI or Azure, prepare the configuration
    // Remove modelType from the configuration before sending
    const { modelType, ...cleanConfig } = config;

    // Create the updated config object
    const updatedConfig = {
      ...currentConfig,
      embedding: [
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
    console.error('Error updating Embedding configuration:', error);
    throw error;
  }
};

const EmbeddingConfigForm = forwardRef<EmbeddingConfigFormRef, EmbeddingConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, provider = 'openAI' }, ref) => {
    const theme = useTheme();
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [showOpenAIPassword, setShowOpenAIPassword] = useState(false);
    const [showAzurePassword, setShowAzurePassword] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [formSubmitSuccess, setFormSubmitSuccess] = useState(false);
    const [fetchError, setFetchError] = useState<boolean>(false);

    const [modelType, setModelType] = useState<'openAI' | 'azureOpenAI' | 'default'>(
      provider === 'Azure OpenAI' ? 'azureOpenAI' : provider === 'default' ? 'default' : 'openAI'
    );

    // Create separate forms for OpenAI, Azure, and Default
    const {
      control: openaiControl,
      handleSubmit: handleOpenAISubmit,
      reset: resetOpenAI,
      formState: { isValid: isOpenAIValid },
    } = useForm<OpenAIEmbeddingFormValues>({
      resolver: zodResolver(openaiSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'openAI',
        apiKey: '',
        model: '',
      },
    });

    const {
      control: azureControl,
      handleSubmit: handleAzureSubmit,
      reset: resetAzure,
      formState: { isValid: isAzureValid },
    } = useForm<AzureEmbeddingFormValues>({
      resolver: zodResolver(azureSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'azureOpenAI',
        endpoint: '',
        apiKey: '',
        model: '',
      },
    });

    const {
      control: defaultControl,
      handleSubmit: handleDefaultSubmit,
      formState: { isValid: isDefaultValid },
    } = useForm<DefaultEmbeddingFormValues>({
      resolver: zodResolver(defaultSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'default',
      },
    });

    // OpenAI form submit handler
    const onOpenAISubmit: SubmitHandler<OpenAIEmbeddingFormValues> = async (data) => {
      try {
        await updateEmbeddingConfig(data, 'openAI');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        setIsEditing(false);
        setFormSubmitSuccess(true);
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save OpenAI embedding configuration';
        setSaveError(errorMessage);
        console.error('Error saving OpenAI embedding configuration:', error);
        setFormSubmitSuccess(false);
      }
    };

    // Azure form submit handler
    const onAzureSubmit: SubmitHandler<AzureEmbeddingFormValues> = async (data) => {
      try {
        await updateEmbeddingConfig(data, 'azureOpenAI');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        setIsEditing(false);
        setFormSubmitSuccess(true);
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save Azure OpenAI embedding configuration';
        setSaveError(errorMessage);
        console.error('Error saving Azure OpenAI embedding configuration:', error);
        setFormSubmitSuccess(false);
      }
    };

    // Default form submit handler
    const onDefaultSubmit: SubmitHandler<DefaultEmbeddingFormValues> = async (data) => {
      try {
        await updateEmbeddingConfig(data, 'default');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        setIsEditing(false);
        setFormSubmitSuccess(true);
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save default embedding configuration';
        setSaveError(errorMessage);
        console.error('Error saving default embedding configuration:', error);
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
                  await updateEmbeddingConfig(data, 'openAI');
                  if (onSaveSuccess) {
                    onSaveSuccess();
                  }
                  setIsEditing(false);
                  setFormSubmitSuccess(true);
                  resolve({ success: true });
                } catch (error) {
                  const errorMessage =
                    error.response?.data?.message ||
                    'Failed to save OpenAI embedding configuration';
                  setSaveError(errorMessage);
                  console.error('Error saving OpenAI embedding configuration:', error);
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
                  await updateEmbeddingConfig(data, 'azureOpenAI');
                  if (onSaveSuccess) {
                    onSaveSuccess();
                  }
                  setIsEditing(false);
                  setFormSubmitSuccess(true);
                  resolve({ success: true });
                } catch (error) {
                  const errorMessage =
                    error.response?.data?.message ||
                    'Failed to save Azure OpenAI embedding configuration';
                  setSaveError(errorMessage);
                  console.error('Error saving Azure OpenAI embedding configuration:', error);
                  setFormSubmitSuccess(false);
                  resolve({ success: false, error: errorMessage });
                } finally {
                  setIsSaving(false);
                }
              })();
            } else if (modelType === 'default') {
              // Execute the default form submission
              handleDefaultSubmit(async (data) => {
                try {
                  await updateEmbeddingConfig(data, 'default');
                  if (onSaveSuccess) {
                    onSaveSuccess();
                  }
                  setIsEditing(false);
                  setFormSubmitSuccess(true);
                  resolve({ success: true });
                } catch (error) {
                  const errorMessage =
                    error.response?.data?.message ||
                    'Failed to save default embedding configuration';
                  setSaveError(errorMessage);
                  console.error('Error saving default embedding configuration:', error);
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
          const config = await getEmbeddingConfig();
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
                model: config.model || '',
              });
            } else if (config.modelType === 'openAI') {
              // For OpenAI configuration
              resetOpenAI({
                modelType: 'openAI',
                apiKey: config.apiKey || '',
                model: config.model || '',
              });
            }
            // Default case is handled by the default state
          } else {
            // If no configuration exists, still display the forms with default values
            resetOpenAI({
              modelType: 'openAI',
              apiKey: '',
              model: '',
            });
            resetAzure({
              modelType: 'azureOpenAI',
              endpoint: '',
              apiKey: '',
              model: '',
            });
          }
        } catch (error) {
          console.error('Failed to load Embedding configuration:', error);
          setFetchError(true); // Set fetch error flag on failure
          setSaveError('Failed to load configuration. View-only mode enabled.');

          // Still reset the forms with default values to display content
          resetOpenAI({
            modelType: 'openAI',
            apiKey: '',
            model: '',
          });
          resetAzure({
            modelType: 'azureOpenAI',
            endpoint: '',
            apiKey: '',
            model: '',
          });
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, [resetOpenAI, resetAzure]);

    // Reset saveError when it changes
    useEffect(() => {
      if (saveError) {
        // Clear save error after 5 seconds
        const timer = setTimeout(() => {
          setSaveError(null);
        }, 5000);
        return () => clearTimeout(timer);
      }
      return undefined;
    }, [saveError]);

    // Notify parent of validation status
    useEffect(() => {
      let isCurrentFormValid = false;

      if (modelType === 'openAI') {
        isCurrentFormValid = isOpenAIValid;
      } else if (modelType === 'azureOpenAI') {
        isCurrentFormValid = isAzureValid;
      } else if (modelType === 'default') {
        isCurrentFormValid = isDefaultValid;
      }

      onValidationChange(isCurrentFormValid && isEditing);
    }, [isOpenAIValid, isAzureValid, isDefaultValid, isEditing, modelType, onValidationChange]);

    // Handle model type change
    const handleModelTypeChange = (newType: 'openAI' | 'azureOpenAI' | 'default') => {
      setModelType(newType);
      // Don't reset forms - we keep separate state for each
    };

    // Handle edit mode toggle
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - reload current data
        getEmbeddingConfig()
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
                  model: config.model || '',
                });
              } else if (config.modelType === 'openAI') {
                resetOpenAI({
                  modelType: 'openAI',
                  apiKey: config.apiKey || '',
                  model: config.model || '',
                });
              }
              // Default case is handled by the default state
            } else {
              // Reset both forms to default values
              resetOpenAI({
                modelType: 'openAI',
                apiKey: '',
                model: '',
              });
              resetAzure({
                modelType: 'azureOpenAI',
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
              Configure your Embedding model to enable semantic search and document retrieval in
              your application.
              {modelType === 'azureOpenAI'
                ? ' You need an active Azure subscription with Azure OpenAI Service enabled.'
                : modelType === 'default'
                  ? ' Using the default embedding model provided by the system.'
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
          {/* Model Type selector - common for all forms */}
          <Grid item xs={12}>
            <FormControl fullWidth size="small" disabled={!isEditing || fetchError}>
              <InputLabel>Provider Type</InputLabel>
              <Select
                name="modelType"
                value={modelType}
                label="Provider Type"
                onChange={(e: SelectChangeEvent) => {
                  const newType = e.target.value as 'openAI' | 'azureOpenAI' | 'default';
                  handleModelTypeChange(newType);
                }}
              >
                <MenuItem value="openAI">OpenAI API</MenuItem>
                <MenuItem value="azureOpenAI">Azure OpenAI Service</MenuItem>
                <MenuItem value="default">Default (System Provided)</MenuItem>
              </Select>
            </FormControl>
          </Grid>

          {/* OpenAI Form */}
          {modelType === 'openAI' && (
            <>
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
                        fieldState.error?.message ||
                        'e.g., text-embedding-3-small, text-embedding-3-large'
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

              <Grid item xs={12} md={12}>
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
                      helperText={
                        fieldState.error?.message ||
                        'e.g., text-embedding-3-small, text-embedding-3-large'
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

          {/* Default Option - No fields needed */}
          {modelType === 'default' && (
            <Grid item xs={12}>
              <Alert severity="info" sx={{ mt: 1 }}>
                Using the default embedding model provided by the system. No additional
                configuration required.
              </Alert>
            </Grid>
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
      </>
    );
  }
);

export default EmbeddingConfigForm;

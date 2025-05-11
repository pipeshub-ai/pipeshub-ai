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
import React, { useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';

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

// Define the loading overlay style
const loadingOverlayStyle = {
  position: 'absolute',
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  backgroundColor: 'rgba(255, 255, 255, 0)',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 1000,
  backdropFilter: 'blur(0px)',
  borderRadius: 1,
};

// Embedding form values interfaces
interface EmbeddingFormValues {
  modelType: 'openAI' | 'azureOpenAI' | 'sentenceTransformers' | 'gemini' | 'cohere' | 'default';
  apiKey?: string;
  model?: string;
  endpoint?: string;
}

interface OpenAIEmbeddingFormValues {
  modelType: 'openAI';
  apiKey: string;
  model: string;
}
interface GeminiEmbeddingFormValues {
  modelType: 'gemini';
  apiKey: string;
  model: string;
}
interface CohereEmbeddingFormValues {
  modelType: 'cohere';
  apiKey: string;
  model: string;
}

interface AzureEmbeddingFormValues {
  modelType: 'azureOpenAI';
  endpoint: string;
  apiKey: string;
  model: string;
}

interface SentenceTransformersFormValues {
  modelType: 'sentenceTransformers';
  model: string;
  apiKey?: string;
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

// Zod schema for Gemini validation
const geminiSchema = z.object({
  modelType: z.literal('gemini'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for Cohere validation
const cohereSchema = z.object({
  modelType: z.literal('cohere'),
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

// Zod schema for Sentence Transformers validation
const sentenceTransformersSchema = z.object({
  modelType: z.literal('sentenceTransformers'),
  model: z.string().min(1, 'Model is required'),
  apiKey: z.string().optional(),
});

// Zod schema for Default option (no validation needed)
const defaultSchema = z.object({
  modelType: z.literal('default'),
});

// Combined schema using discriminated union
const embeddingSchema = z.discriminatedUnion('modelType', [
  openaiSchema,
  azureSchema,
  sentenceTransformersSchema,
  geminiSchema,
  cohereSchema,
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
      let modelType:
        | 'openAI'
        | 'azureOpenAI'
        | 'sentenceTransformers'
        | 'gemini'
        | 'cohere'
        | 'default';
      if (embeddingConfig.provider === 'azureOpenAI') {
        modelType = 'azureOpenAI';
      } else if (embeddingConfig.provider === 'sentenceTransformers') {
        modelType = 'sentenceTransformers';
      } else if (embeddingConfig.provider === 'gemini') {
        modelType = 'gemini';
      } else if (embeddingConfig.provider === 'cohere') {
        modelType = 'cohere';
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

    // For OpenAI, Azure, or Sentence Transformers, prepare the configuration
    // Remove modelType from the configuration before sending
    const { modelType, ...cleanConfig } = config;

    // Determine the correct provider based on modelType
    const actualProvider =
      modelType === 'azureOpenAI'
        ? 'azureOpenAI'
        : modelType === 'sentenceTransformers'
          ? 'sentenceTransformers'
          : modelType === 'gemini'
            ? 'gemini'
            : modelType === 'cohere'
              ? 'cohere'
              : 'openAI';

    // Create the updated config object
    const updatedConfig = {
      ...currentConfig,
      embedding: [
        {
          provider: actualProvider,
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
    const [showGeminiPassword, setShowGeminiPassword] = useState(false);
    const [showCoherePassword, setShowCoherePassword] = useState(false);
    const [showAzurePassword, setShowAzurePassword] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [formSubmitSuccess, setFormSubmitSuccess] = useState(false);
    const [fetchError, setFetchError] = useState<boolean>(false);

    // Refs to prevent race conditions
    const isSavingRef = useRef(false);
    const formSubmitSuccessRef = useRef(false);
    const saveErrorRef = useRef<string | null>(null);

    const [modelType, setModelType] = useState<
      'openAI' | 'azureOpenAI' | 'sentenceTransformers' | 'cohere' | 'gemini' | 'default'
    >(
      provider === 'azureOpenAI'
        ? 'azureOpenAI'
        : provider === 'sentenceTransformers'
          ? 'sentenceTransformers'
          : provider === 'default'
            ? 'default'
            : provider === 'gemini'
              ? 'gemini'
              : provider === 'cohere'
                ? 'cohere'
                : 'openAI'
    );

    // Create separate forms for each model type
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
      control: geminiControl,
      handleSubmit: handleGeminiSubmit,
      reset: resetGemini,
      formState: { isValid: isGeminiValid },
    } = useForm<GeminiEmbeddingFormValues>({
      resolver: zodResolver(geminiSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'gemini',
        apiKey: '',
        model: '',
      },
    });

    const {
      control: cohereControl,
      handleSubmit: handleCohereSubmit,
      reset: resetCohere,
      formState: { isValid: isCohereValid },
    } = useForm<CohereEmbeddingFormValues>({
      resolver: zodResolver(cohereSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'cohere',
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
      control: sentenceTransformersControl,
      handleSubmit: handleSentenceTransformersSubmit,
      reset: resetSentenceTransformers,
      formState: { isValid: isSentenceTransformersValid },
    } = useForm<SentenceTransformersFormValues>({
      resolver: zodResolver(sentenceTransformersSchema),
      mode: 'onChange',
      defaultValues: {
        modelType: 'sentenceTransformers',
        model: '',
        apiKey: '',
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

    // Update state from refs in a controlled way
    useEffect(() => {
      if (isSavingRef.current !== isSaving) {
        setIsSaving(isSavingRef.current);
      }

      if (formSubmitSuccessRef.current !== formSubmitSuccess) {
        setFormSubmitSuccess(formSubmitSuccessRef.current);
      }

      if (saveErrorRef.current !== saveError) {
        setSaveError(saveErrorRef.current);
      }
    }, [isSaving, formSubmitSuccess, saveError]);

    // OpenAI form submit handler
    const onOpenAISubmit: SubmitHandler<OpenAIEmbeddingFormValues> = async (data) => {
      try {
        await updateEmbeddingConfig(data, 'openAI');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        formSubmitSuccessRef.current = true;
        setFormSubmitSuccess(true);
        return true;
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save OpenAI embedding configuration';
        saveErrorRef.current = errorMessage;
        setSaveError(errorMessage);
        console.error('Error saving OpenAI embedding configuration:', error);
        formSubmitSuccessRef.current = false;
        setFormSubmitSuccess(false);
        return false;
      } finally {
        // Only set editing to false after successful save
        if (formSubmitSuccessRef.current) {
          setIsEditing(false);
        }
      }
    };
    const onGeminiSubmit: SubmitHandler<GeminiEmbeddingFormValues> = async (data) => {
      try {
        await updateEmbeddingConfig(data, 'gemini');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        formSubmitSuccessRef.current = true;
        setFormSubmitSuccess(true);
        return true;
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save Gemini embedding configuration';
        saveErrorRef.current = errorMessage;
        setSaveError(errorMessage);
        console.error('Error saving Gemini embedding configuration:', error);
        formSubmitSuccessRef.current = false;
        setFormSubmitSuccess(false);
        return false;
      } finally {
        // Only set editing to false after successful save
        if (formSubmitSuccessRef.current) {
          setIsEditing(false);
        }
      }
    };
    const onCohereSubmit: SubmitHandler<CohereEmbeddingFormValues> = async (data) => {
      try {
        await updateEmbeddingConfig(data, 'cohere');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        formSubmitSuccessRef.current = true;
        setFormSubmitSuccess(true);
        return true;
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save Cohere embedding configuration';
        saveErrorRef.current = errorMessage;
        setSaveError(errorMessage);
        console.error('Error saving Cohere embedding configuration:', error);
        formSubmitSuccessRef.current = false;
        setFormSubmitSuccess(false);
        return false;
      } finally {
        // Only set editing to false after successful save
        if (formSubmitSuccessRef.current) {
          setIsEditing(false);
        }
      }
    };

    // Azure form submit handler
    const onAzureSubmit: SubmitHandler<AzureEmbeddingFormValues> = async (data) => {
      try {
        await updateEmbeddingConfig(data, 'azureOpenAI');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        formSubmitSuccessRef.current = true;
        setFormSubmitSuccess(true);
        return true;
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save Azure OpenAI embedding configuration';
        saveErrorRef.current = errorMessage;
        setSaveError(errorMessage);
        console.error('Error saving Azure OpenAI embedding configuration:', error);
        formSubmitSuccessRef.current = false;
        setFormSubmitSuccess(false);
        return false;
      } finally {
        // Only set editing to false after successful save
        if (formSubmitSuccessRef.current) {
          setIsEditing(false);
        }
      }
    };

    // Sentence Transformers form submit handler
    const onSentenceTransformersSubmit: SubmitHandler<SentenceTransformersFormValues> = async (
      data
    ) => {
      try {
        await updateEmbeddingConfig(data, 'sentenceTransformers');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        formSubmitSuccessRef.current = true;
        setFormSubmitSuccess(true);
        return true;
      } catch (error) {
        const errorMessage =
          error.response?.data?.message ||
          'Failed to save Sentence Transformers embedding configuration';
        saveErrorRef.current = errorMessage;
        setSaveError(errorMessage);
        console.error('Error saving Sentence Transformers embedding configuration:', error);
        formSubmitSuccessRef.current = false;
        setFormSubmitSuccess(false);
        return false;
      } finally {
        // Only set editing to false after successful save
        if (formSubmitSuccessRef.current) {
          setIsEditing(false);
        }
      }
    };

    // Default form submit handler
    const onDefaultSubmit: SubmitHandler<DefaultEmbeddingFormValues> = async (data) => {
      try {
        await updateEmbeddingConfig(data, 'default');
        if (onSaveSuccess) {
          onSaveSuccess();
        }
        formSubmitSuccessRef.current = true;
        setFormSubmitSuccess(true);
        return true;
      } catch (error) {
        const errorMessage =
          error.response?.data?.message || 'Failed to save default embedding configuration';
        saveErrorRef.current = errorMessage;
        setSaveError(errorMessage);
        console.error('Error saving default embedding configuration:', error);
        formSubmitSuccessRef.current = false;
        setFormSubmitSuccess(false);
        return false;
      } finally {
        // Only set editing to false after successful save
        if (formSubmitSuccessRef.current) {
          setIsEditing(false);
        }
      }
    };

    // Expose the handleSave method to the parent component
    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => {
        try {
          // Prevent multiple save operations
          if (isSavingRef.current) {
            return {
              success: false,
              error: 'A save operation is already in progress',
            };
          }

          // Set loading state BEFORE any other operations
          isSavingRef.current = true;
          setIsSaving(true);

          // Give UI time to render loading state before continuing
          await new Promise((resolve) => setTimeout(resolve, 50));

          saveErrorRef.current = null;
          setSaveError(null);
          formSubmitSuccessRef.current = false;
          setFormSubmitSuccess(false);

          let formResult: boolean | undefined = false;

          if (modelType === 'openAI') {
            // Execute the form submission
            formResult = await new Promise<boolean>((resolve) => {
              handleOpenAISubmit(async (data) => {
                const result: any = await onOpenAISubmit(data);
                resolve(result);
              })();
            });
          } else if (modelType === 'azureOpenAI') {
            // Execute the form submission
            formResult = await new Promise<boolean>((resolve) => {
              handleAzureSubmit(async (data) => {
                const result: any = await onAzureSubmit(data);
                resolve(result);
              })();
            });
          } else if (modelType === 'sentenceTransformers') {
            // Execute the form submission
            formResult = await new Promise<boolean>((resolve) => {
              handleSentenceTransformersSubmit(async (data) => {
                const result: any = await onSentenceTransformersSubmit(data);
                resolve(result);
              })();
            });
          } else if (modelType === 'default') {
            // Execute the form submission for default
            formResult = await new Promise<boolean>((resolve) => {
              handleDefaultSubmit(async (data) => {
                const result: any = await onDefaultSubmit(data);
                resolve(result);
              })();
            });
          } else if (modelType === 'gemini') {
            // Execute the form submission
            formResult = await new Promise<boolean>((resolve) => {
              handleGeminiSubmit(async (data) => {
                const result: any = await onGeminiSubmit(data);
                resolve(result);
              })();
            });
          } else if (modelType === 'cohere') {
            // Execute the form submission
            formResult = await new Promise<boolean>((resolve) => {
              handleCohereSubmit(async (data) => {
                const result: any = await onCohereSubmit(data);
                resolve(result);
              })();
            });
          }

          // Keep loading state for a short delay to avoid UI flickering
          await new Promise((resolve) => setTimeout(resolve, 300));

          // If there was an error during form submission
          if (formResult === false) {
            isSavingRef.current = false;
            setIsSaving(false);
            return {
              success: false,
              error: saveErrorRef.current || 'Failed to save configuration',
            };
          }

          // If we got here, the submission was successful
          isSavingRef.current = false;
          setIsSaving(false);
          return { success: true };
        } catch (error) {
          // Small delay before changing state to avoid UI flickering
          await new Promise((resolve) => setTimeout(resolve, 300));

          isSavingRef.current = false;
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
            } else if (config.modelType === 'sentenceTransformers') {
              // For Sentence Transformers configuration
              resetSentenceTransformers({
                modelType: 'sentenceTransformers',
                model: config.model || '',
                apiKey: config.apiKey || '',
              });
            } else if (config.modelType === 'openAI') {
              // For OpenAI configuration
              resetOpenAI({
                modelType: 'openAI',
                apiKey: config.apiKey || '',
                model: config.model || '',
              });
            } else if (config.modelType === 'gemini') {
              // For OpenAI configuration
              resetGemini({
                modelType: 'gemini',
                apiKey: config.apiKey || '',
                model: config.model || '',
              });
            } else if (config.modelType === 'cohere') {
              // For OpenAI configuration
              resetCohere({
                modelType: 'cohere',
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
            resetSentenceTransformers({
              modelType: 'sentenceTransformers',
              model: '',
              apiKey: '',
            });
            resetGemini({
              modelType: 'gemini',
              apiKey: '',
              model: '',
            });
            resetCohere({
              modelType: 'cohere',
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
          resetSentenceTransformers({
            modelType: 'sentenceTransformers',
            model: '',
            apiKey: '',
          });
          resetGemini({
            modelType: 'gemini',
            apiKey: '',
            model: '',
          });
          resetCohere({
            modelType: 'cohere',
            apiKey: '',
            model: '',
          });
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, [resetOpenAI, resetAzure, resetSentenceTransformers, resetCohere, resetGemini]);

    // Reset saveError after a timeout
    useEffect(() => {
      if (saveError) {
        // Clear save error after 5 seconds
        const timer = setTimeout(() => {
          saveErrorRef.current = null;
          setSaveError(null);
        }, 5000);
        return () => clearTimeout(timer);
      }
      return undefined;
    }, [saveError]);

    // Auto-hide success message after a timeout
    useEffect(() => {
      if (formSubmitSuccess) {
        // Clear success message after 5 seconds
        const timer = setTimeout(() => {
          formSubmitSuccessRef.current = false;
          setFormSubmitSuccess(false);
        }, 5000);
        return () => clearTimeout(timer);
      }
      return undefined;
    }, [formSubmitSuccess]);

    // Notify parent of validation status
    useEffect(() => {
      let isCurrentFormValid = false;

      if (modelType === 'openAI') {
        isCurrentFormValid = isOpenAIValid;
      } else if (modelType === 'azureOpenAI') {
        isCurrentFormValid = isAzureValid;
      } else if (modelType === 'sentenceTransformers') {
        isCurrentFormValid = isSentenceTransformersValid;
      } else if (modelType === 'default') {
        isCurrentFormValid = isDefaultValid;
      } else if (modelType === 'gemini') {
        isCurrentFormValid = isGeminiValid;
      } else if (modelType === 'cohere') {
        isCurrentFormValid = isCohereValid;
      }

      onValidationChange(isCurrentFormValid && isEditing);
    }, [
      isOpenAIValid,
      isAzureValid,
      isSentenceTransformersValid,
      isDefaultValid,
      isGeminiValid,
      isCohereValid,
      isEditing,
      modelType,
      onValidationChange,
    ]);

    // Handle model type change
    const handleModelTypeChange = (
      newType: 'openAI' | 'azureOpenAI' | 'sentenceTransformers' | 'cohere' | 'gemini' | 'default'
    ) => {
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
              } else if (config.modelType === 'sentenceTransformers') {
                resetSentenceTransformers({
                  modelType: 'sentenceTransformers',
                  model: config.model || '',
                  apiKey: config.apiKey || '',
                });
              } else if (config.modelType === 'openAI') {
                resetOpenAI({
                  modelType: 'openAI',
                  apiKey: config.apiKey || '',
                  model: config.model || '',
                });
              } else if (config.modelType === 'gemini') {
                resetGemini({
                  modelType: 'gemini',
                  apiKey: config.apiKey || '',
                  model: config.model || '',
                });
              } else if (config.modelType === 'cohere') {
                resetCohere({
                  modelType: 'cohere',
                  apiKey: config.apiKey || '',
                  model: config.model || '',
                });
              }
              // Default case is handled by the default state
            } else {
              // Reset all forms to default values
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
              resetSentenceTransformers({
                modelType: 'sentenceTransformers',
                model: '',
                apiKey: '',
              });
              resetGemini({
                modelType: 'gemini',
                apiKey: '',
                model: '',
              });
              resetCohere({
                modelType: 'cohere',
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
        saveErrorRef.current = null;
        setSaveError(null);
        formSubmitSuccessRef.current = false;
        setFormSubmitSuccess(false);
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
      <Box sx={{ position: 'relative' }}>
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
                : modelType === 'sentenceTransformers'
                  ? ' Use Sentence Transformers for local embedding generation.'
                  : modelType === 'default'
                    ? ' Using the default embedding model provided by the system.'
                    : modelType === 'gemini'
                      ? ' Enter your Gemini API credentials to get started.'
                      : modelType === 'cohere'
                        ? 'Enter your Cohere API credentials to get started.'
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
              disabled={isSaving}
            >
              {isEditing ? 'Cancel' : 'Edit'}
            </Button>
          </Box>
        )}

        <Grid container spacing={2.5} sx={{ mb: 2 }}>
          {/* Model Type selector - common for all forms */}
          <Grid item xs={12}>
            <FormControl fullWidth size="small" disabled={!isEditing || fetchError || isSaving}>
              <InputLabel>Provider Type</InputLabel>
              <Select
                name="modelType"
                value={modelType}
                label="Provider Type"
                onChange={(e: SelectChangeEvent) => {
                  const newType = e.target.value as
                    | 'openAI'
                    | 'azureOpenAI'
                    | 'sentenceTransformers'
                    | 'gemini'
                    | 'cohere'
                    | 'default';
                  handleModelTypeChange(newType);
                }}
              >
                <MenuItem value="openAI">OpenAI API</MenuItem>
                <MenuItem value="azureOpenAI">Azure OpenAI Service</MenuItem>
                <MenuItem value="gemini">Gemini</MenuItem>
                <MenuItem value="cohere">Cohere</MenuItem>
                <MenuItem value="sentenceTransformers">Sentence Transformers</MenuItem>
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
                      disabled={!isEditing || fetchError || isSaving}
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
                              disabled={!isEditing || fetchError || isSaving}
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
                      disabled={!isEditing || fetchError || isSaving}
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
                      disabled={!isEditing || fetchError || isSaving}
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
                              disabled={!isEditing || fetchError || isSaving}
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
                      helperText={
                        fieldState.error?.message || 'e.g., gemini-embedding-exp-03-07, etc'
                      }
                      required
                      disabled={!isEditing || fetchError || isSaving}
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

          {modelType === 'cohere' && (
            <>
              <Grid item xs={12} md={6}>
                <Controller
                  name="apiKey"
                  control={cohereControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="API Key"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'Your Cohere API Key'}
                      required
                      disabled={!isEditing || fetchError || isSaving}
                      type={showCoherePassword ? 'text' : 'password'}
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">
                            <Iconify icon={keyIcon} width={18} height={18} />
                          </InputAdornment>
                        ),
                        endAdornment: (
                          <InputAdornment position="end">
                            <IconButton
                              onClick={() => setShowCoherePassword(!showCoherePassword)}
                              edge="end"
                              size="small"
                              disabled={!isEditing || fetchError || isSaving}
                            >
                              <Iconify
                                icon={showCoherePassword ? eyeOffIcon : eyeIcon}
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
                  control={cohereControl}
                  render={({ field, fieldState }) => (
                    <TextField
                      {...field}
                      label="Model Name"
                      fullWidth
                      size="small"
                      error={!!fieldState.error}
                      helperText={fieldState.error?.message || 'e.g., embed-v4.0, etc'}
                      required
                      disabled={!isEditing || fetchError || isSaving}
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
                      disabled={!isEditing || fetchError || isSaving}
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
                      disabled={!isEditing || fetchError || isSaving}
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
                              disabled={!isEditing || fetchError || isSaving}
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
                      disabled={!isEditing || fetchError || isSaving}
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

          {/* Sentence Transformers Form */}
          {modelType === 'sentenceTransformers' && (
            <Grid item xs={12} md={12}>
              <Controller
                name="model"
                control={sentenceTransformersControl}
                render={({ field, fieldState }) => (
                  <TextField
                    {...field}
                    label="Model Name"
                    fullWidth
                    size="small"
                    error={!!fieldState.error}
                    helperText={fieldState.error?.message || 'e.g., all-MiniLM-L6-v2'}
                    required
                    disabled={!isEditing || fetchError || isSaving}
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

        {formSubmitSuccess && !saveError && (
          <Alert severity="success" sx={{ mt: 3 }}>
            Configuration saved successfully.
          </Alert>
        )}

        {/* Overlay loading indicator - replaces the old one */}
        {isSaving && (
          <Box sx={loadingOverlayStyle}>
            <CircularProgress size={32} />
            <Typography variant="body2" sx={{ mt: 2, fontWeight: 500 }}>
              Saving configuration...
            </Typography>
          </Box>
        )}
      </Box>
    );
  }
);

export default EmbeddingConfigForm;

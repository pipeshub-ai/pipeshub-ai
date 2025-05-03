import type { SelectChangeEvent } from '@mui/material';

import { z } from 'zod';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import React, { useState, useEffect } from 'react';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';

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
  FormHelperText,
  InputAdornment,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import type { EmbeddingFormValues } from './types';

// Zod schema for OpenAI embedding validation
const openaiEmbeddingSchema = z.object({
  modelType: z.literal('openai'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for Azure OpenAI embedding validation
const azureEmbeddingSchema = z.object({
  modelType: z.literal('azureOpenAI'),
  endpoint: z.string().min(1, 'Endpoint is required').url('Please enter a valid URL'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for Sentence Transformers embedding validation
const sentenceTransformersEmbeddingSchema = z.object({
  modelType: z.literal('sentenceTransformers'),
  model: z.string().min(1, 'Model is required'),
  // apiKey is not required for sentenceTransformers
});

// Zod schema for Default option - no validation needed
const defaultEmbeddingSchema = z.object({
  modelType: z.literal('default'),
  // No other fields required for default
});

// Combined schema using discriminated union
const embeddingSchema = z.discriminatedUnion('modelType', [
  openaiEmbeddingSchema,
  azureEmbeddingSchema,
  sentenceTransformersEmbeddingSchema,
  defaultEmbeddingSchema,
]);

interface EmbeddingConfigStepProps {
  onSubmit: (data: EmbeddingFormValues) => void;
  onSkip: () => void;
  initialValues: EmbeddingFormValues | null;
}

const EmbeddingConfigStep: React.FC<EmbeddingConfigStepProps> = ({
  onSubmit,
  onSkip,
  initialValues,
}) => {
  const [showPassword, setShowPassword] = useState<boolean>(false);
  const [modelType, setModelType] = useState<
    'openai' | 'azureOpenAI' | 'sentenceTransformers' | 'default'
  >(initialValues?.modelType || 'default');

  // Get default values based on modelType
  const getDefaultValues = () => {
    if (modelType === 'azureOpenAI') {
      return {
        modelType: 'azureOpenAI' as const,
        endpoint: initialValues?.endpoint || '',
        apiKey: initialValues?.apiKey || '',
        model: initialValues?.model || '',
      };
    }
    if (modelType === 'sentenceTransformers') {
      return {
        modelType: 'sentenceTransformers' as const,
        model: initialValues?.model || '', // Default model for sentence transformers
      };
    }
    if (modelType === 'default') {
      // Default option - no additional configuration needed
      return {
        modelType: 'default' as const,
      };
    }
    return {
      modelType: 'openai' as const,
      apiKey: initialValues?.apiKey || '',
      model: initialValues?.model || '',
    };
  };

  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isValid, isDirty },
    trigger,
    watch,
  } = useForm<EmbeddingFormValues>({
    resolver: zodResolver(embeddingSchema),
    mode: 'onChange', // Validate on change
    defaultValues: getDefaultValues(),
  });

  // Watch the current modelType for conditional rendering
  const currentModelType = watch('modelType');

  // Handle model type change
  const handleModelTypeChange = (
    newType: 'openai' | 'azureOpenAI' | 'sentenceTransformers' | 'default'
  ) => {
    setModelType(newType);

    if (newType === 'azureOpenAI') {
      reset({
        modelType: 'azureOpenAI',
        endpoint: '',
        apiKey: '',
        model: '',
      });
    } else if (newType === 'sentenceTransformers') {
      reset({
        modelType: 'sentenceTransformers',
        model: '', // Default model
      });
    } else if (newType === 'default') {
      // Just reset to default with no additional fields
      reset({
        modelType: 'default',
      });
    } else {
      reset({
        modelType: 'openai',
        apiKey: '',
        model: '',
      });
    }
  };

  // Initialize form with initial values if available
  useEffect(() => {
    if (initialValues) {
      setModelType(initialValues.modelType);
      reset(initialValues);
      // Validate initial values
      setTimeout(() => {
        trigger();
      }, 0);
    }
  }, [initialValues, reset, trigger]);

  // Expose methods for external validation and submission
  useEffect(() => {
    // Method to check if form has any input
    window.hasEmbeddingInput = () => {
      // If using default or sentenceTransformers, consider it as having input
      if (modelType === 'default' || modelType === 'sentenceTransformers') {
        return true;
      }

      const values = getDefaultValues();
      return Object.values(values).some(
        (val) => typeof val === 'string' && val.trim() !== '' && val !== values.model
      );
    };

    // Method to validate and submit the form programmatically
    window.submitEmbeddingForm = async () => {
      // If using default option, always consider it valid
      if (modelType === 'default') {
        const data = { modelType: 'default' };
        onSubmit(data as EmbeddingFormValues);
        return true;
      }

      // Otherwise trigger validation for all fields
      const isFormValid = await trigger();

      if (isFormValid) {
        // Use a simple trick to ensure the form submits directly
        const formSubmitHandler = handleSubmit((data) => {
          onSubmit(data);
          return true;
        });

        // Execute the submission handler directly
        formSubmitHandler();
        return true;
      }
      return false;
    };

    // Method to check if the form is valid
    window.isEmbeddingFormValid = async () => {
      // Default is always valid
      if (modelType === 'default') {
        return true;
      }

      return trigger();
    };

    return () => {
      // Clean up when component unmounts
      delete window.submitEmbeddingForm;
      delete window.isEmbeddingFormValid;
      delete window.hasEmbeddingInput;
    };
    // eslint-disable-next-line
  }, [handleSubmit, onSubmit, trigger, modelType]);

  // Direct form submission handler
  const onFormSubmit = (data: EmbeddingFormValues) => {
    onSubmit(data);
  };

  return (
    <Box
      component="form"
      id="embedding-config-form"
      onSubmit={handleSubmit(onFormSubmit)}
      noValidate
    >
      <Typography variant="subtitle1" gutterBottom>
        Embeddings Configuration
      </Typography>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Configure the embedding model for generating text embeddings in your application. Embeddings
        are used for semantic search and document retrieval.
      </Typography>

      <Alert severity="info" sx={{ mb: 3 }}>
        Select the embedding provider to use. You can use the default system embeddings or configure
        a specific provider. All fields marked with <span style={{ color: 'error.main' }}>*</span>{' '}
        are required for the selected provider.
      </Alert>

      <Grid container spacing={2}>
        <Grid item xs={12}>
          <Controller
            name="modelType"
            control={control}
            render={({ field, fieldState }) => (
              <FormControl fullWidth error={!!fieldState.error} size="small">
                <InputLabel>Provider *</InputLabel>
                <Select
                  {...field}
                  label="Provider *"
                  onChange={(e: SelectChangeEvent) => {
                    const newType = e.target.value as
                      | 'openai'
                      | 'azureOpenAI'
                      | 'sentenceTransformers'
                      | 'default';
                    field.onChange(newType);
                    handleModelTypeChange(newType);
                  }}
                >
                  <MenuItem value="default">Default</MenuItem>
                  <MenuItem value="sentenceTransformers">Sentence Transformer</MenuItem>
                  <MenuItem value="openai">OpenAI</MenuItem>
                  <MenuItem value="azureOpenAI">Azure OpenAI</MenuItem>
                </Select>
                {fieldState.error && <FormHelperText>{fieldState.error.message}</FormHelperText>}
              </FormControl>
            )}
          />
        </Grid>

        {/* Show message for default option */}
        {currentModelType === 'default' && (
          <Grid item xs={12}>
            <Alert severity="success" sx={{ mt: 1 }}>
              Using default system embeddings. No additional configuration required.
            </Alert>
          </Grid>
        )}

        {/* Fields for Sentence Transformers */}
        {currentModelType === 'sentenceTransformers' && (
          <Grid item xs={12}>
            <Controller
              name="model"
              control={control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label="Embedding Model"
                  fullWidth
                  size="small"
                  error={!!fieldState.error}
                  helperText={fieldState.error?.message || 'e.g: all-MiniLM-L6-v2'}
                  required
                  onBlur={() => {
                    field.onBlur();
                    trigger('model');
                  }}
                />
              )}
            />
          </Grid>
        )}

        {/* Azure OpenAI specific fields */}
        {currentModelType === 'azureOpenAI' && (
          <Grid item xs={12}>
            <Controller
              name="endpoint"
              control={control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label="Endpoint"
                  fullWidth
                  size="small"
                  error={!!fieldState.error}
                  helperText={
                    fieldState.error?.message ||
                    'e.g., https://your-resource-name.openai.azure.com/'
                  }
                  required
                  onBlur={() => {
                    field.onBlur();
                    trigger('endpoint');
                  }}
                />
              )}
            />
          </Grid>
        )}

        {/* API Key field - only show for OpenAI and Azure OpenAI */}
        {(currentModelType === 'openai' || currentModelType === 'azureOpenAI') && (
          <Grid item xs={12}>
            <Controller
              name="apiKey"
              control={control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label="API Key"
                  fullWidth
                  size="small"
                  error={!!fieldState.error}
                  helperText={fieldState.error?.message}
                  type={showPassword ? 'text' : 'password'}
                  required
                  onBlur={() => {
                    field.onBlur();
                    trigger('apiKey');
                  }}
                  InputProps={{
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          onClick={() => setShowPassword(!showPassword)}
                          edge="end"
                          size="small"
                        >
                          <Iconify
                            icon={showPassword ? eyeOffIcon : eyeIcon}
                            width={16}
                            height={16}
                          />
                        </IconButton>
                      </InputAdornment>
                    ),
                  }}
                />
              )}
            />
          </Grid>
        )}

        {/* Model field - only show for OpenAI and Azure OpenAI */}
        {(currentModelType === 'openai' || currentModelType === 'azureOpenAI') && (
          <Grid item xs={12}>
            <Controller
              name="model"
              control={control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label="Embedding Model"
                  fullWidth
                  size="small"
                  error={!!fieldState.error}
                  helperText={
                    fieldState.error?.message ||
                    'e.g., text-embedding-3-small, text-embedding-3-large'
                  }
                  required
                  onBlur={() => {
                    field.onBlur();
                    trigger('model');
                  }}
                />
              )}
            />
          </Grid>
        )}
      </Grid>

      {/* This hidden submit button ensures the form can be submitted programmatically */}
      <Button type="submit" style={{ display: 'none' }} id="embedding-form-submit-button">
        Submit
      </Button>
    </Box>
  );
};

// Declare the window method types for external validation calls
declare global {
  interface Window {
    submitEmbeddingForm?: () => Promise<boolean>;
    isEmbeddingFormValid?: () => Promise<boolean>;
    hasEmbeddingInput?: () => boolean;
  }
}

export default EmbeddingConfigStep;

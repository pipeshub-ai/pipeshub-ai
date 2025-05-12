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
  FormHelperText,
  InputAdornment,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import type {
  LlmFormValues,
  AzureLlmFormValues,
  OpenAILlmFormValues,
  GeminiLlmFormValues,
  AnthropicLlmFormValues,
  OpenAICompatibleLlmFormValues,
} from './types';

// Zod schema for OpenAI validation with more descriptive error messages
const openaiSchema = z.object({
  modelType: z.literal('openai'),
  // clientId: z.string().min(1, 'Client ID is required'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

const geminiSchema = z.object({
  modelType: z.literal('gemini'),
  // clientId: z.string().min(1, 'Client ID is required'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

const anthropicSchema = z.object({
  modelType: z.literal('anthropic'),
  // clientId: z.string().min(1, 'Client ID is required'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for Azure OpenAI validation with more descriptive error messages
const azureSchema = z.object({
  modelType: z.literal('azure'),
  endpoint: z.string().min(1, 'Endpoint is required').url('Please enter a valid URL'),
  apiKey: z.string().min(1, 'API Key is required'),
  deploymentName: z.string().min(1, 'Deployment Name is required'),
  model: z.string().min(1, 'Model is required'),
});

// Zod schema for OpenAI API Compatible validation with more descriptive error messages
const openAICompatibleSchema = z.object({
  modelType: z.literal('openAICompatible'),
  endpoint: z.string().min(1, 'Endpoint is required').url('Please enter a valid URL'),
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

interface LlmConfigStepProps {
  onSubmit: (data: LlmFormValues) => void;
  onSkip: () => void;
  initialValues: LlmFormValues | null;
}

const LlmConfigStep: React.FC<LlmConfigStepProps> = ({ onSubmit, onSkip, initialValues }) => {
  const [showPassword, setShowPassword] = useState<boolean>(false);
  const [modelType, setModelType] = useState<
    'openai' | 'azure' | 'gemini' | 'anthropic' | 'openAICompatible'
  >(initialValues?.modelType || 'openai');

  // Get default values based on modelType
  const getDefaultValues = () => {
    if (modelType === 'azure') {
      return {
        modelType: 'azure' as const,
        endpoint: (initialValues as AzureLlmFormValues)?.endpoint || '',
        apiKey: initialValues?.apiKey || '',
        deploymentName: (initialValues as AzureLlmFormValues)?.deploymentName || '',
        model: initialValues?.model || '',
      } as AzureLlmFormValues;
    }
    if (modelType === 'openAICompatible') {
      return {
        modelType: 'openAICompatible' as const,
        endpoint: (initialValues as OpenAICompatibleLlmFormValues)?.endpoint || '',
        apiKey: initialValues?.apiKey || '',
        model: initialValues?.model || '',
      } as OpenAICompatibleLlmFormValues;
    }
    if (modelType === 'gemini') {
      return {
        modelType: 'gemini' as const,
        // clientId: (initialValues as OpenAILlmFormValues)?.clientId || '',
        apiKey: initialValues?.apiKey || '',
        model: initialValues?.model || '',
      } as GeminiLlmFormValues;
    }
    if (modelType === 'anthropic') {
      return {
        modelType: 'anthropic' as const,
        // clientId: (initialValues as OpenAILlmFormValues)?.clientId || '',
        apiKey: initialValues?.apiKey || '',
        model: initialValues?.model || '',
      } as AnthropicLlmFormValues;
    }
    return {
      modelType: 'openai' as const,
      // clientId: (initialValues as OpenAILlmFormValues)?.clientId || '',
      apiKey: initialValues?.apiKey || '',
      model: initialValues?.model || '',
    } as OpenAILlmFormValues;
  };

  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isValid, isDirty, touchedFields },
    trigger,
  } = useForm<LlmFormValues>({
    resolver: zodResolver(llmSchema),
    mode: 'onChange', // Validate on change
    defaultValues: getDefaultValues(),
  });

  // Handle model type change
  const handleModelTypeChange = (
    newType: 'openai' | 'azure' | 'gemini' | 'anthropic' | 'openAICompatible'
  ) => {
    setModelType(newType);
    reset(
      newType === 'azure'
        ? ({
            modelType: 'azure',
            endpoint: '',
            apiKey: '',
            deploymentName: '',
            model: '',
          } as AzureLlmFormValues)
        : newType === 'gemini'
          ? ({
              modelType: 'gemini',
              // clientId: '',
              apiKey: '',
              model: '',
            } as GeminiLlmFormValues)
          : newType === 'anthropic'
            ? ({
                modelType: 'anthropic',
                // clientId: '',
                apiKey: '',
                model: '',
              } as AnthropicLlmFormValues)
            : newType === 'openAICompatible'
              ? ({
                  modelType: 'openAICompatible',
                  endpoint: '',
                  apiKey: '',
                  model: '',
                } as OpenAICompatibleLlmFormValues)
              : ({
                  modelType: 'openai',
                  // clientId: '',
                  apiKey: '',
                  model: '',
                } as OpenAILlmFormValues)
    );
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

  // Expose submit method to parent component with improved validation
  useEffect(() => {
    (window as any).submitLlmForm = async () => {
      // Trigger validation for all fields
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

    return () => {
      delete (window as any).submitLlmForm;
    };
  }, [handleSubmit, onSubmit, trigger]);

  // Direct form submission handler
  const onFormSubmit = (data: LlmFormValues) => {
    onSubmit(data);
  };

  return (
    <Box component="form" id="llm-config-form" onSubmit={handleSubmit(onFormSubmit)} noValidate>
      <Typography variant="subtitle1" gutterBottom>
        Large Language Model
      </Typography>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Configure OpenAI , Azure OpenAI or Gemini to enable AI features.
      </Typography>

      <Alert severity="info" sx={{ mb: 3 }}>
        LLM configuration is required to proceed with setup. All fields marked with{' '}
        <span style={{ color: 'error.main' }}>*</span> are required.
      </Alert>
      <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
        Refer to{' '}
        <Link href="https://docs.pipeshub.com/ai-models/overview" target="_blank" rel="noopener">
          the documentation
        </Link>{' '}
        for more information.
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
                      | 'azure'
                      | 'gemini'
                      | 'anthropic'
                      | 'openAICompatible';
                    field.onChange(newType);
                    handleModelTypeChange(newType);
                  }}
                >
                  <MenuItem value="openai">OpenAI</MenuItem>
                  <MenuItem value="azure">Azure OpenAI</MenuItem>
                  <MenuItem value="gemini">Gemini</MenuItem>
                  <MenuItem value="anthropic">Anthropic</MenuItem>
                  <MenuItem value="openAICompatible">OpenAI API Compatible</MenuItem>
                </Select>
                {fieldState.error && <FormHelperText>{fieldState.error.message}</FormHelperText>}
              </FormControl>
            )}
          />
        </Grid>

        {/* OpenAI specific fields */}
        {/* {modelType === 'openai' && (
          <Grid item xs={12}>
            <Controller
              name="clientId"
              control={control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label='Client ID'
                  fullWidth
                  size="small"
                  error={!!fieldState.error}
                  helperText={fieldState.error?.message}
                  required
                  onBlur={() => {
                    field.onBlur();
                    trigger('clientId');
                  }}
                />
              )}
            />
          </Grid>
        )} */}

        {/* Azure OpenAI specific fields */}
        {modelType === 'azure' && (
          <>
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
            <Grid item xs={12}>
              <Controller
                name="deploymentName"
                control={control}
                render={({ field, fieldState }) => (
                  <TextField
                    {...field}
                    label="Deployment Name"
                    fullWidth
                    size="small"
                    error={!!fieldState.error}
                    helperText={fieldState.error?.message}
                    required
                    onBlur={() => {
                      field.onBlur();
                      trigger('deploymentName');
                    }}
                  />
                )}
              />
            </Grid>
          </>
        )}

        {/* OpenAi API compatible fields */}
        {modelType === 'openAICompatible' && (
          <>
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
                      'e.g., https://api.together.xyz/v1/'
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
          </>
        )}

        {/* Common fields for both providers */}
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

        <Grid item xs={12}>
          <Controller
            name="model"
            control={control}
            render={({ field, fieldState }) => (
              <TextField
                {...field}
                label="Model Name"
                fullWidth
                size="small"
                error={!!fieldState.error}
                helperText={
                  fieldState.error?.message ||
                  (modelType === 'openai'
                    ? 'e.g., gpt-4-turbo'
                    : modelType === 'gemini'
                      ? 'e.g., gemini-2.0-flash'
                      : modelType === 'anthropic'
                        ? 'e.g.,  claude-3-7-sonnet-20250219'
                        : modelType === 'openAICompatible'
                          ? 'e.g., deepseek-ai/DeepSeek-V3'
                          : 'e.g., gpt-4o')
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
      </Grid>

      {/* This hidden submit button ensures the form can be submitted programmatically */}
      <Button type="submit" style={{ display: 'none' }} id="llm-form-submit-button">
        Submit
      </Button>
    </Box>
  );
};

export default LlmConfigStep;

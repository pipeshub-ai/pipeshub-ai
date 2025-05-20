// providers/constants.ts
// Provider configurations without imports (breaking the cycle)

import { z } from 'zod';
import linkIcon from '@iconify-icons/mdi/link';
import cubeIcon from '@iconify-icons/mdi/cube-outline';
import { ProviderConfig, ProviderType } from './types';

// OpenAI Provider
export const openaiSchema = z.object({
  modelType: z.literal('openAI'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const openAIProvider: ProviderConfig = {
  id: 'openAI',
  label: 'OpenAI API',
  schema: openaiSchema,
  defaultValues: {
    modelType: 'openAI',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., gpt-4o, gpt-4-turbo, gpt-3.5-turbo',
  description: 'Enter your OpenAI API credentials to get started.',
  additionalFields: []
};

// Azure OpenAI Provider
export const azureSchema = z.object({
  modelType: z.literal('azureOpenAI'),
  endpoint: z
    .string()
    .min(1, 'Endpoint is required')
    .startsWith('https://', 'Endpoint must start with https://'),
  apiKey: z.string().min(1, 'API Key is required'),
  deploymentName: z.string().min(1, 'Deployment Name is required'),
  model: z.string().min(1, 'Model is required'),
});

export const azureOpenAIProvider: ProviderConfig = {
  id: 'azureOpenAI',
  label: 'Azure OpenAI Service',
  schema: azureSchema,
  defaultValues: {
    modelType: 'azureOpenAI',
    endpoint: '',
    apiKey: '',
    deploymentName: '',
    model: '',
  },
  modelPlaceholder: 'e.g., gpt-4, gpt-35-turbo',
  description: 'You need an active Azure subscription with Azure OpenAI Service enabled.',
  additionalFields: [
    {
      name: 'endpoint',
      label: 'Endpoint URL',
      placeholder: 'e.g., https://your-resource.openai.azure.com/',
      icon: linkIcon,
    },
    {
      name: 'deploymentName',
      label: 'Deployment Name',
      placeholder: 'Your Azure OpenAI deployment name',
      icon: cubeIcon,
    }
  ]
};

// Gemini Provider
export const geminiSchema = z.object({
  modelType: z.literal('gemini'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const geminiProvider: ProviderConfig = {
  id: 'gemini',
  label: 'Gemini API',
  schema: geminiSchema,
  defaultValues: {
    modelType: 'gemini',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., gemini-2.0-flash',
  description: 'Enter your Gemini API credentials to get started.',
  additionalFields: []
};

// Anthropic Provider
export const anthropicSchema = z.object({
  modelType: z.literal('anthropic'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const anthropicProvider: ProviderConfig = {
  id: 'anthropic',
  label: 'Anthropic API',
  schema: anthropicSchema,
  defaultValues: {
    modelType: 'anthropic',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., claude-3-7-sonnet-20250219',
  description: 'Enter your Anthropic API credentials to get started.',
  additionalFields: []
};

// OpenAI API Compatible Provider
export const openAICompatibleSchema = z.object({
  modelType: z.literal('openAICompatible'),
  endpoint: z
    .string()
    .min(1, 'Endpoint is required')
    .startsWith('http', 'Endpoint must start with http:// or https://'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const openAICompatibleProvider: ProviderConfig = {
  id: 'openAICompatible',
  label: 'OpenAI API Compatible',
  schema: openAICompatibleSchema,
  defaultValues: {
    modelType: 'openAICompatible',
    endpoint: '',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., deepseek-ai/DeepSeek-V3',
  description: 'Enter your OpenAI-compatible API credentials to get started.',
  additionalFields: [
    {
      name: 'endpoint',
      label: 'Endpoint URL',
      placeholder: 'e.g., https://api.together.xyz/v1/',
      icon: linkIcon,
    }
  ]
};


// All providers in a simple array
export const providers = [
  openAIProvider,
  azureOpenAIProvider,
  geminiProvider,
  anthropicProvider,
  openAICompatibleProvider
];

// Helper function to get provider by ID
export const getProviderById = (id: ProviderType): ProviderConfig | undefined =>
  providers.find(provider => provider.id === id);
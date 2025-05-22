// providers/types.ts 

import { z } from 'zod';
import type { IconifyIcon } from '@iconify/react';

// Provider type definitions
export type ProviderType = 'openAI' | 'azureOpenAI' | 'gemini' | 'anthropic' | 'openAICompatible';

// Base form values interface with optional _provider tracking field
export interface BaseLlmFormValues {
  modelType: ProviderType;
  apiKey: string;
  model: string;
  _provider?: ProviderType; 
}

// Provider-specific interfaces
export interface OpenAILlmFormValues extends BaseLlmFormValues {
  modelType: 'openAI';
  _provider?: 'openAI';
}

export interface GeminiLlmFormValues extends BaseLlmFormValues {
  modelType: 'gemini';
  _provider?: 'gemini';
}

export interface AnthropicLlmFormValues extends BaseLlmFormValues {
  modelType: 'anthropic';
  _provider?: 'anthropic';
}

export interface AzureLlmFormValues extends BaseLlmFormValues {
  modelType: 'azureOpenAI';
  endpoint: string;
  deploymentName: string;
  _provider?: 'azureOpenAI';
}

export interface OpenAICompatibleLlmFormValues extends BaseLlmFormValues {
  modelType: 'openAICompatible';
  endpoint: string;
  _provider?: 'openAICompatible';
}

// Combined type for all possible form values
export type LlmFormValues =
  | OpenAILlmFormValues
  | GeminiLlmFormValues
  | AnthropicLlmFormValues
  | AzureLlmFormValues
  | OpenAICompatibleLlmFormValues;

// Provider registry interface
export interface ProviderConfig {
  id: ProviderType;
  label: string;
  schema: z.ZodType<any>;
  defaultValues: any;
  modelPlaceholder: string;
  description?: string;
  additionalFields?: {
    name: string;
    label: string;
    placeholder: string;
    icon: string | IconifyIcon;
    type?: 'text' | 'password';
  }[];
}

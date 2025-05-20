// providers/openai-compatible.ts

import { z } from 'zod';
import linkIcon from '@iconify-icons/mdi/link';
import { ProviderConfig } from './types';

// Zod schema for OpenAI API Compatible validation
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
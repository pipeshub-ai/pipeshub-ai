// providers/azure-openai.ts

import { z } from 'zod';
import keyIcon from '@iconify-icons/mdi/key';
import linkIcon from '@iconify-icons/mdi/link';
import robotIcon from '@iconify-icons/mdi/robot';
import { EmbeddingProviderConfig } from './types';

// Zod schema for Azure OpenAI validation
export const azureEmbeddingSchema = z.object({
  modelType: z.literal('azureOpenAI'),
  endpoint: z
    .string()
    .min(1, 'Endpoint is required')
    .startsWith('https://', 'Endpoint must start with https://'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const azureOpenAIEmbeddingProvider: EmbeddingProviderConfig = {
  id: 'azureOpenAI',
  label: 'Azure OpenAI Service',
  schema: azureEmbeddingSchema,
  defaultValues: {
    modelType: 'azureOpenAI',
    endpoint: '',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., text-embedding-3-small, text-embedding-3-large',
  description: 'You need an active Azure subscription with Azure OpenAI Service enabled.',
  additionalFields: [
    {
      name: 'endpoint',
      label: 'Endpoint URL',
      placeholder: 'e.g., https://your-resource.openai.azure.com/',
      icon: linkIcon,
    }
  ]
};
// providers/azure-openai.ts 

import { z } from 'zod';
import linkIcon from '@iconify-icons/mdi/link';
import cubeIcon from '@iconify-icons/mdi/cube-outline';
import { ProviderConfig } from './types';

// Zod schema for Azure OpenAI validation
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
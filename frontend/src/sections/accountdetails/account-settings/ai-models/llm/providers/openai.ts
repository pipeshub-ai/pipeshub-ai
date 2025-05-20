// providers/openai.ts (Fixed circular dependency)

import { z } from 'zod';
import keyIcon from '@iconify-icons/mdi/key';
import robotIcon from '@iconify-icons/mdi/robot';
import { ProviderConfig } from './types';

// Zod schema for OpenAI validation
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
// providers/openai.ts

import { z } from 'zod';
import keyIcon from '@iconify-icons/mdi/key';
import robotIcon from '@iconify-icons/mdi/robot';
import { EmbeddingProviderConfig } from './types';

// Zod schema for OpenAI validation
export const openaiEmbeddingSchema = z.object({
  modelType: z.literal('openAI'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const openAIEmbeddingProvider: EmbeddingProviderConfig = {
  id: 'openAI',
  label: 'OpenAI API',
  schema: openaiEmbeddingSchema,
  defaultValues: {
    modelType: 'openAI',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., text-embedding-3-small, text-embedding-3-large',
  description: 'Enter your OpenAI API credentials to get started.',
  additionalFields: []
};
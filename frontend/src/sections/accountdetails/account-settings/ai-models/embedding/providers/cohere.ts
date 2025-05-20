// providers/cohere.ts

import { z } from 'zod';
import { EmbeddingProviderConfig } from './types';

// Zod schema for Cohere validation
export const cohereEmbeddingSchema = z.object({
  modelType: z.literal('cohere'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const cohereEmbeddingProvider: EmbeddingProviderConfig = {
  id: 'cohere',
  label: 'Cohere',
  schema: cohereEmbeddingSchema,
  defaultValues: {
    modelType: 'cohere',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., embed-v4.0',
  description: 'Enter your Cohere API credentials to get started.',
  additionalFields: []
};
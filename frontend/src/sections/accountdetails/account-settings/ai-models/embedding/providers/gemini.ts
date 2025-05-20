// providers/gemini.ts

import { z } from 'zod';
import { EmbeddingProviderConfig } from './types';

// Zod schema for Gemini validation
export const geminiEmbeddingSchema = z.object({
  modelType: z.literal('gemini'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const geminiEmbeddingProvider: EmbeddingProviderConfig = {
  id: 'gemini',
  label: 'Gemini API',
  schema: geminiEmbeddingSchema,
  defaultValues: {
    modelType: 'gemini',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., gemini-embedding-exp-03-07',
  description: 'Enter your Gemini API credentials to get started.',
  additionalFields: []
};
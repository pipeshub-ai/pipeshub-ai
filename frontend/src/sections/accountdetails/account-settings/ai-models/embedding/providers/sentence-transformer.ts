// providers/sentence-transformer.ts

import { z } from 'zod';
import robotIcon from '@iconify-icons/mdi/robot';
import { EmbeddingProviderConfig } from './types';

// Zod schema for Sentence Transformers validation
export const sentenceTransformersEmbeddingSchema = z.object({
  modelType: z.literal('sentenceTransformers'),
  model: z.string().min(1, 'Model is required'),
  apiKey: z.string().optional(),
});

export const sentenceTransformersEmbeddingProvider: EmbeddingProviderConfig = {
  id: 'sentenceTransformers',
  label: 'Sentence Transformers',
  schema: sentenceTransformersEmbeddingSchema,
  defaultValues: {
    modelType: 'sentenceTransformers',
    model: '',
    apiKey: '',
  },
  modelPlaceholder: 'e.g., all-MiniLM-L6-v2',
  description: 'Use Sentence Transformers for local embedding generation.',
  additionalFields: []
};
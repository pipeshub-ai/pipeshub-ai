// providers/default.ts

import { z } from 'zod';
import { EmbeddingProviderConfig } from './types';

// Zod schema for Default option (no validation needed)
export const defaultEmbeddingSchema = z.object({
  modelType: z.literal('default'),
});

export const defaultEmbeddingProvider: EmbeddingProviderConfig = {
  id: 'default',
  label: 'Default (System Provided)',
  schema: defaultEmbeddingSchema,
  defaultValues: {
    modelType: 'default',
  },
  description: 'Using the default embedding model provided by the system.',
  additionalFields: []
};
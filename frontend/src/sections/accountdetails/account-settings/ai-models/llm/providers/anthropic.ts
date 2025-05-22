// providers/anthropic.ts

import { z } from 'zod';
import { ProviderConfig } from './types';

// Zod schema for Anthropic validation
export const anthropicSchema = z.object({
  modelType: z.literal('anthropic'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const anthropicProvider: ProviderConfig = {
  id: 'anthropic',
  label: 'Anthropic API',
  schema: anthropicSchema,
  defaultValues: {
    modelType: 'anthropic',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., claude-3-7-sonnet-20250219',
  description: 'Enter your Anthropic API credentials to get started.',
  additionalFields: []
};
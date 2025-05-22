
// providers/gemini.ts

import { z } from 'zod';
import { ProviderConfig } from './types';

// Zod schema for Gemini validation
export const geminiSchema = z.object({
  modelType: z.literal('gemini'),
  apiKey: z.string().min(1, 'API Key is required'),
  model: z.string().min(1, 'Model is required'),
});

export const geminiProvider: ProviderConfig = {
  id: 'gemini',
  label: 'Gemini API',
  schema: geminiSchema,
  defaultValues: {
    modelType: 'gemini',
    apiKey: '',
    model: '',
  },
  modelPlaceholder: 'e.g., gemini-2.0-flash',
  description: 'Enter your Gemini API credentials to get started.',
  additionalFields: []
};
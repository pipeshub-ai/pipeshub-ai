// providers/types.ts

import { z } from 'zod';
import { IconifyIcon } from '@iconify/react';

// Base provider interface
export interface BaseEmbeddingProviderConfig {
  id: EmbeddingProviderType;
  label: string;
  schema: z.ZodType<any>;
  defaultValues: any;
  description: string;
  additionalFields?: EmbeddingFieldConfig[];
  modelPlaceholder?: string;
}

// Field configuration interface
export interface EmbeddingFieldConfig {
  name: string;
  label: string;
  placeholder?: string;
  type?: 'text' | 'password';
  icon?: string | IconifyIcon;
  required?: boolean;
}

// Provider types
export type EmbeddingProviderType = 
  | 'openAI' 
  | 'azureOpenAI' 
  | 'sentenceTransformers' 
  | 'gemini' 
  | 'cohere' 
  | 'default';

// Base form values interface
export interface BaseEmbeddingFormValues {
  modelType: EmbeddingProviderType;
  _provider?: EmbeddingProviderType;
}

// Provider-specific form values interfaces
export interface OpenAIEmbeddingFormValues extends BaseEmbeddingFormValues {
  modelType: 'openAI';
  apiKey: string;
  model: string;
}

export interface AzureOpenAIEmbeddingFormValues extends BaseEmbeddingFormValues {
  modelType: 'azureOpenAI';
  endpoint: string;
  apiKey: string;
  model: string;
}

export interface SentenceTransformersEmbeddingFormValues extends BaseEmbeddingFormValues {
  modelType: 'sentenceTransformers';
  model: string;
  apiKey?: string;
}

export interface GeminiEmbeddingFormValues extends BaseEmbeddingFormValues {
  modelType: 'gemini';
  apiKey: string;
  model: string;
}

export interface CohereEmbeddingFormValues extends BaseEmbeddingFormValues {
  modelType: 'cohere';
  apiKey: string;
  model: string;
}

export interface DefaultEmbeddingFormValues extends BaseEmbeddingFormValues {
  modelType: 'default';
}

// Union type for all provider form values
export type EmbeddingFormValues =
  | OpenAIEmbeddingFormValues
  | AzureOpenAIEmbeddingFormValues
  | SentenceTransformersEmbeddingFormValues
  | GeminiEmbeddingFormValues
  | CohereEmbeddingFormValues
  | DefaultEmbeddingFormValues;

// Provider config interface
export interface EmbeddingProviderConfig extends BaseEmbeddingProviderConfig {
  id: EmbeddingProviderType;
  schema: z.ZodType<any>;
  defaultValues: EmbeddingFormValues;
}
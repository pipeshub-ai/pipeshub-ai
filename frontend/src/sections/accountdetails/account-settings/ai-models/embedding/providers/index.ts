// providers/index.ts

import { EmbeddingProviderConfig, EmbeddingProviderType } from './types';
import { openAIEmbeddingProvider } from './openai';
import { azureOpenAIEmbeddingProvider } from './azure-openai';
import { sentenceTransformersEmbeddingProvider } from './sentence-transformer';
import { geminiEmbeddingProvider } from './gemini';
import { cohereEmbeddingProvider } from './cohere';
import { defaultEmbeddingProvider } from './default';

// Register all embedding providers in this array
export const embeddingProviders: EmbeddingProviderConfig[] = [
  openAIEmbeddingProvider,
  azureOpenAIEmbeddingProvider,
  geminiEmbeddingProvider,
  cohereEmbeddingProvider,
  sentenceTransformersEmbeddingProvider,
  defaultEmbeddingProvider,
];

// Helper function to get a provider by ID
export const getEmbeddingProviderById = (
  id: EmbeddingProviderType
): EmbeddingProviderConfig | undefined => embeddingProviders.find((provider) => provider.id === id);

export * from './types';

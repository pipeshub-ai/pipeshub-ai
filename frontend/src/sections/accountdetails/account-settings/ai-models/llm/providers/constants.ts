// providers/constants.ts

import { ProviderConfig, ProviderType } from './types';
import { openAIProvider } from './openai';
import { azureOpenAIProvider } from './azure-openai';
import { geminiProvider } from './gemini';
import { anthropicProvider } from './anthropic';
import { openAICompatibleProvider } from './openai-compatible';

// All providers in a simple array
export const providers = [
  openAIProvider,
  azureOpenAIProvider,
  geminiProvider,
  anthropicProvider,
  openAICompatibleProvider
];

// Helper function to get provider by ID
export const getProviderById = (id: ProviderType): ProviderConfig | undefined =>
  providers.find(provider => provider.id === id);
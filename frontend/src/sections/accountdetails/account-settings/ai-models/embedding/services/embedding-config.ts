// services/embedding-config.ts

import axios from 'src/utils/axios';
import { EmbeddingFormValues, EmbeddingProviderType } from '../providers';

/**
 * Fetch the current embedding configuration from the API
 * @returns The embedding configuration or null if not found
 */
export const getEmbeddingConfig = async (): Promise<EmbeddingFormValues | null> => {
  try {
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const { data } = response;

    // Check if Embedding configuration exists
    if (data.embedding && data.embedding.length > 0) {
      const embeddingConfig = data.embedding[0];
      const config = embeddingConfig.configuration;

      // Map provider to modelType
      const modelTypeMap: Record<string, EmbeddingProviderType> = {
        'openAI': 'openAI',
        'azureOpenAI': 'azureOpenAI',
        'sentenceTransformers': 'sentenceTransformers',
        'gemini': 'gemini',
        'cohere': 'cohere'
      };

      // Get the modelType based on the provider or default to openAI
      const modelType = modelTypeMap[embeddingConfig.provider] || 'openAI';

      // Return the configuration with the correct modelType
      return {
        ...config,
        modelType,
      } as EmbeddingFormValues;
    }

    // If no embedding configuration is found, return default type
    return {
      modelType: 'default',
    } as EmbeddingFormValues;
  } catch (error) {
    console.error('Error fetching Embedding configuration:', error);
    throw error;
  }
};

/**
 * Update the embedding configuration through the API
 * @param config The new embedding configuration
 * @returns The API response
 */
export const updateEmbeddingConfig = async (
  config: EmbeddingFormValues
): Promise<any> => {
  try {
    // First get the current configuration
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const currentConfig = response.data;

    // If using default, send an empty array for embeddings
    if (config.modelType === 'default') {
      const updatedConfig = {
        ...currentConfig,
        embedding: [], // Empty array for default
      };

      // Update the configuration
      const updateResponse = await axios.post(
        '/api/v1/configurationManager/aiModelsConfig',
        updatedConfig
      );
      return updateResponse;
    }

    // For other providers, prepare the configuration
    // Remove modelType and _provider from the configuration before sending
    const { modelType, _provider, ...cleanConfig } = config;

    // Map modelType to provider name for the API
    const providerMap: Record<EmbeddingProviderType, string> = {
      'openAI': 'openAI',
      'azureOpenAI': 'azureOpenAI',
      'sentenceTransformers': 'sentenceTransformers',
      'gemini': 'gemini',
      'cohere': 'cohere',
      'default': 'default'
    };

    // Get the provider name based on the modelType
    const provider = providerMap[modelType];

    // Create the updated config object
    const updatedConfig = {
      ...currentConfig,
      embedding: [
        {
          provider,
          configuration: cleanConfig,
        },
      ],
    };

    // Update the configuration
    const updateResponse = await axios.post(
      '/api/v1/configurationManager/aiModelsConfig',
      updatedConfig
    );
    return updateResponse;
  } catch (error) {
    console.error('Error updating Embedding configuration:', error);
    throw error;
  }
};
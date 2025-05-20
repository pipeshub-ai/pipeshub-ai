// services/llm-config.ts 

import axios from 'src/utils/axios';
import { LlmFormValues, ProviderType } from '../providers/types';

let pendingRequest: Promise<LlmFormValues | null> | null = null;

const requestThrottleMs = 2000; 
let lastRequestTime = 0;
let cachedResult: LlmFormValues | null = null;
let cacheTime = 0;
const cacheExpirationMs = 30000; 

/**
 * Gets the LLM configuration from the API
 * Uses request deduplication, rate limiting, and caching to prevent continuous API calls
 */
export const getLlmConfig = async (): Promise<LlmFormValues | null> => {
  const now = Date.now();
  
  if (cachedResult && (now - cacheTime < cacheExpirationMs)) {
    return Promise.resolve(cachedResult);
  }
  
  if (pendingRequest) {
    return pendingRequest;
  }
  
  if (now - lastRequestTime < requestThrottleMs) {
    return Promise.resolve(cachedResult);
  }
  
  lastRequestTime = now;
  
  const fetchRequest = async (): Promise<LlmFormValues | null> => {
    try {
      const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
      const { data } = response;

      if (data.llm && data.llm.length > 0) {
        const llmConfig = data.llm[0];
        const config = llmConfig.configuration;

        const modelType = llmConfig.provider as ProviderType;
        
        const fullConfig: LlmFormValues = {
          ...config,
          modelType,
        };
        
        cachedResult = fullConfig;
        cacheTime = Date.now();
        
        return fullConfig;
      }
      
      cachedResult = null;
      cacheTime = Date.now();
      
      return null;
    } catch (error) {
      console.error('Error fetching LLM configuration:', error);
      throw error;
    } finally {
      pendingRequest = null;
    }
  };

  pendingRequest = fetchRequest();
  return pendingRequest;
};

/**
 * Invalidates the cache and forces a refresh on next request
 */
export const invalidateLlmConfigCache = (): void => {
  cachedResult = null;
  cacheTime = 0;
  pendingRequest = null;
};

/**
 * Updates the LLM configuration
 * Optimized to prevent unnecessary API calls
 */
export const updateLlmConfig = async (config: LlmFormValues): Promise<any> => {
  try {
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const currentConfig = response.data;

    const { modelType, _provider, ...cleanConfig } = config;

    const provider = modelType;

    const updatedConfig = {
      ...currentConfig,
      llm: [
        {
          provider,
          configuration: cleanConfig,
        },
      ],
    };

    const currentLlmConfig = currentConfig.llm?.[0]?.configuration || {};
    if (JSON.stringify(currentLlmConfig) === JSON.stringify(cleanConfig)) {
      console.log('Configuration unchanged, skipping update');
      return { data: { success: true, message: 'No changes detected' } };
    }

    const updateResponse = await axios.post(
      '/api/v1/configurationManager/aiModelsConfig',
      updatedConfig
    );
    
    invalidateLlmConfigCache();
    
    return updateResponse;
  } catch (error) {
    console.error('Error updating LLM configuration:', error);
    throw error;
  }
};
// services/llm-config.ts (Fixed TypeScript errors and continuous API calls)

import axios from 'src/utils/axios';
import { LlmFormValues, ProviderType } from '../providers/types';

// Track API request status with proper typing
let pendingRequest: Promise<LlmFormValues | null> | null = null;

// Add rate limiting and caching to prevent continuous API calls
const requestThrottleMs = 2000; // Don't allow more than one request every 2 seconds
let lastRequestTime = 0;
let cachedResult: LlmFormValues | null = null;
let cacheTime = 0;
const cacheExpirationMs = 30000; // Cache expires after 30 seconds

/**
 * Gets the LLM configuration from the API
 * Uses request deduplication, rate limiting, and caching to prevent continuous API calls
 */
export const getLlmConfig = async (): Promise<LlmFormValues | null> => {
  const now = Date.now();
  
  // Use cache if it exists and hasn't expired
  if (cachedResult && (now - cacheTime < cacheExpirationMs)) {
    return Promise.resolve(cachedResult);
  }
  
  // Return the pending request if there is one
  if (pendingRequest) {
    return pendingRequest;
  }
  
  // If the last request was too recent, throttle
  if (now - lastRequestTime < requestThrottleMs) {
    // Return cached result or null if none
    return Promise.resolve(cachedResult);
  }
  
  // Update request time
  lastRequestTime = now;
  
  // Create a new request
  const fetchRequest = async (): Promise<LlmFormValues | null> => {
    try {
      const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
      const { data } = response;

      // Check if LLM configuration exists
      if (data.llm && data.llm.length > 0) {
        const llmConfig = data.llm[0];
        const config = llmConfig.configuration;

        // Set the modelType based on the provider
        const modelType = llmConfig.provider as ProviderType;
        
        // Create the full configuration with the correct modelType
        const fullConfig: LlmFormValues = {
          ...config,
          modelType,
        };
        
        // Update cache
        cachedResult = fullConfig;
        cacheTime = Date.now();
        
        return fullConfig;
      }
      
      // Update cache with null result
      cachedResult = null;
      cacheTime = Date.now();
      
      return null;
    } catch (error) {
      console.error('Error fetching LLM configuration:', error);
      throw error;
    } finally {
      // Clear the pending request
      pendingRequest = null;
    }
  };

  // Store and return the request
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
    // First get the current configuration
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const currentConfig = response.data;

    // Remove custom tracking field from the configuration before sending
    const { modelType, _provider, ...cleanConfig } = config;

    // Provider should be same as modelType for consistent naming
    const provider = modelType;

    // Create the updated config object
    const updatedConfig = {
      ...currentConfig,
      llm: [
        {
          provider,
          configuration: cleanConfig,
        },
      ],
    };

    // Only update if the configuration has actually changed
    const currentLlmConfig = currentConfig.llm?.[0]?.configuration || {};
    if (JSON.stringify(currentLlmConfig) === JSON.stringify(cleanConfig)) {
      console.log('Configuration unchanged, skipping update');
      return { data: { success: true, message: 'No changes detected' } };
    }

    // Update the configuration
    const updateResponse = await axios.post(
      '/api/v1/configurationManager/aiModelsConfig',
      updatedConfig
    );
    
    // Clear any cached or pending requests to ensure fresh data on next fetch
    invalidateLlmConfigCache();
    
    return updateResponse;
  } catch (error) {
    console.error('Error updating LLM configuration:', error);
    throw error;
  }
};
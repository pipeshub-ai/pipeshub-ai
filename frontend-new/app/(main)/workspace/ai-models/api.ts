import { apiClient } from '@/lib/api';
import type {
  AllModelsResponse,
  CapabilitiesResponse,
  ModelsByTypeResponse,
  ProviderSchemaResponse,
  RegistryResponse,
} from './types';

const BASE = '/api/v1/configurationManager';

export const AIModelsApi = {
  // Registry endpoints (proxied to Python backend)
  getRegistry: async (params?: { search?: string; capability?: string }) => {
    const { data } = await apiClient.get<RegistryResponse>(`${BASE}/ai-models/registry`, {
      params,
    });
    return data;
  },

  getCapabilities: async () => {
    const { data } = await apiClient.get<CapabilitiesResponse>(
      `${BASE}/ai-models/registry/capabilities`
    );
    return data;
  },

  getProviderSchema: async (providerId: string, capability?: string) => {
    const { data } = await apiClient.get<ProviderSchemaResponse>(
      `${BASE}/ai-models/registry/${providerId}/schema`,
      { params: capability ? { capability } : undefined }
    );
    return data;
  },

  // CRUD endpoints (existing Node.js backend)
  getAllModels: async () => {
    const { data } = await apiClient.get<AllModelsResponse>(`${BASE}/ai-models`);
    return data;
  },

  getModelsByType: async (modelType: string) => {
    const { data } = await apiClient.get<ModelsByTypeResponse>(
      `${BASE}/ai-models/${modelType}`
    );
    return data;
  },

  addProvider: async (payload: {
    modelType: string;
    provider: string;
    configuration: Record<string, unknown> | {model:"default"};
    isMultimodal?: boolean;
    isReasoning?: boolean;
    isDefault?: boolean;
    contextLength?: number | null;
  }) => {
    const body =
      payload.provider === 'default'
        ? { ...payload, configuration: { model: 'default' as const } }
        : payload;
    const { data } = await apiClient.post(`${BASE}/ai-models/providers`, body);
    return data;
  },

  updateProvider: async (
    modelType: string,
    modelKey: string,
    payload: {
      provider: string;
      configuration: Record<string, unknown>;
      isMultimodal?: boolean;
      isReasoning?: boolean;
      isDefault?: boolean;
      contextLength?: number | null;
    }
  ) => {
    const { data } = await apiClient.put(
      `${BASE}/ai-models/providers/${modelType}/${modelKey}`,
      payload
    );
    return data;
  },

  deleteProvider: async (modelType: string, modelKey: string) => {
    const { data } = await apiClient.delete(
      `${BASE}/ai-models/providers/${modelType}/${modelKey}`
    );
    return data;
  },

  setDefault: async (modelType: string, modelKey: string) => {
    const { data } = await apiClient.put(
      `${BASE}/ai-models/default/${modelType}/${modelKey}`
    );
    return data;
  },
};

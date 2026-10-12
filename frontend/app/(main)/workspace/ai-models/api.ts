import { apiClient } from '@/lib/api';
import { streamSSEGet, streamSSERequest, type SSEEvent, type SSEStreamingOptions } from '@/lib/api/streaming';
import { REVEAL_PARAMS } from '@/lib/hooks/use-secret-reveal-available';
import type {
  AllModelsResponse,
  DiscoveryResponse,
  DownloadProgressPayload,
  ModelRoleAssignment,
  ModelRolesResponse,
  ModelsByTypeResponse,
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

  discoverModels: async (body: {
    provider: string;
    capability?: string;
    configuration?: Record<string, unknown>;
    modelKey?: string;
    query?: string;
  }, signal?: AbortSignal) => {
    const { data } = await apiClient.post<DiscoveryResponse>(`${BASE}/ai-models/discover`, body, {
      signal,
    });
    return data;
  },

  batchAddModels: (
    body: {
      modelType: string;
      provider: string;
      configuration: Record<string, unknown>;
      models: Array<{
        model: string;
        modelFriendlyName?: string;
        isMultimodal: boolean;
        isReasoning: boolean;
        contextLength?: number | null;
      }>;
      defaultModel?: string;
      connectionId?: string;
    },
    options: SSEStreamingOptions,
  ) => streamSSERequest(`${BASE}/ai-models/providers/batch`, body, options),

  rotateConnectionCredentials: async (connectionId: string, configuration: Record<string, unknown>) => {
    const { data } = await apiClient.put(
      `${BASE}/ai-models/connections/${encodeURIComponent(connectionId)}/credentials`,
      { configuration },
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

  /** Stored configuration of one model, credentials included. Only where the deployment allows it. */
  revealModelConfiguration: async (modelType: string, modelKey: string) => {
    const { data } = await apiClient.get<ModelsByTypeResponse>(
      `${BASE}/ai-models/${modelType}`,
      { params: REVEAL_PARAMS }
    );
    const model = data?.models?.find((m) => m.modelKey === modelKey);
    return (model?.configuration ?? {}) as Record<string, unknown>;
  },

  addProvider: async (payload: {
    modelType: string;
    provider: string;
    modelName?: string;
    configuration: Record<string, unknown>;
    isMultimodal?: boolean;
    isReasoning?: boolean;
    isDefault?: boolean;
    contextLength?: number | null;
  }) => {
    const { modelName, ...rest } = payload;
    const body =
      payload.provider === 'default' && modelName
        ? { ...rest, configuration: { model: modelName } }
        : rest;
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
      `${BASE}/ai-models/providers/${modelType}/${modelKey}`,
      { suppressErrorToast: true }
    );
    return data;
  },

  setDefault: async (modelType: string, modelKey: string) => {
    const { data } = await apiClient.put(
      `${BASE}/ai-models/default/${modelType}/${modelKey}`
    );
    return data;
  },

  // Local embedding model download progress
  prepareModel: async (model: string, trustRemoteCode = false) => {
    const { data } = await apiClient.post<DownloadProgressPayload>(
      `${BASE}/ai-models/prepare-model`,
      { model, trustRemoteCode }
    );
    return data;
  },

  streamDownloadProgress: (
    model: string,
    options: {
      onEvent: (event: SSEEvent<DownloadProgressPayload>) => void;
      onError: (error: Error) => void;
      signal?: AbortSignal;
    }
  ) =>
    streamSSEGet<DownloadProgressPayload>(
      `${BASE}/ai-models/download-progress?model=${encodeURIComponent(model)}`,
      options
    ),

  // Model roles endpoints
  getRoles: async () => {
    const { data } = await apiClient.get<ModelRolesResponse>(`${BASE}/ai-models/roles`);
    return data;
  },

  updateRoles: async (roles: Record<string, ModelRoleAssignment>) => {
    const { data } = await apiClient.put(`${BASE}/ai-models/roles`, { roles });
    return data;
  },
};

import { apiClient } from '@/lib/api';
import type { GeminiFileSearchConfig, GeminiFileSearchKbStats } from './types';

// ============================================================
// Base URL — routes live on the KB router (proxied to the Python
// backend's /api/v1/gemini-file-search/* endpoints).
// ============================================================

const BASE_URL = '/api/v1/knowledgeBase/gemini-file-search';

const DEFAULT_CONFIG: GeminiFileSearchConfig = {
  enabled: false,
  generationModel: 'gemini-3.5-flash',
  embeddingModel: 'models/gemini-embedding-2',
  maxStoresPerQuery: 5,
  maxMediaPerQuery: 5,
};

const normalizeConfig = (raw: Record<string, unknown> | undefined | null): GeminiFileSearchConfig => {
  if (!raw || typeof raw !== 'object') return { ...DEFAULT_CONFIG };
  return {
    enabled: typeof raw.enabled === 'boolean' ? raw.enabled : DEFAULT_CONFIG.enabled,
    generationModel:
      typeof raw.generationModel === 'string' && raw.generationModel.trim()
        ? raw.generationModel
        : DEFAULT_CONFIG.generationModel,
    embeddingModel:
      typeof raw.embeddingModel === 'string' && raw.embeddingModel.trim()
        ? raw.embeddingModel
        : DEFAULT_CONFIG.embeddingModel,
    maxStoresPerQuery: Number.isFinite(Number(raw.maxStoresPerQuery))
      ? Math.min(Math.max(Number(raw.maxStoresPerQuery), 1), DEFAULT_CONFIG.maxStoresPerQuery)
      : DEFAULT_CONFIG.maxStoresPerQuery,
    maxMediaPerQuery: Number.isFinite(Number(raw.maxMediaPerQuery))
      ? Number(raw.maxMediaPerQuery)
      : DEFAULT_CONFIG.maxMediaPerQuery,
  };
};

export const GeminiFileSearchApi = {
  /** Fetch the global settings (merged with defaults). */
  async getConfig(): Promise<GeminiFileSearchConfig> {
    try {
      const { data } = await apiClient.get(`${BASE_URL}/config`);
      if (data?.success) {
        return normalizeConfig(data.config);
      }
      return { ...DEFAULT_CONFIG };
    } catch {
      return { ...DEFAULT_CONFIG };
    }
  },

  /** Persist the global settings. Returns the merged config. */
  async updateConfig(config: GeminiFileSearchConfig): Promise<GeminiFileSearchConfig> {
    const { data } = await apiClient.put(`${BASE_URL}/config`, config);
    if (data?.success) {
      return normalizeConfig(data.config);
    }
    throw new Error(data?.message || 'Failed to update Gemini File Search settings');
  },

  /** Fetch store size + document counts for a KB. */
  async getKbStats(kbId: string): Promise<GeminiFileSearchKbStats | null> {
    try {
      const { data } = await apiClient.get(`${BASE_URL}/kb/${encodeURIComponent(kbId)}/stats`);
      return data;
    } catch {
      return null;
    }
  },
};

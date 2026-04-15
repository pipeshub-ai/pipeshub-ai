import { apiClient } from '@/lib/api';
import type {
  ConfiguredWebSearchProvider,
  WebSearchConfigData,
  WebSearchProviderData,
  WebSearchSettings,
} from './types';

// ============================================================
// Base URL
// ============================================================

const BASE_URL = '/api/v1/configurationManager/web-search';

// ============================================================
// Defaults
// ============================================================

const DEFAULT_WEB_SEARCH_SETTINGS: WebSearchSettings = {
  includeImages: false,
  maxImages: 3,
};

const normalizeWebSearchSettings = (settings: Record<string, unknown>): WebSearchSettings => {
  const includeImages =
    typeof settings?.includeImages === 'boolean'
      ? settings.includeImages
      : DEFAULT_WEB_SEARCH_SETTINGS.includeImages;
  const parsedMaxImages = Number(settings?.maxImages);
  const maxImages =
    Number.isInteger(parsedMaxImages) && parsedMaxImages >= 1 && parsedMaxImages <= 500
      ? parsedMaxImages
      : DEFAULT_WEB_SEARCH_SETTINGS.maxImages;

  return { includeImages, maxImages };
};

// ============================================================
// Web Search API
// ============================================================

export const WebSearchApi = {
  async getConfig(): Promise<WebSearchConfigData> {
    try {
      const { data } = await apiClient.get(BASE_URL);
      if (data.status === 'success') {
        const providers: ConfiguredWebSearchProvider[] = Array.isArray(data.providers)
          ? data.providers.map((provider: { providerKey: string; provider: string; configuration?: Record<string, unknown>; isDefault?: boolean }) => ({
              providerKey: provider.providerKey,
              provider: provider.provider,
              configuration: provider.configuration || {},
              isDefault: provider.isDefault || false,
            }))
          : [];

        return {
          providers,
          settings: normalizeWebSearchSettings(data.settings),
        };
      }
      return { providers: [], settings: DEFAULT_WEB_SEARCH_SETTINGS };
    } catch {
      return { providers: [], settings: DEFAULT_WEB_SEARCH_SETTINGS };
    }
  },

  async updateSettings(settings: WebSearchSettings): Promise<WebSearchSettings> {
    const { data } = await apiClient.put(`${BASE_URL}/settings`, settings);
    if (data.status === 'success') {
      return normalizeWebSearchSettings(data.settings);
    }
    throw new Error(data.message || 'Failed to update web search settings');
  },

  async addProvider(providerData: WebSearchProviderData): Promise<unknown> {
    const requestData = {
      provider: providerData.provider,
      configuration: providerData.configuration,
      isDefault: providerData.isDefault || false,
    };
    const { data } = await apiClient.post(`${BASE_URL}/providers`, requestData);
    if (data.status === 'success') {
      return data;
    }
    throw new Error(data.message || 'Failed to add provider');
  },

  async updateProvider(providerKey: string, providerData: WebSearchProviderData): Promise<unknown> {
    const requestData = {
      provider: providerData.provider,
      configuration: providerData.configuration,
      isDefault: providerData.isDefault || false,
    };
    const { data } = await apiClient.put(`${BASE_URL}/providers/${providerKey}`, requestData);
    if (data.status === 'success') {
      return data;
    }
    throw new Error(data.message || 'Failed to update provider');
  },

  async deleteProvider(providerKey: string): Promise<unknown> {
    const { data } = await apiClient.delete(`${BASE_URL}/providers/${providerKey}`);
    if (data.status === 'success') {
      return data;
    }
    throw new Error(data.message || 'Failed to delete provider');
  },

  async setDefaultProvider(providerKey: string): Promise<unknown> {
    const { data } = await apiClient.put(`${BASE_URL}/default/${providerKey}`);
    if (data.status === 'success') {
      return data;
    }
    throw new Error(data.message || 'Failed to set default provider');
  },
};

import { apiClient } from '@/lib/api';
import { REVEAL_PARAMS } from '@/lib/hooks/use-secret-reveal-available';
import {
  MAX_IMAGES_PER_PAGE,
  type ConfiguredWebSearchProvider,
  type WebSearchConfigData,
  type WebSearchProviderAgentUsage,
  type WebSearchProviderData,
  type WebSearchSettings,
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
  // The server stores up to 500 but reads no more than MAX_IMAGES_PER_PAGE from a page: show the
  // count that applies, so the editor is never handed a value it refuses.
  const maxImages =
    Number.isInteger(parsedMaxImages) && parsedMaxImages >= 1 && parsedMaxImages <= 500
      ? Math.min(parsedMaxImages, MAX_IMAGES_PER_PAGE)
      : DEFAULT_WEB_SEARCH_SETTINGS.maxImages;

  return { includeImages, maxImages };
};

// ============================================================
// Web Search API
// ============================================================

export const WebSearchApi = {
  /** Stored API key of one provider. Only where the deployment allows it. */
  async revealProviderApiKey(providerKey: string): Promise<string | null> {
    const { data } = await apiClient.get(BASE_URL, { params: REVEAL_PARAMS });
    const providers: { providerKey?: string; configuration?: { apiKey?: unknown } }[] =
      Array.isArray(data?.providers) ? data.providers : [];
    const apiKey = providers.find((p) => p.providerKey === providerKey)?.configuration?.apiKey;
    return typeof apiKey === 'string' ? apiKey : null;
  },

  /** The stored providers; throws when they can't be read (unlike `getConfig`). */
  async getProviders(): Promise<ConfiguredWebSearchProvider[]> {
    const { data } = await apiClient.get(BASE_URL);
    if (data?.status !== 'success' || !Array.isArray(data.providers)) {
      throw new Error('Could not read the web search settings');
    }
    return data.providers;
  },

  async getConfig(options?: { suppressErrorToast?: boolean }): Promise<WebSearchConfigData> {
    try {
      const { data } = await apiClient.get(BASE_URL, { suppressErrorToast: options?.suppressErrorToast });
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
          // An older server sends no flag: the default provider is what made web search work.
          configured: typeof data.configured === 'boolean' ? data.configured : providers.some((p) => p.isDefault),
          settings: normalizeWebSearchSettings(data.settings),
          inherited: !!data?.inherited || false,
        };
      }
      return { providers: [], inherited: false };
    } catch {
      return { providers: [], inherited: false };
    }
  },

  async updateSettings(settings: WebSearchSettings): Promise<WebSearchSettings> {
    const { data } = await apiClient.put(`${BASE_URL}/settings`, settings);
    if (data.status === 'success') {
      return normalizeWebSearchSettings(data.settings);
    }
    // No English fallback: the page shows a translated description when the server gives none.
    throw new Error(data.message || '');
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

  async getProviderUsage(provider: string): Promise<WebSearchProviderAgentUsage[]> {
    try {
      const { data } = await apiClient.get(`/api/v1/agents/web-search-usage/${encodeURIComponent(provider)}`);
      if (data.success && Array.isArray(data.agents)) {
        return data.agents;
      }
      return [];
    } catch {
      return [];
    }
  },

  /** `warning` is set when DuckDuckGo was saved while it refused the test search. */
  async setDefaultProvider(providerKey: string): Promise<{ warning?: string }> {
    const { data } = await apiClient.put(`${BASE_URL}/default/${providerKey}`);
    if (data.status === 'success') {
      return data;
    }
    throw new Error(data.message || 'Failed to set default provider');
  },
};

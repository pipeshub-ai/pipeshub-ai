import axios from 'src/utils/axios';
import {
  ConfiguredWebSearchProvider,
  WebSearchConfigData,
  WebSearchProviderData,
  WebSearchSettings,
} from '../types';

const DEFAULT_WEB_SEARCH_SETTINGS: WebSearchSettings = {
  includeImages: false,
  maxImages: 3,
};

const normalizeWebSearchSettings = (settings: any): WebSearchSettings => {
  const includeImages =
    typeof settings?.includeImages === 'boolean'
      ? settings.includeImages
      : DEFAULT_WEB_SEARCH_SETTINGS.includeImages;
  const parsedMaxImages = Number(settings?.maxImages);
  const maxImages =
    Number.isInteger(parsedMaxImages) && parsedMaxImages >= 1 && parsedMaxImages <= 500
      ? parsedMaxImages
      : DEFAULT_WEB_SEARCH_SETTINGS.maxImages;

  return {
    includeImages,
    maxImages,
  };
};

export const webSearchService = {
  async getConfig(): Promise<WebSearchConfigData> {
    try {
      const response = await axios.get('/api/v1/configurationManager/web-search');
      if (response.data.status === 'success') {
        const providers = Array.isArray(response.data.providers)
          ? response.data.providers.map((provider: any) => ({
              providerKey: provider.providerKey,
              provider: provider.provider,
              configuration: provider.configuration || {},
              isDefault: provider.isDefault || false,
            }))
          : [];

        return {
          providers,
          settings: normalizeWebSearchSettings(response.data.settings),
        };
      }
      return {
        providers: [],
        settings: DEFAULT_WEB_SEARCH_SETTINGS,
      };
    } catch (err) {
      console.error('Error fetching web search config:', err);
      return {
        providers: [],
        settings: DEFAULT_WEB_SEARCH_SETTINGS,
      };
    }
  },

  // Get all configured web search providers
  async getAllProviders(): Promise<ConfiguredWebSearchProvider[]> {
    const config = await this.getConfig();
    return config.providers;
  },

  async updateSettings(settings: WebSearchSettings): Promise<WebSearchSettings> {
    try {
      const response = await axios.put('/api/v1/configurationManager/web-search/settings', settings);
      if (response.data.status === 'success') {
        return normalizeWebSearchSettings(response.data.settings);
      }
      throw new Error(response.data.message || 'Failed to update web search settings');
    } catch (err: any) {
      console.error('Error updating web search settings:', err);
      throw new Error(
        err.response?.data?.message || err.message || 'Failed to update web search settings'
      );
    }
  },

  // Add new provider
  async addProvider(providerData: WebSearchProviderData): Promise<any> {
    try {
      const requestData = {
        provider: providerData.provider,
        configuration: providerData.configuration,
        isDefault: providerData.isDefault || false,
      };

      const response = await axios.post(
        '/api/v1/configurationManager/web-search/providers',
        requestData
      );

      if (response.data.status === 'success') {
        return response.data;
      }
      throw new Error(response.data.message || 'Failed to add provider');
    } catch (err: any) {
      console.error('Error adding web search provider:', err);
      throw new Error(
        err.response?.data?.message || err.message || 'Failed to add web search provider'
      );
    }
  },

  // Update provider
  async updateProvider(providerKey: string, providerData: WebSearchProviderData): Promise<any> {
    try {
      const requestData = {
        provider: providerData.provider,
        configuration: providerData.configuration,
        isDefault: providerData.isDefault || false,
      };

      const response = await axios.put(
        `/api/v1/configurationManager/web-search/providers/${providerKey}`,
        requestData
      );

      if (response.data.status === 'success') {
        return response.data;
      }
      throw new Error(response.data.message || 'Failed to update provider');
    } catch (err: any) {
      console.error('Error updating provider:', err);
      throw new Error(err.response?.data?.message || err.message || 'Failed to update provider');
    }
  },

  // Delete provider
  async deleteProvider(providerKey: string): Promise<any> {
    try {
      const response = await axios.delete(
        `/api/v1/configurationManager/web-search/providers/${providerKey}`
      );

      if (response.data.status === 'success') {
        return response.data;
      }
      throw new Error(response.data.message || 'Failed to delete provider');
    } catch (err: any) {
      console.error('Error deleting provider:', err);
      throw new Error(err.response?.data?.message || err.message || 'Failed to delete provider');
    }
  },

  // Set default provider
  async setDefaultProvider(providerKey: string): Promise<any> {
    try {
      const response = await axios.put(
        `/api/v1/configurationManager/web-search/default/${providerKey}`
      );

      if (response.data.status === 'success') {
        return response.data;
      }
      throw new Error(response.data.message || 'Failed to set default provider');
    } catch (err: any) {
      console.error('Error setting default provider:', err);
      throw new Error(
        err.response?.data?.message || err.message || 'Failed to set default provider'
      );
    }
  },
};

export const { getAllProviders, addProvider, updateProvider, deleteProvider, setDefaultProvider } =
  webSearchService;

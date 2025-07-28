import { DynamicConfigFactory, GeneratedProvider } from "./config-factory";
import { EMBEDDING_PROVIDERS, LLM_PROVIDERS, SMTP_PROVIDERS, STORAGE_PROVIDERS, URL_PROVIDERS } from "./providers";

// AUTO-GENERATED CONFIGS
export const LLM_CONFIG = DynamicConfigFactory.generateConfigType(LLM_PROVIDERS);
export const EMBEDDING_CONFIG = DynamicConfigFactory.generateConfigType(EMBEDDING_PROVIDERS);
export const STORAGE_CONFIG = DynamicConfigFactory.generateConfigType(STORAGE_PROVIDERS);
export const SMTP_CONFIG = DynamicConfigFactory.generateConfigType(SMTP_PROVIDERS);
export const URL_CONFIG = DynamicConfigFactory.generateConfigType(URL_PROVIDERS);

// UNIFIED CONFIG TYPE
export type ConfigType = 'llm' | 'embedding' | 'storage' | 'url' | 'smtp';

// HELPER FUNCTIONS
export const getProvidersForType = (configType: ConfigType): GeneratedProvider[] => {
  switch (configType) {
    case 'llm':
      return LLM_CONFIG;
    case 'embedding':
      return EMBEDDING_CONFIG;
    case 'storage':
      return STORAGE_CONFIG;
    case 'url':
      return URL_CONFIG;
    case 'smtp':
      return SMTP_CONFIG;
    default:
      console.error(`Unknown config type: ${configType}`);
      return [];
  }
};

export const getProviderById = (configType: ConfigType, id: string): GeneratedProvider | null => {
  const providers = getProvidersForType(configType);
  return providers.find(p => p.id === id) || null;
};

// LEGACY SUPPORT FUNCTIONS (for backward compatibility)
export const getLlmProviders = () => LLM_CONFIG;
export const getLlmProviderById = (id: string) => getProviderById('llm', id);

export const getEmbeddingProviders = () => EMBEDDING_CONFIG;
export const getEmbeddingProviderById = (id: string) => getProviderById('embedding', id);

export const getStorageProviders = () => STORAGE_CONFIG;
export const getStorageProviderById = (id: string) => getProviderById('storage', id);

export const getUrlProviders = () => URL_CONFIG;
export const getUrlProviderById = (id: string) => getProviderById('url', id);

export const getSmtpProviders = () => SMTP_CONFIG;
export const getSmtpProviderById = (id: string) => getProviderById('smtp', id);
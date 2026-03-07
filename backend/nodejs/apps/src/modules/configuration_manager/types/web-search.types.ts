/**
 * Web Search provider configuration
 */
export interface WebSearchProviderConfiguration {
  provider: string;
  configuration: Record<string, any>;
  providerKey: string;
  isDefault: boolean;
  [key: string]: any;
}

export interface WebSearchSettings {
  includeImages: boolean;
  maxImages: number;
}

/**
 * Web Search Configuration structure
 */
export interface WebSearchConfig {
  providers: WebSearchProviderConfiguration[];
  settings?: WebSearchSettings;
}

/**
 * Web Search provider configuration
 */
export interface WebSearchProviderConfiguration {
  provider: string;
  configuration: Record<string, any>;
  providerKey: string;
  isDefault: boolean;
}

export interface WebSearchSettings {
  enabled: boolean;
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

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

/**
 * Providers that only read pages (backing up the built-in page reader) and cannot search, so
 * they can never be the default web search provider.
 */
export const PAGE_READER_ONLY_PROVIDERS: ReadonlySet<string> = new Set([
  'crawl4ai',
]);

export function canSearch(provider: unknown): boolean {
  return (
    typeof provider !== 'string' || !PAGE_READER_ONLY_PROVIDERS.has(provider)
  );
}

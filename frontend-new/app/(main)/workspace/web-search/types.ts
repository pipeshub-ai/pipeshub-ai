// ============================================================
// Web Search Configuration — Types
// ============================================================

export type WebSearchProviderType = 'duckduckgo' | 'serper' | 'tavily';

export const DUCKDUCKGO_PROVIDER_ID: WebSearchProviderType = 'duckduckgo';

// ── API response shapes ──────────────────────────────────────

export interface ConfiguredWebSearchProvider {
  providerKey: string;
  provider: string;
  configuration: Record<string, string>;
  isDefault: boolean;
}

export interface WebSearchSettings {
  includeImages: boolean;
  maxImages: number;
}

export interface WebSearchConfigData {
  providers: ConfiguredWebSearchProvider[];
  settings: WebSearchSettings;
}

// ── Web search provider usage (agent check) ─────────────────

export interface WebSearchProviderAgentUsage {
  name: string;
  _key: string;
  creatorName: string | null;
}

// ── API request payloads ─────────────────────────────────────

export interface WebSearchProviderData {
  provider: string;
  configuration: Record<string, string>;
  isDefault?: boolean;
}

// ── Panel state ──────────────────────────────────────────────

export type ConfigurableProvider = Extract<WebSearchProviderType, 'serper' | 'tavily'>;

// ── Per-provider display metadata ────────────────────────────

export interface WebSearchProviderMeta {
  type: WebSearchProviderType;
  label: string;
  description: string;
  icon: string;
  iconType: 'material' | 'image';
  configurable: boolean;
  docUrl: string;
  apiKeyHelperText?: string;
  apiKeyPlaceholder?: string;
}

export const WEB_SEARCH_PROVIDER_META: WebSearchProviderMeta[] = [
  {
    type: 'duckduckgo',
    label: 'DuckDuckGo',
    description: 'Built-in, no configuration required',
    icon: '/icons/web-search/duckduckgo.svg',
    iconType: 'image',
    configurable: false,
    docUrl: 'https://duckduckgo.com/about',
  },
  {
    type: 'serper',
    label: 'Serper',
    description: 'Fast Google Search API with generous free tier',
    icon: '/icons/web-search/serper.svg',
    iconType: 'image',
    configurable: true,
    docUrl: 'https://serper.dev/docs',
    apiKeyHelperText: 'Get your API key from https://serper.dev',
    apiKeyPlaceholder: 'Enter your Serper API key',
  },
  {
    type: 'tavily',
    label: 'Tavily',
    description: 'AI-optimised search API',
    icon: '/icons/web-search/tavily.svg',
    iconType: 'image',
    configurable: true,
    docUrl: 'https://docs.tavily.com',
    apiKeyHelperText: 'Get your API key from https://tavily.com',
    apiKeyPlaceholder: 'Enter your Tavily API key',
  },
];

export const ALL_WEB_SEARCH_PROVIDER_TYPES: WebSearchProviderType[] = [
  'duckduckgo',
  'serper',
  'tavily',
];

// ============================================================
// Web Search Configuration — Types
// ============================================================

export type WebSearchProviderType =
  | 'duckduckgo'
  | 'serper'
  | 'tavily'
  | 'exa'
  | 'brave'
  | 'searxng'
  | 'firecrawl'
  | 'crawl4ai';

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
  inherited?: boolean;
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

export type ConfigurableProvider = Exclude<WebSearchProviderType, 'duckduckgo'>;

// ── Per-provider display metadata ────────────────────────────

/** One setting a provider's form asks for. `apiKey` is the only secret, masked once saved. */
export interface WebSearchProviderField {
  key: 'apiKey' | 'baseUrl';
  label: string;
  placeholder: string;
  required: boolean;
  helperText?: string;
}

export interface WebSearchProviderMeta {
  type: WebSearchProviderType;
  label: string;
  description: string;
  icon: string;
  iconType: 'material' | 'image';
  configurable: boolean;
  docUrl: string;
  /** False for a provider that only reads pages: it backs up page reading and is never the default. */
  searches: boolean;
  fields: WebSearchProviderField[];
}

function apiKeyField(label: string, url: string, required = true): WebSearchProviderField {
  return {
    key: 'apiKey',
    label: 'API Key',
    placeholder: `Enter your ${label} API key`,
    required,
    helperText: `Get your API key from ${url}`,
  };
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
    searches: true,
    fields: [],
  },
  {
    type: 'serper',
    label: 'Serper',
    description: 'Fast Google Search API with generous free tier',
    icon: '/icons/web-search/serper.svg',
    iconType: 'image',
    configurable: true,
    docUrl: 'https://serper.dev/docs',
    searches: true,
    fields: [apiKeyField('Serper', 'https://serper.dev')],
  },
  {
    type: 'tavily',
    label: 'Tavily',
    description: 'AI-optimised search API',
    icon: '/icons/web-search/tavily.svg',
    iconType: 'image',
    configurable: true,
    docUrl: 'https://docs.tavily.com',
    searches: true,
    fields: [apiKeyField('Tavily', 'https://tavily.com')],
  },
  {
    type: 'exa',
    label: 'Exa',
    description: 'Neural web search API',
    icon: '/icons/web-search/exa.svg',
    iconType: 'image',
    configurable: true,
    docUrl: 'https://docs.exa.ai',
    searches: true,
    fields: [apiKeyField('Exa', 'https://dashboard.exa.ai/api-keys')],
  },
  {
    type: 'brave',
    label: 'Brave Search',
    description: 'Independent web index with its own API',
    icon: 'travel_explore',
    iconType: 'material',
    configurable: true,
    docUrl: 'https://api-dashboard.search.brave.com/app/documentation',
    searches: true,
    fields: [apiKeyField('Brave Search', 'https://api-dashboard.search.brave.com')],
  },
  {
    type: 'searxng',
    label: 'SearXNG',
    description: 'Self-hosted metasearch, no API key needed',
    icon: 'dns',
    iconType: 'material',
    configurable: true,
    docUrl: 'https://docs.searxng.org',
    searches: true,
    fields: [
      {
        key: 'baseUrl',
        label: 'Server URL',
        placeholder: 'http://searxng:8080',
        required: true,
        helperText: 'Its settings.yml must list json under search.formats.',
      },
    ],
  },
  {
    type: 'firecrawl',
    label: 'Firecrawl',
    description: 'Searches and reads pages, including JavaScript-heavy sites',
    icon: 'local_fire_department',
    iconType: 'material',
    configurable: true,
    docUrl: 'https://docs.firecrawl.dev',
    searches: true,
    fields: [
      {
        key: 'baseUrl',
        label: 'Server URL',
        placeholder: 'https://api.firecrawl.dev',
        required: false,
        helperText: 'Leave empty for Firecrawl Cloud; set it for a self-hosted server.',
      },
      apiKeyField('Firecrawl', 'https://www.firecrawl.dev/app/api-keys', false),
    ],
  },
  {
    type: 'crawl4ai',
    label: 'Crawl4AI',
    description: 'Self-hosted page reader for JavaScript-heavy sites',
    icon: 'article',
    iconType: 'material',
    configurable: true,
    docUrl: 'https://docs.crawl4ai.com',
    searches: false,
    fields: [
      {
        key: 'baseUrl',
        label: 'Server URL',
        placeholder: 'http://crawl4ai:11235',
        required: true,
        helperText: 'Version 0.9.4 or later.',
      },
      {
        key: 'apiKey',
        label: 'API Token',
        placeholder: 'Enter the server token',
        required: false,
        helperText: 'The token the server was started with, if any.',
      },
    ],
  },
];

export const ALL_WEB_SEARCH_PROVIDER_TYPES: WebSearchProviderType[] = WEB_SEARCH_PROVIDER_META.map((m) => m.type);

/** The label of the provider marked default, or DuckDuckGo's when none is (the server's fallback). */
export function defaultProviderLabel(
  providers: Pick<ConfiguredWebSearchProvider, 'provider' | 'isDefault'>[],
): string {
  const type = providers.find((p) => p.isDefault)?.provider ?? DUCKDUCKGO_PROVIDER_ID;
  return WEB_SEARCH_PROVIDER_META.find((m) => m.type === type)?.label ?? type;
}

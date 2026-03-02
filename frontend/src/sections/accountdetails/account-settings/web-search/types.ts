export type ProviderId = string;

/** Built-in provider that is always configured and cannot be removed or edited */
export const DUCKDUCKGO_PROVIDER_ID = 'duckduckgo';

export interface ConfiguredWebSearchProvider {
  providerKey: string;
  provider: string;
  configuration: Record<string, any>;
  isDefault: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface WebSearchProvider {
  id: string;
  name: string;
  description: string;
  requiredFields: RequiredField[];
  optionalFields?: OptionalField[];
  isPopular?: boolean;
  src?: string;
  color: string;
}

export interface RequiredField {
  name: string;
  label: string;
  type: 'text' | 'password';
  placeholder?: string;
  description?: string;
}

export interface OptionalField {
  name: string;
  label: string;
  type: 'text' | 'select';
  placeholder?: string;
  description?: string;
  options?: string[];
}

export interface WebSearchProviderData {
  provider: string;
  configuration: Record<string, any>;
  isDefault?: boolean;
}

export interface WebSearchSettings {
  includeImages: boolean;
  maxImages: number;
}

export interface WebSearchConfigData {
  providers: ConfiguredWebSearchProvider[];
  settings: WebSearchSettings;
}

export const AVAILABLE_WEB_SEARCH_PROVIDERS: WebSearchProvider[] = [
  {
    id: 'duckduckgo',
    name: 'DuckDuckGo',
    description: 'Free web search with no API key required (System provided)',
    requiredFields: [],
    isPopular: true,
    color: '#DE5833',
  },
  {
    id: 'serper',
    name: 'Serper',
    description: 'Fast Google Search API with generous free tier',
    requiredFields: [
      {
        name: 'apiKey',
        label: 'API Key',
        type: 'password',
        placeholder: 'Enter your Serper API key',
        description: 'Get your API key from https://serper.dev',
      },
    ],
    isPopular: true,
    color: '#4285F4',
  },
  {
    id: 'tavily',
    name: 'Tavily',
    description: 'AI-optimized search API',
    requiredFields: [
      {
        name: 'apiKey',
        label: 'API Key',
        type: 'password',
        placeholder: 'Enter your Tavily API key',
        description: 'Get your API key from https://tavily.com',
      },
    ],
    isPopular: true,
    color: '#7C3AED',
  }
];

export interface ApiResponse {
  status: string;
  data?: any;
  providers?: ConfiguredWebSearchProvider[];
  settings?: WebSearchSettings;
  message?: string;
}

// ============================================================================
// MCP Server Types
// ============================================================================

export interface MCPServerAuthHint {
  methods: string[];
  defaultMethod: string;
  oauth2AuthorizationUrl?: string;
  oauth2TokenUrl?: string;
  oauth2Scopes?: string[];
  envMapping?: Record<string, string>;
}

export interface MCPServerTemplate {
  typeId: string;
  displayName: string;
  description: string;
  transport: string;
  command?: string;
  defaultArgs?: string[];
  requiredEnv?: string[];
  optionalEnv?: string[];
  url?: string;
  authMode: string;
  supportedAuthTypes: string[];
  redirectUri?: string;
  iconPath?: string;
  documentationUrl?: string;
  tags: string[];
  auth?: MCPServerAuthHint;
  authConfig?: {
    methods: string[];
    defaultMethod: string;
    oauth2AuthorizationUrl?: string;
    oauth2TokenUrl?: string;
    oauth2Scopes?: string[];
    envMapping?: Record<string, string>;
  };
}

export interface MCPServerTool {
  name: string;
  namespacedName: string;
  description: string;
  inputSchema?: Record<string, unknown>;
}

export interface MCPServerInstance {
  instanceId: string;
  instanceName: string;
  serverType: string;
  displayName: string;
  description?: string;
  transport: string;
  command?: string;
  args?: string[];
  url?: string;
  authMode: string;
  supportedAuthTypes: string[];
  requiredEnv?: string[];
  iconPath?: string;
  enabled: boolean;
  isAuthenticated?: boolean;
  isConfigured?: boolean;
  isFromRegistry?: boolean;
  agentIsAuthenticated?: boolean;
  oauthExpiresAt?: number;
  hasOAuthClientConfig?: boolean;
  tools?: MCPServerTool[];
  toolCount?: number;
}

export interface PaginationInfo {
  page: number;
  limit: number;
  total: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

// ============================================================================
// UI Types
// ============================================================================

export type TabValue = 'my-servers' | 'available';
export type FilterType = 'all' | 'authenticated' | 'not-authenticated';

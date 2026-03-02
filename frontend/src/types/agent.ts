// src/types/agent.ts
export interface AgentTemplate {
  _id: string;
  _key: string;
  name: string;
  description: string;
  category: string;
  startMessage: string;
  systemPrompt: string;
  tools: string[];
  models: string[];
  apps: string[];
  kb: string[];
  vectorDBs: string[];
  tags: string[];
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  usageCount: number;
  rating: number;
  icon?: string;
  isDeleted?: boolean;
}

export interface Agent {
  _id: string;
  _key: string;
  name: string;
  description: string;
  startMessage: string;
  systemPrompt: string;
  instructions?: string;
  // Legacy fields (deprecated)
  tools?: string[];
  kb?: string[];
  connectors?: ConnectorInstance[];
  // New graph-based fields
  // Models can be:
  // - Array of strings (model keys) before enrichment
  // - Array of enriched model objects after backend processing
  models: (string | {
    modelKey: string;
    provider: string;
    modelName: string;
    isReasoning?: boolean;
    isMultimodal?: boolean;
    isDefault?: boolean;
    modelType?: string;
    [key: string]: any;
  })[];
  knowledge?: Knowledge[]; // Linked via graph
  toolsets?: Toolset[]; // Linked via graph
  vectorDBs?: string[];
  tags: string[];
  templateId?: string;
  createdBy: string;
  orgId?: string;
  createdAtTimestamp: string | number;
  updatedAtTimestamp: string | number;
  lastUsedAt?: string;
  conversationCount?: number;
  sharedWith?: string[];
  version?: number;
  icon?: string;
  isActive?: boolean;
  isDeleted?: boolean;
  shareWithOrg?: boolean; // Whether agent is shared with the whole organization
  can_view?: boolean;
  can_share?: boolean;
  can_edit?: boolean;
  can_delete?: boolean;
  user_role?: string;
  access_type?: string;
  flow?: {
    nodes: any[];
    edges: any[];
  };
}

export interface AgentConversation {
  _id: string;
  agentKey: string;
  title: string;
  messages: AgentMessage[];
  createdBy: string;
  orgId: string;
  createdAt: string;
  updatedAt: string;
  lastActivityAt: string;
  isActive: boolean;
  metadata?: Record<string, any>;
  conversationSource: string;
  userId: string;
}

export interface AgentMessage {
  _id: string;
  messageType: 'user_query' | 'bot_response';
  content: string;
  contentFormat: 'MARKDOWN' | 'HTML' | 'TEXT';
  citations?: AgentCitation[];
  confidence?: string;
  feedback?: any[];
  followUpQuestions?: string[];
  createdAt: string;
  updatedAt: string;
  metadata?: Record<string, any>;
}

export interface AgentCitation {
  citationId: string;
  citationData?: {
    _id: string;
    content: string;
    metadata: Record<string, any>;
    createdAt: string;
    updatedAt: string;
    chunkIndex?: number;
  };
  citationType?: string;
}

export interface AgentTemplateFormData {
  name: string;
  description: string;
  category: string;
  startMessage: string;
  systemPrompt: string;
  instructions?: string;
  tags: string[];
  isDeleted?: boolean;
}

export interface ConnectorInstance {
  id: string;
  name: string;
  type: string;
  scope: 'personal' | 'team';
  category: 'knowledge' | 'action'; // Purpose: knowledge (apps) or action (tools)
}

// ============================================================================
// New Graph-based Toolset Types
// ============================================================================

export enum ToolsetType {
  APP = 'app',
  FILE = 'file',
  WEB_SEARCH = 'web_search',
  DATABASE = 'database',
  UTILITY = 'utility',
}

export enum AuthType {
  OAUTH = 'OAUTH',
  API_TOKEN = 'API_TOKEN',
  BEARER_TOKEN = 'BEARER_TOKEN',
  NONE = 'NONE',
}

/**
 * Tool node - minimal schema
 */
export interface Tool {
  _key: string;
  _id?: string;
  toolName: string;
  fullName: string; // e.g., "slack.send_message"
  appName: string;
  description: string;
  createdBy: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
}

/**
 * Toolset node - minimal schema (NO auth fields in DB)
 * Auth status retrieved dynamically from etcd
 */
export interface Toolset {
  _key: string;
  _id?: string;
  name: string; // Normalized name for etcd path
  displayName: string;
  type: string; // app, utility, etc
  userId: string; // Owner (for etcd path)
  createdBy: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  // Dynamic fields from etcd (not in DB)
  authType?: string;
  isAuthenticated?: boolean;
  isConfigured?: boolean;
  etcdPath?: string;
  metadata?: Record<string, any>; // From etcd
  // Tools linked to this toolset
  tools?: Tool[];
  // Additional display fields (from registry)
  category?: string;
  group?: string;
  iconPath?: string;
  description?: string;
  supportedAuthTypes?: string[];
  toolCount?: number;
}

/**
 * Knowledge node - filter configuration for connectors
 */
export interface Knowledge {
  _key: string;
  _id?: string;
  connectorId: string;
  filters: string; // Stringified JSON
  filtersParsed?: {
    recordGroups?: string[];
    records?: string[];
    [key: string]: any;
  }; // Parsed filters
  createdBy: string;
  updatedBy?: string | null;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
}

/**
 * Toolset from registry (Python code)
 * Now includes tools for drag-and-drop selection
 */
export interface RegistryToolset {
  name: string; // Normalized name (e.g., "slack")
  normalized_name: string; // Normalized name for API
  displayName: string; // Display name (e.g., "Slack")
  appGroup?: string; // App group (e.g., "Communication")
  category: string; // Category (e.g., "communication", "app")
  supportedAuthTypes: string[]; // Supported auth types
  description: string; // Toolset description
  iconPath?: string; // Icon path
  tools: RegistryTool[]; // Tools included in this toolset
  toolCount: number; // Number of tools
  config?: Record<string, any>; // Configuration schema
}

/**
 * Tool from registry (Python code)
 */
export interface RegistryTool {
  name: string; // Tool name (e.g., "send_message")
  fullName: string; // Full name (e.g., "slack.send_message")
  description: string;
  parameters?: Array<{
    name: string;
    type: string;
    description: string;
    required: boolean;
    default?: any;
  }>;
  tags?: string[];
  examples?: any[];
}

/**
 * Toolset auth status from etcd
 */
export interface ToolsetAuthStatus {
  exists: boolean;
  isAuthenticated: boolean;
  authType: string | null;
  configuredAt?: number;
}

/**
 * Simple tool reference (for agent form data, doesn't need full DB fields)
 */
export interface ToolRef {
  name: string;
  fullName: string;
  description?: string;
}

/**
 * Toolset reference in agent (replaces direct connector array)
 */
export interface ToolsetReference {
  id: string;
  instanceId?: string; // NEW: the specific instance identifier
  name: string;
  displayName: string;
  type: string;
  tools?: ToolRef[];
}

/**
 * Knowledge reference in agent
 */
export interface KnowledgeReference {
  id: string;
  connectorId: string;
  filters?: Record<string, any>;
}

export interface AgentFormData {
  name: string;
  description: string;
  startMessage: string;
  systemPrompt: string;
  instructions?: string;
  // Models as array of model objects or keys
  models: (string | { provider: string; modelName: string; isReasoning: boolean; modelKey: string })[];
  // Legacy support (deprecated)
  tools?: string[];
  kb?: string[];
  connectors?: ConnectorInstance[];
  vectorDBs?: string[];
  tags: string[];
  templateId?: string;
  shareWithOrg?: boolean; // Whether to share this agent with the whole organization
  flow?: {
    nodes: any[];
    edges: any[];
  };
  // New graph-based fields
  knowledge?: KnowledgeReference[];
  toolsets?: ToolsetReference[];
}

export interface AgentStats {
  totalAgents: number;
  activeAgents: number;
  totalConversations: number;
  totalMessages: number;
  averageResponseTime: number;
  popularTags: string[];
  recentActivity: any[];
}

export interface AgentFilterOptions {
  status?: 'active' | 'inactive' | 'draft' | null;
  tags?: string[];
  createdBy?: string;
  dateRange?: {
    start: string;
    end: string;
  };
  searchQuery?: string;
}



export interface FlowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: {
    type: string;
    label: string;
    config: Record<string, any>;
    inputs?: string[];
    outputs?: string[];
    isConfigured?: boolean;
  };
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
}

export interface AgentFlowConfig {
  nodes: FlowNode[];
  edges: FlowEdge[];
  metadata?: {
    version: string;
    createdAt: string;
    updatedAt: string;
  };
}

// Enhanced Agent interface with flow support
export interface AgentWithFlow extends Agent {
  flowConfig?: AgentFlowConfig;
  builderType?: 'wizard' | 'flow';
}

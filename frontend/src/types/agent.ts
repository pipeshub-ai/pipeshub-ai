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
  tools: string[];
  models: {
    provider: string;
    modelName: string;
    isReasoning: boolean;
  }[];
  apps: string[];
  kb: string[];
  vectorDBs: string[];
  tags: string[];
  templateId?: string;
  createdBy: string;
  orgId: string;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  lastUsedAt?: string;
  conversationCount: number;
  sharedWith: string[];
  version: number;
  icon?: string;
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
  tags: string[];
  isDeleted?: boolean;
}

export interface AgentFormData {
  name: string;
  description: string;
  startMessage: string;
  systemPrompt: string;
  tools: string[];
  models: {
    provider: string;
    modelName: string;
    isReasoning: boolean;
  }[];
  apps: string[];
  kb: string[];
  vectorDBs: string[];
  tags: string[];
  templateId?: string;
  flow?: {
    nodes: any[];
    edges: any[];
  };
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

// src/sections/agents/services/agent-api-service.ts
import axios from 'src/utils/axios';
import type {
  Agent,
  AgentTemplate,
  AgentFormData,
  AgentTemplateFormData,
  AgentConversation,
  AgentFilterOptions,
  AgentStats,
  ConnectorInstance,
} from 'src/types/agent';

export interface PaginationParams {
  page?: number;
  limit?: number;
  sort?: string;
  order?: 'asc' | 'desc';
}

export interface KnowledgeBaseResponse {
  knowledgeBases: KnowledgeBase[];
  pagination: {
    page: number;
    limit: number;
    totalCount: number;
    totalPages: number;
    hasNext: boolean;
    hasPrev: boolean;
  };
  filters: {
    applied: Record<string, any>;
    available: {
      permissions: string[];
      sortFields: string[];
      sortOrders: string[];
    };
  };
}

export interface KnowledgeBase {
  id: string; // KB ID (record group ID)
  name: string;
  connectorId: string; // KB connector instance ID (e.g., "knowledgeBase_orgId")
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  createdBy: string;
  userRole: string;
  folders?: Array<{
    id: string;
    name: string;
    createdAtTimestamp: number;
    webUrl: string;
  }>;
}

export interface ToolData {
  tool_id: string;
  app_name: string;
  tool_name: string;
  full_name: string;
  description: string;
  parameters: Array<{
    name: string;
    type: string;
    description: string;
    required: boolean;
    default: any;
    enum: any;
    items: any;
    properties: any;
  }>;
  returns: any;
  examples: any[];
  tags: string[];
  parameter_count: number;
  required_parameters: string[];
  optional_parameters: string[];
  ctag: string;
  created_at: string;
  updated_at: string;
}

class AgentApiService {
  static baseUrl = '/api/v1/agents';

  // Agent CRUD Operations
  static async getAgents(): Promise<Agent[]> {
    const response = await axios.get(`${this.baseUrl}`);
    return response.data.agents;
  }

  static async getAgent(agentKey: string): Promise<Agent> {
    const response = await axios.get(`${this.baseUrl}/${agentKey}`);
    return response.data.agent;
  }

  static async createAgent(data: AgentFormData): Promise<Agent> {
    // Transform data before sending
    const transformedData = this.transformAgentFormData(data);
    const response = await axios.post(`${this.baseUrl}/create`, transformedData);
    return response.data.agent;
  }

  static async updateAgent(agentKey: string, data: Partial<AgentFormData>): Promise<Agent> {
    // Transform data before sending
    const transformedData = this.transformAgentFormData(data);
    const response = await axios.put(`${this.baseUrl}/${agentKey}`, transformedData);
    return response.data.agent;
  }

  static async deleteAgent(agentKey: string): Promise<void> {
    await axios.delete(`${this.baseUrl}/${agentKey}`);
  }

  // Agent Template CRUD Operations
  static async getTemplates(): Promise<AgentTemplate[]> {
    const response = await axios.get(`${this.baseUrl}/template`);
    return response.data.templates;
  }

  static async getTemplate(templateKey: string): Promise<AgentTemplate> {
    const response = await axios.get(`${this.baseUrl}/templates/${templateKey}`);
    return response.data.template;
  }

  static async createTemplate(data: AgentTemplateFormData): Promise<AgentTemplate> {
    const response = await axios.post(`${this.baseUrl}/template`, data);
    return response.data.template;
  }

  static async updateTemplate(
    templateKey: string,
    data: Partial<AgentTemplateFormData>
  ): Promise<AgentTemplate> {
    const response = await axios.patch(`${this.baseUrl}/templates/${templateKey}`, data);
    return response.data.template;
  }

  static async deleteTemplate(templateKey: string): Promise<void> {
    await axios.delete(`${this.baseUrl}/templates/${templateKey}`);
  }

  // Agent Conversation Operations
  static async getAgentConversations(agentKey: string): Promise<AgentConversation[]> {
    const response = await axios.get(`${this.baseUrl}/${agentKey}/conversations`);
    return response.data.conversations;
  }

  static async getAgentConversation(
    agentKey: string,
    conversationKey: string
  ): Promise<AgentConversation> {
    const response = await axios.get(
      `${this.baseUrl}/${agentKey}/conversations/${conversationKey}`
    );
    return response.data.conversation;
  }

  static async createAgentConversation(
    agentKey: string,
    title?: string
  ): Promise<AgentConversation> {
    const response = await axios.post(`${this.baseUrl}/${agentKey}/conversations`, { title });
    return response.data.conversation;
  }

  static async deleteAgentConversation(agentKey: string, conversationKey: string): Promise<void> {
    await axios.delete(`${this.baseUrl}/${agentKey}/conversations/${conversationKey}`);
  }

  static async updateConversationTitle(
    agentKey: string,
    conversationKey: string,
    title: string
  ): Promise<AgentConversation> {
    const response = await axios.patch(
      `${this.baseUrl}/${agentKey}/conversations/${conversationKey}/title`,
      { title }
    );
    return response.data.conversation;
  }

  // Agent Chat Operations
  static async sendMessage(
    agentKey: string,
    conversationKey: string | null,
    message: string,
    options?: {
      stream?: boolean;
      modelKey?: string;
      temperature?: number;
    }
  ): Promise<Response> {
    const url = conversationKey
      ? `${this.baseUrl}/${agentKey}/conversations/${conversationKey}/messages/stream`
      : `${this.baseUrl}/${agentKey}/conversations/stream`;

    const token = localStorage.getItem('jwt_access_token');

    return fetch(`${axios.defaults.baseURL}${url}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        query: message,
        ...options,
      }),
    });
  }

  static async regenerateMessage(
    agentKey: string,
    conversationKey: string,
    messageId: string
  ): Promise<AgentConversation> {
    const response = await axios.post(
      `${this.baseUrl}/${agentKey}/conversations/${conversationKey}/messages/${messageId}/regenerate`
    );
    return response.data.conversation;
  }

  static async submitMessageFeedback(
    agentKey: string,
    conversationKey: string,
    messageId: string,
    feedback: any
  ): Promise<void> {
    await axios.post(
      `${this.baseUrl}/${agentKey}/conversations/${conversationKey}/messages/${messageId}/feedback`,
      feedback
    );
  }

  // Analytics & Statistics
  static async getAgentStats(agentKey?: string): Promise<AgentStats> {
    const url = agentKey ? `${this.baseUrl}/${agentKey}/stats` : `${this.baseUrl}/stats`;
    const response = await axios.get(url);
    return response.data.stats;
  }

  static async getPopularTags(): Promise<string[]> {
    const response = await axios.get(`${this.baseUrl}/tags`);
    return response.data.tags;
  }

  // Validation
  static async validateAgentName(name: string, excludeAgentKey?: string): Promise<boolean> {
    const params = new URLSearchParams({ name });
    if (excludeAgentKey) params.append('exclude', excludeAgentKey);

    const response = await axios.get(`${this.baseUrl}/validate-name?${params.toString()}`);
    return response.data.isValid;
  }

  static async validateTemplateName(name: string, excludeTemplateKey?: string): Promise<boolean> {
    const params = new URLSearchParams({ name });
    if (excludeTemplateKey) params.append('exclude', excludeTemplateKey);

    const response = await axios.get(
      `${this.baseUrl}/templates/validate-name?${params.toString()}`
    );
    return response.data.isValid;
  }

  static async getAvailableModels(): Promise<string[]> {
    const response = await axios.get(`/api/v1/configurationManager/ai-models/available/llm`);
    return response.data.models;
  }

  static async getKnowledgeBases(params?: {
    page?: number;
    limit?: number;
    search?: string;
    sortBy?: string;
    sortOrder?: 'asc' | 'desc';
  }): Promise<KnowledgeBaseResponse> {
    const response = await axios.get(`/api/v1/knowledgeBase/`, { params });

    if (!response.data) {
      throw new Error('Failed to fetch knowledge bases');
    }

    // Check if the API returns paginated data or simple array
    if (response.data.knowledgeBases && response.data.pagination) {
      // Paginated response
      return {
        knowledgeBases: response.data.knowledgeBases,
        pagination: response.data.pagination,
        filters: response.data.filters || {
          applied: {},
          available: { permissions: [], sortFields: [], sortOrders: [] },
        },
      };
    }

    if (Array.isArray(response.data)) {
      // Simple array response (fallback)
      return {
        knowledgeBases: response.data,
        pagination: {
          page: 1,
          limit: response.data.length,
          totalCount: response.data.length,
          totalPages: 1,
          hasNext: false,
          hasPrev: false,
        },
        filters: { applied: {}, available: { permissions: [], sortFields: [], sortOrders: [] } },
      };
    }

    // Handle other response formats
    return response.data;
  }

  /**
   * Transform form data to match backend expectations
   * - Toolsets: Pass through as-is (new graph-based format)
   * - Knowledge: Pass through as-is (new graph-based format)
   * - Tools: Convert to app_name.tool_name format if not already (legacy)
   * - KB: Ensure we're sending IDs not names
   * - Connector Instances: Ensure proper format with required fields (legacy)
   */
  private static transformAgentFormData(data: Partial<AgentFormData>): Partial<AgentFormData> {
    const transformed = { ...data };
    
    // Log what we're about to send
    console.log('📦 Agent form data before transform:', {
      toolsets: data.toolsets,
      knowledge: data.knowledge,
      models: data.models,
    });

    // Transform tools to ensure they're in the correct format
    if (transformed.tools && Array.isArray(transformed.tools)) {
      transformed.tools = transformed.tools
        .filter((tool) => tool && typeof tool === 'string') // Filter out undefined/null/non-string tools
        .map((tool) => {
          // If tool is already in app_name.tool_name format, keep it
          if (tool.includes('.')) {
            return tool;
          }
          // Otherwise, assume it needs transformation (though this shouldn't happen with the new UI)
          return tool;
        });
    }

    // KBs are now part of knowledge array - no separate kb field needed

    // Transform connector instances to ensure proper format
    if (transformed.connectors && Array.isArray(transformed.connectors)) {
      // Validate: No duplicate connector instances with same id and category
      const connectorKeyMap = new Map<string, ConnectorInstance>();
      const duplicates: string[] = [];

      transformed.connectors
        .filter((instance) => instance && typeof instance === 'object' && instance.id && instance.name && instance.type)
        .forEach((instance) => {
          const connectorKey = `${instance.id}:${instance.category || 'action'}`;
          if (connectorKeyMap.has(connectorKey)) {
            if (!duplicates.includes(connectorKey)) {
              duplicates.push(connectorKey);
            }
          } else {
            connectorKeyMap.set(connectorKey, instance);
          }
        });

      if (duplicates.length > 0) {
        throw new Error(
          `Duplicate connector instances found. Each connector ID can only appear once per category. Duplicates: ${duplicates.join(', ')}`
        );
      }

      transformed.connectors = transformed.connectors
        .filter((instance) => instance && typeof instance === 'object' && instance.id && instance.name && instance.type)
        .map((instance) => ({
          id: instance.id,
          name: instance.name,
          type: instance.type,
          scope: instance.scope || 'personal',
          category: instance.category || 'action',
        }));
    }

    // Clean up empty arrays - handle each property type specifically
    if (transformed.tools && Array.isArray(transformed.tools) && transformed.tools.length === 0) {
      transformed.tools = [] as string[];
    }

    if (
      transformed.models &&
      Array.isArray(transformed.models) &&
      transformed.models.length === 0
    ) {
      transformed.models = [];
    }



    // KBs are now part of knowledge array - no separate kb field

    if (
      transformed.connectors &&
      Array.isArray(transformed.connectors) &&
      transformed.connectors.length === 0
    ) {
      transformed.connectors = [];
    }

    if (
      transformed.vectorDBs &&
      Array.isArray(transformed.vectorDBs) &&
      transformed.vectorDBs.length === 0
    ) {
      transformed.vectorDBs = [] as string[];
    }

    if (transformed.tags && Array.isArray(transformed.tags) && transformed.tags.length === 0) {
      transformed.tags = [] as string[];
    }

    // Log the final transformed data
    console.log('📦 Agent form data after transform:', {
      toolsets: transformed.toolsets,
      knowledge: transformed.knowledge,
      models: transformed.models,
    });

    return transformed;
  }

  /**
   * Get knowledge base details by ID
   */
  static async getKnowledgeBaseDetails(kbId: string): Promise<KnowledgeBase | null> {
    try {
      const response = await this.getKnowledgeBases();
      return response.knowledgeBases.find((kb) => kb.id === kbId) || null;
    } catch (error) {
      console.error('Error getting knowledge base details:', error);
      return null;
    }
  }

  /**
   * Search knowledge bases by name
   */
  static async searchKnowledgeBases(searchTerm: string, limit = 10): Promise<KnowledgeBase[]> {
    try {
      const response = await this.getKnowledgeBases({
        search: searchTerm,
        limit,
      });
      return response.knowledgeBases;
    } catch (error) {
      console.error('Error searching knowledge bases:', error);
      return [];
    }
  }

}

export default AgentApiService;

import { apiClient } from '@/lib/api';
import { mapApiConversationToConversation } from '@/chat/api';
import type { ConversationsListResponse } from '@/chat/types';
import type {
  AgentDetail,
  AgentsListApiResponse,
  AgentsListPagination,
  AgentsListParams,
  AgentsListResult,
  AgentToolsListRow,
  AgentConversationDetailApiResponse,
  AgentConversationsListResult,
  CreateAgentApiResponse,
  FetchAgentConversationResult,
  GetAgentApiResponse,
  GetAgentResult,
  KnowledgeBaseForBuilder,
  KnowledgeBaseListApiResponse,
  KnowledgeBasesForBuilderResult,
  UpdateAgentApiResponse,
} from './types';
import type { AgentFormPayload } from './agent-builder/types';
import type { BuilderSidebarToolset } from './toolsets-api';

const AGENTS_BASE_URL = '/api/v1/agents';

const KB_PAGE_MAX = 100;

/** Stable slug for `tool-*` node types; flow reconstruction matches on `full_name` first. */
function catalogToolIdFromFullName(fullName: string): string {
  const s = fullName.trim() || 'tool';
  return s.replace(/[^a-zA-Z0-9_-]/g, '_');
}

/**
 * Flatten tools from toolset registry + configured instances (GET /api/v1/toolsets/...).
 * Replaces removed GET /api/v1/agents/tools/list.
 */
export function buildToolsCatalogFromToolsets(toolsets: BuilderSidebarToolset[]): AgentToolsListRow[] {
  const rows: AgentToolsListRow[] = [];
  const seen = new Set<string>();
  for (const ts of toolsets) {
    const appName = (ts.toolsetType || ts.normalized_name || ts.name || 'other').trim() || 'other';
    for (const t of ts.tools || []) {
      const full_name = (t.fullName || '').trim() || `${appName}.${t.name || 'tool'}`;
      if (seen.has(full_name)) continue;
      seen.add(full_name);
      const tool_name = (t.name || '').trim() || full_name.split('.').pop() || 'tool';
      rows.push({
        tool_id: catalogToolIdFromFullName(full_name),
        app_name: appName,
        tool_name,
        full_name,
        description: (t.description || '').trim(),
        parameters: [],
      });
    }
  }
  return rows;
}

/** Add tools that exist only on the saved agent (e.g. older toolsets) and prefer `_key` as `tool_id` when present. */
export function mergeToolsFromAgentDetail(
  agent: AgentDetail | null,
  catalog: AgentToolsListRow[]
): AgentToolsListRow[] {
  const byFull = new Map<string, AgentToolsListRow>();
  for (const r of catalog) {
    byFull.set(r.full_name, { ...r });
  }

  if (!agent?.toolsets?.length) {
    return Array.from(byFull.values());
  }

  for (const ts of agent.toolsets) {
    const appName = (ts.name || ts.type || 'other').trim() || 'other';
    for (const t of ts.tools || []) {
      const full_name = (t.fullName || '').trim() || `${appName}.${t.name || 'tool'}`;
      const prev = byFull.get(full_name);
      if (prev) {
        byFull.set(full_name, {
          ...prev,
          tool_id: t._key || prev.tool_id,
          description: (prev.description || t.description || '').trim(),
        });
      } else {
        byFull.set(full_name, {
          tool_id: t._key || catalogToolIdFromFullName(full_name),
          app_name: appName,
          tool_name: (t.name || '').trim() || full_name.split('.').pop() || 'tool',
          full_name,
          description: (t.description || '').trim(),
          parameters: [],
        });
      }
    }
  }
  return Array.from(byFull.values());
}

/** Collect `fullName` from GET /agents/:id `toolsets[].tools[]` for stream payloads */
export function extractAgentToolFullNames(agent: AgentDetail | null | undefined): string[] {
  if (!agent?.toolsets?.length) return [];
  const names: string[] = [];
  for (const ts of agent.toolsets) {
    if (!ts?.tools?.length) continue;
    for (const t of ts.tools) {
      if (typeof t.fullName === 'string') {
        names.push(t.fullName);
      }
    }
  }
  return names;
}

function emptyPagination(): AgentsListPagination {
  return {
    currentPage: 1,
    limit: 20,
    totalItems: 0,
    totalPages: 0,
    hasNext: false,
    hasPrev: false,
  };
}

export const AgentsApi = {
  async getAgents(params?: AgentsListParams): Promise<AgentsListResult> {
    const query: Record<string, string | number> = {};
    if (params?.page != null) query.page = params.page;
    if (params?.limit != null) query.limit = params.limit;
    if (params?.search) query.search = params.search;
    if (params?.sort) query.sort_by = params.sort;
    if (params?.order) query.sort_order = params.order;

    const { data } = await apiClient.get<AgentsListApiResponse>(AGENTS_BASE_URL, { params: query });
    return {
      agents: data?.agents ?? [],
      pagination: data?.pagination ?? emptyPagination(),
    };
  },

  async getAgent(agentKey: string): Promise<GetAgentResult> {
    const { data } = await apiClient.get<GetAgentApiResponse>(`${AGENTS_BASE_URL}/${agentKey}`);
    const agent = data?.agent;
    if (!agent || typeof agent !== 'object') {
      return { agent: null, toolFullNames: [] };
    }
    return {
      agent,
      toolFullNames: extractAgentToolFullNames(agent),
    };
  },

  /**
   * Single agent conversation + messages (chat history).
   * GET /api/v1/agents/:agentId/conversations/:conversationId
   */
  async fetchAgentConversation(
    agentId: string,
    conversationId: string
  ): Promise<FetchAgentConversationResult> {
    const { data } = await apiClient.get<AgentConversationDetailApiResponse>(
      `${AGENTS_BASE_URL}/${agentId}/conversations/${conversationId}`
    );

    const conv = data?.conversation;
    if (!conv) {
      throw new Error('Agent conversation not found');
    }
    const messages = conv.messages ?? [];
    return {
      conversation: conv,
      messages,
    };
  },

  /**
   * List conversations for a single agent (sidebar + more panel).
   * GET /api/v1/agents/:agentId/conversations
   */
  async fetchAgentConversations(
    agentId: string,
    params?: { page?: number; limit?: number; search?: string }
  ): Promise<AgentConversationsListResult> {
    const query: Record<string, string | number> = {};
    if (params?.page != null) query.page = params.page;
    if (params?.limit != null) query.limit = params.limit;
    if (params?.search) query.search = params.search;

    const { data } = await apiClient.get<ConversationsListResponse>(
      `${AGENTS_BASE_URL}/${agentId}/conversations`,
      { params: query }
    );

    const pagination = data?.pagination ?? {
      page: 1,
      limit: 20,
      totalCount: 0,
      totalPages: 0,
      hasNextPage: false,
      hasPrevPage: false,
    };

    return {
      conversations: (data?.conversations ?? []).map(mapApiConversationToConversation),
      sharedConversations: (data?.sharedWithMeConversations ?? []).map(mapApiConversationToConversation),
      pagination,
    };
  },

  /**
   * DELETE /api/v1/agents/:agentId/conversations/:conversationId
   */
  async deleteAgentConversation(agentId: string, conversationId: string): Promise<void> {
    await apiClient.delete(`${AGENTS_BASE_URL}/${agentId}/conversations/${conversationId}`);
  },

  /** POST /api/v1/agents/create */
  async createAgent(payload: AgentFormPayload): Promise<AgentDetail> {
    const { data } = await apiClient.post<CreateAgentApiResponse>(`${AGENTS_BASE_URL}/create`, payload);
    if (!data?.agent) throw new Error('Create agent failed');
    return data.agent;
  },

  /**
   * PUT /api/v1/agents/:agentKey
   * Some deployments return only `{ status, message }` on success; we then GET the agent.
   */
  async updateAgent(agentKey: string, payload: Partial<AgentFormPayload>): Promise<AgentDetail> {
    const { data } = await apiClient.put<UpdateAgentApiResponse>(`${AGENTS_BASE_URL}/${agentKey}`, payload);

    if (data?.agent && typeof data.agent === 'object') {
      return data.agent;
    }

    const { agent } = await this.getAgent(agentKey);
    if (agent) {
      return agent;
    }

    const msg = typeof data?.message === 'string' ? data.message.trim() : '';
    throw new Error(msg || 'Update agent failed');
  },

  /** DELETE /api/v1/agents/:agentKey */
  async deleteAgent(agentKey: string): Promise<void> {
    await apiClient.delete(`${AGENTS_BASE_URL}/${agentKey}`);
  },

  /** GET /api/v1/knowledgeBase/ — collections for agent builder (limit 1–100 per request). */
  async getKnowledgeBasesForBuilder(params?: { page?: number; limit?: number }): Promise<KnowledgeBasesForBuilderResult> {
    const limit = Math.min(Math.max(params?.limit ?? KB_PAGE_MAX, 1), KB_PAGE_MAX);
    const page = Math.max(params?.page ?? 1, 1);
    const { data } = await apiClient.get<KnowledgeBaseListApiResponse>('/api/v1/knowledgeBase/', {
      params: { page, limit },
    });
    return { knowledgeBases: data?.knowledgeBases ?? [] };
  },

  /** Paginate KB list until exhausted (each page obeys API max limit of 100). */
  async getAllKnowledgeBasesForBuilder(): Promise<KnowledgeBasesForBuilderResult> {
    const all: KnowledgeBaseForBuilder[] = [];
    let page = 1;
    for (;;) {
      const { knowledgeBases } = await this.getKnowledgeBasesForBuilder({ page, limit: KB_PAGE_MAX });
      all.push(...knowledgeBases);
      if (knowledgeBases.length < KB_PAGE_MAX) break;
      page += 1;
      if (page > 500) break;
    }
    return { knowledgeBases: all };
  },
};

export type { AgentToolsListRow, KnowledgeBaseForBuilder } from './types';

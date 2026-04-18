import { apiClient } from '@/lib/api';
import { mapApiConversationToConversation } from '@/chat/api';
import type { ConversationsListResponse, Conversation } from '@/chat/types';
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
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';

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

type KnowledgeGraphEntry = Record<string, unknown>;

function isKnowledgeGraphEntry(value: unknown): value is KnowledgeGraphEntry {
  return Boolean(value) && typeof value === 'object';
}

/** Parse `filters` / `filtersParsed` on a knowledge graph row (string JSON or object). */
function parseKnowledgeFiltersRecord(entry: KnowledgeGraphEntry): Record<string, unknown> {
  const fp = entry.filtersParsed ?? entry.filters;
  if (typeof fp === 'string') {
    try {
      const parsed = JSON.parse(fp) as unknown;
      return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }
  if (fp && typeof fp === 'object') {
    return fp as Record<string, unknown>;
  }
  return {};
}

/**
 * `recordGroups` from parsed filters: trimmed non-empty ids, order preserved,
 * duplicates within the same filters object collapsed.
 */
function normalizedRecordGroupIds(filters: Record<string, unknown>): string[] {
  const rg = filters.recordGroups;
  if (!Array.isArray(rg)) return [];
  const out: string[] = [];
  const seenLocal = new Set<string>();
  for (const x of rg) {
    if (typeof x !== 'string') continue;
    const id = x.trim();
    if (!id || seenLocal.has(id)) continue;
    seenLocal.add(id);
    out.push(id);
  }
  return out;
}

/** Toolset groups for agent chat Actions tab (labels, icons, search metadata). */
export interface AgentChatToolGroupRow {
  label: string;
  toolsetSlug: string;
  /** Stable id for UI keys — same toolset type may appear multiple times as instances. */
  instanceId?: string;
  iconPath?: string;
  fullNames: string[];
  toolDescriptions?: Record<string, string>;
}

export function buildAgentChatToolGroups(agent: AgentDetail | null | undefined): AgentChatToolGroupRow[] {
  if (!agent?.toolsets?.length) return [];
  const groups: AgentChatToolGroupRow[] = [];
  for (const ts of agent.toolsets) {
    const fullNames = (ts.tools || [])
      .map((t) => (typeof t.fullName === 'string' ? t.fullName.trim() : ''))
      .filter(Boolean);
    if (fullNames.length === 0) continue;

    const toolDescriptions: Record<string, string> = {};
    for (const t of ts.tools || []) {
      const fn = typeof t.fullName === 'string' ? t.fullName.trim() : '';
      if (!fn) continue;
      const d = typeof t.description === 'string' ? t.description.trim() : '';
      if (d) toolDescriptions[fn] = d;
    }

    const instanceLabel = typeof ts.instanceName === 'string' ? ts.instanceName.trim() : '';
    const productLabel = (ts.displayName || ts.name || 'Tools').trim();
    const instanceId = typeof ts.instanceId === 'string' ? ts.instanceId.trim() : '';
    groups.push({
      label: instanceLabel || productLabel,
      toolsetSlug: (typeof ts.name === 'string' ? ts.name : '').trim(),
      ...(instanceId ? { instanceId } : {}),
      iconPath:
        typeof ts.iconPath === 'string' && ts.iconPath.trim() ? ts.iconPath.trim() : undefined,
      fullNames,
      toolDescriptions: Object.keys(toolDescriptions).length ? toolDescriptions : undefined,
    });
  }
  return groups;
}

/** Connector instance ids (non–knowledge-base) from the agent graph `knowledge[]` entry. */
export function extractAgentKnowledgeDefaults(
  agent: AgentDetail | null | undefined
): { apps: string[]; kb: string[] } {
  const apps: string[] = [];
  const kb: string[] = [];
  const raw = agent?.knowledge;
  if (!Array.isArray(raw)) return { apps, kb };

  for (const entry of raw) {
    if (!isKnowledgeGraphEntry(entry)) continue;
    const connectorId = typeof entry.connectorId === 'string' ? entry.connectorId.trim() : '';
    if (!connectorId) continue;

    const recordGroups = normalizedRecordGroupIds(parseKnowledgeFiltersRecord(entry));

    if (recordGroups.length > 0) {
      kb.push(...recordGroups);
    } else if (!connectorId.startsWith('knowledgeBase_')) {
      apps.push(connectorId);
    }
  }

  return {
    apps: Array.from(new Set(apps)),
    kb: Array.from(new Set(kb)),
  };
}

/** App connector rows (instance ids + labels) for agent chat UI — excludes KB-backed sources. */
export function extractAgentKnowledgeConnectors(
  agent: AgentDetail | null | undefined
): Array<{ id: string; label: string; connectorKind: string }> {
  const rows: Array<{ id: string; label: string; connectorKind: string }> = [];
  const seen = new Set<string>();
  const raw = agent?.knowledge;
  if (!Array.isArray(raw)) return rows;

  for (const entry of raw) {
    if (!isKnowledgeGraphEntry(entry)) continue;
    const connectorId = typeof entry.connectorId === 'string' ? entry.connectorId.trim() : '';
    if (!connectorId || connectorId.startsWith('knowledgeBase_')) continue;

    const recordGroups = normalizedRecordGroupIds(parseKnowledgeFiltersRecord(entry));
    if (recordGroups.length > 0) continue;

    if (seen.has(connectorId)) continue;
    seen.add(connectorId);

    const label =
      typeof entry.displayName === 'string' && entry.displayName.trim()
        ? entry.displayName.trim()
        : typeof entry.name === 'string' && entry.name.trim()
          ? entry.name.trim()
          : connectorId.split('/').pop() || connectorId;
    const connectorKind =
      (typeof entry.type === 'string' && entry.type.trim()) ||
      (typeof entry.name === 'string' && entry.name.trim()) ||
      label;
    rows.push({ id: connectorId, label, connectorKind });
  }
  return rows;
}

/** Row for agent-scoped Collections UI — ids come from `filters.recordGroups`, not hub KB node ids. */
export interface AgentKnowledgeCollectionRow {
  id: string;
  name: string;
  /** Knowledge graph `type` (e.g. `KB`, `Jira`, `Confluence`) — drives row artwork. */
  sourceType?: string;
}

/**
 * Build collection rows from the agent knowledge graph (`recordGroups`).
 * Labels use `displayName` / `name` on the knowledge entry so the list stays populated
 * even when ids do not match `/knowledge-hub/nodes` collection roots.
 */
export function extractAgentKnowledgeCollectionRows(
  agent: AgentDetail | null | undefined
): AgentKnowledgeCollectionRow[] {
  const rows: AgentKnowledgeCollectionRow[] = [];
  const seen = new Set<string>();
  const raw = agent?.knowledge;
  if (!Array.isArray(raw)) return rows;

  for (const entry of raw) {
    if (!isKnowledgeGraphEntry(entry)) continue;

    const recordGroups = normalizedRecordGroupIds(parseKnowledgeFiltersRecord(entry));
    if (recordGroups.length === 0) continue;

    const base =
      (typeof entry.displayName === 'string' && entry.displayName.trim()) ||
      (typeof entry.name === 'string' && entry.name.trim()) ||
      'Collection';

    const connectorId = typeof entry.connectorId === 'string' ? entry.connectorId.trim() : '';

    const sourceType =
      typeof entry.type === 'string' && entry.type.trim()
        ? entry.type.trim()
        : connectorId.startsWith('knowledgeBase_')
          ? 'KB'
          : undefined;

    let ordinal = 0;
    for (const rgId of recordGroups) {
      if (seen.has(rgId)) continue;
      seen.add(rgId);
      ordinal += 1;
      const name = recordGroups.length === 1 ? base : `${base} (${ordinal})`;
      rows.push({ id: rgId, name, sourceType });
    }
  }
  return rows;
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

  /**
   * PATCH /api/v1/agents/:agentKey/conversations/:conversationId/title
   */
  async renameAgentConversation(agentId: string, conversationId: string, title: string): Promise<void> {
    await apiClient.patch(`${AGENTS_BASE_URL}/${agentId}/conversations/${conversationId}/title`, { title });
  },

  /**
   * GET /api/v1/agents/conversations/show/archives
   * Returns all archived agent conversations for the current user, grouped by agentKey.
   */
  async fetchAllAgentsArchivedConversations(params?: { agentPage?: number; agentLimit?: number }): Promise<{
    groups: Array<{
      agentKey: string;
      conversations: Conversation[];
      totalCount: number;
      hasMore: boolean;
    }>;
    agentPagination: {
      page: number;
      limit: number;
      totalCount: number;
      totalPages: number;
      hasNextPage: boolean;
      hasPrevPage: boolean;
    };
  }> {
    const query: Record<string, string | number> = {};
    if (params?.agentPage != null) query.agentPage = params.agentPage;
    if (params?.agentLimit != null) query.agentLimit = params.agentLimit;

    const { data } = await apiClient.get<{
      groups: Array<{
        agentKey: string;
        conversations: ReturnType<typeof mapApiConversationToConversation>[];
        totalCount: number;
        hasMore: boolean;
      }>;
      agentPagination: {
        page: number;
        limit: number;
        totalCount: number;
        totalPages: number;
        hasNextPage: boolean;
        hasPrevPage: boolean;
      };
    }>(`${AGENTS_BASE_URL}/conversations/show/archives`, { params: query });

    return {
      groups: (data?.groups ?? []).map((g) => ({
        agentKey: g.agentKey,
        conversations: g.conversations.map((c: any) => mapApiConversationToConversation(c)),
        totalCount: g.totalCount,
        hasMore: g.hasMore,
      })),
      agentPagination: data?.agentPagination ?? {
        page: 1, limit: 5, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false,
      },
    };
  },

  /**
   * GET /api/v1/agents/:agentId/conversations/show/archives
   * Returns archived conversations for a specific agent (for "See More" pagination).
   */
  async fetchAgentArchivedConversations(
    agentId: string,
    params?: { page?: number; limit?: number }
  ): Promise<AgentConversationsListResult> {
    const query: Record<string, string | number> = {};
    if (params?.page != null) query.page = params.page;
    if (params?.limit != null) query.limit = params.limit;

    const { data } = await apiClient.get<{
      conversations: any[];
      pagination: ConversationsListResponse['pagination'];
    }>(`${AGENTS_BASE_URL}/${agentId}/conversations/show/archives`, { params: query });

    return {
      conversations: (data?.conversations ?? []).map(mapApiConversationToConversation),
      sharedConversations: [],
      pagination: data?.pagination ?? {
        page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false,
      },
    };
  },

  /**
   * POST /api/v1/agents/:agentKey/conversations/:conversationId/archive
   */
  async archiveAgentConversation(agentId: string, conversationId: string): Promise<void> {
    await apiClient.post(`${AGENTS_BASE_URL}/${agentId}/conversations/${conversationId}/archive`);
  },

  /**
   * POST /api/v1/agents/:agentKey/conversations/:conversationId/unarchive
   */
  async restoreAgentConversation(agentId: string, conversationId: string): Promise<void> {
    await apiClient.post(`${AGENTS_BASE_URL}/${agentId}/conversations/${conversationId}/unarchive`);
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

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { Conversation, ConversationMessage, AgentArchivedGroup } from './types';
import { ArchivedChatsApi } from './api';

// ======================================================
// Constants
// ======================================================

const ASSISTANT_CHATS_PAGE_SIZE = 10;
const AGENT_GROUPS_PAGE_SIZE = 5;
const AGENT_CHATS_PAGE_SIZE = 5;

// ======================================================
// Types
// ======================================================

interface PaginationState {
  page: number;
  totalCount: number;
  hasMore: boolean;
  isLoadingMore: boolean;
}

interface SelectedConversationState {
  id: string;
  title: string;
  messages: ConversationMessage[];
  /** agentKey if this is an agent conversation, null for normal chats. */
  agentKey: string | null;
}

interface ArchivedChatsState {
  // -- Normal conversations --
  conversations: Conversation[];
  isLoadingList: boolean;
  listError: string | null;
  assistantPagination: PaginationState;

  /**
   * Messages extracted from the archived list response, keyed by conversation ID.
   * Populated by loadArchivedConversations so that loadConversation doesn't need
   * a second API call.
   */
  messagesMap: Record<string, ConversationMessage[]>;

  // -- Agent conversation groups --
  agentGroups: AgentArchivedGroup[];
  isLoadingAgentGroups: boolean;
  agentGroupsError: string | null;
  /** Bumped whenever agent groups are re-fetched from the server (resets sidebar "See more" pagination). */
  agentGroupsEpoch: number;
  agentGroupsPagination: PaginationState;

  // -- Selected conversation --
  selected: SelectedConversationState | null;
  isLoadingConversation: boolean;
  conversationError: string | null;
}

interface ArchivedChatsActions {
  /** Fetch the first page of archived normal conversations. */
  loadArchivedConversations: () => Promise<void>;

  /** Load more normal conversations ("Show More"). */
  loadMoreAssistantChats: () => Promise<void>;

  /** Fetch the first page of archived agent conversations grouped by agent. */
  loadAgentArchivedConversations: () => Promise<void>;

  /** Load more agent groups ("More Agents"). */
  loadMoreAgentGroups: () => Promise<void>;

  /**
   * Load a single conversation's messages for display.
   * Pass agentKey for agent conversations.
   */
  loadConversation: (id: string, title: string, agentKey?: string | null) => Promise<void>;

  /** Load more conversations for a specific agent group ("More Chats"). */
  loadMoreForAgent: (agentKey: string, page: number) => Promise<void>;

  /** Remove a conversation from the list (after restore or permanent delete). */
  removeConversation: (id: string, agentKey?: string | null) => void;

  /** Clear the selected conversation state. */
  clearSelection: () => void;
}

type ArchivedChatsStore = ArchivedChatsState & ArchivedChatsActions;

// ======================================================
// Initial state
// ======================================================

const initialPagination: PaginationState = {
  page: 1,
  totalCount: 0,
  hasMore: false,
  isLoadingMore: false,
};

const initialState: ArchivedChatsState = {
  conversations: [],
  isLoadingList: false,
  listError: null,
  assistantPagination: { ...initialPagination },
  messagesMap: {},
  agentGroups: [],
  isLoadingAgentGroups: false,
  agentGroupsError: null,
  agentGroupsEpoch: 0,
  agentGroupsPagination: { ...initialPagination },
  selected: null,
  isLoadingConversation: false,
  conversationError: null,
};

// ======================================================
// Store
// ======================================================

export const useArchivedChatsStore = create<ArchivedChatsStore>()(
  devtools(
    immer((set, get) => ({
      ...initialState,

      loadArchivedConversations: async () => {
        set((state) => {
          state.isLoadingList = true;
          state.listError = null;
        });
        try {
          const { conversations, messagesMap, pagination } =
            await ArchivedChatsApi.fetchArchivedConversations({ page: 1, limit: ASSISTANT_CHATS_PAGE_SIZE });
          set((state) => {
            state.conversations = conversations;
            state.messagesMap = messagesMap;
            state.isLoadingList = false;
            state.assistantPagination = {
              page: pagination.page,
              totalCount: pagination.totalCount,
              hasMore: pagination.hasNextPage,
              isLoadingMore: false,
            };
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Failed to load archived chats';
          set((state) => {
            state.listError = message;
            state.isLoadingList = false;
          });
        }
      },

      loadMoreAssistantChats: async () => {
        const { assistantPagination } = get();
        if (assistantPagination.isLoadingMore || !assistantPagination.hasMore) return;

        set((state) => {
          state.assistantPagination.isLoadingMore = true;
        });

        try {
          const nextPage = assistantPagination.page + 1;
          const { conversations, messagesMap, pagination } =
            await ArchivedChatsApi.fetchArchivedConversations({ page: nextPage, limit: ASSISTANT_CHATS_PAGE_SIZE });

          set((state) => {
            // Append new conversations (deduplicate)
            const existingIds = new Set(state.conversations.map((c) => c.id));
            for (const conv of conversations) {
              if (!existingIds.has(conv.id)) {
                state.conversations.push(conv);
              }
            }
            // Merge messagesMap
            Object.assign(state.messagesMap, messagesMap);
            state.assistantPagination = {
              page: pagination.page,
              totalCount: pagination.totalCount,
              hasMore: pagination.hasNextPage,
              isLoadingMore: false,
            };
          });
        } catch {
          set((state) => {
            state.assistantPagination.isLoadingMore = false;
          });
        }
      },

      loadAgentArchivedConversations: async () => {
        set((state) => {
          state.isLoadingAgentGroups = true;
          state.agentGroupsError = null;
        });
        try {
          const [groupsRes, agentsRes] = await Promise.all([
            ArchivedChatsApi.fetchAllAgentsArchivedConversations({ agentPage: 1, agentLimit: AGENT_GROUPS_PAGE_SIZE }),
            ArchivedChatsApi.getAgents({ limit: 200 }),
          ]);

          // Build a name lookup map
          const nameMap: Record<string, string> = {};
          for (const agent of agentsRes.agents) {
            if (agent._key) nameMap[agent._key] = agent.name || agent._key;
          }

          set((state) => {
            state.agentGroups = groupsRes.groups.map((g) => ({
              ...g,
              agentName: nameMap[g.agentKey] ?? null,
              isLoadingMore: false,
            }));
            state.agentGroupsEpoch += 1;
            state.isLoadingAgentGroups = false;
            state.agentGroupsPagination = {
              page: groupsRes.agentPagination.page,
              totalCount: groupsRes.agentPagination.totalCount,
              hasMore: groupsRes.agentPagination.hasNextPage,
              isLoadingMore: false,
            };
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Failed to load agent archived chats';
          set((state) => {
            state.agentGroupsError = message;
            state.isLoadingAgentGroups = false;
          });
        }
      },

      loadMoreAgentGroups: async () => {
        const { agentGroupsPagination } = get();
        if (agentGroupsPagination.isLoadingMore || !agentGroupsPagination.hasMore) return;

        set((state) => {
          state.agentGroupsPagination.isLoadingMore = true;
        });

        try {
          const nextPage = agentGroupsPagination.page + 1;
          const [groupsRes, agentsRes] = await Promise.all([
            ArchivedChatsApi.fetchAllAgentsArchivedConversations({ agentPage: nextPage, agentLimit: AGENT_GROUPS_PAGE_SIZE }),
            ArchivedChatsApi.getAgents({ limit: 200 }),
          ]);

          const nameMap: Record<string, string> = {};
          for (const agent of agentsRes.agents) {
            if (agent._key) nameMap[agent._key] = agent.name || agent._key;
          }

          set((state) => {
            // Append new agent groups (deduplicate by agentKey)
            const existingKeys = new Set(state.agentGroups.map((g) => g.agentKey));
            for (const g of groupsRes.groups) {
              if (!existingKeys.has(g.agentKey)) {
                state.agentGroups.push({
                  ...g,
                  agentName: nameMap[g.agentKey] ?? null,
                  isLoadingMore: false,
                });
              }
            }
            state.agentGroupsPagination = {
              page: groupsRes.agentPagination.page,
              totalCount: groupsRes.agentPagination.totalCount,
              hasMore: groupsRes.agentPagination.hasNextPage,
              isLoadingMore: false,
            };
          });
        } catch {
          set((state) => {
            state.agentGroupsPagination.isLoadingMore = false;
          });
        }
      },

      loadConversation: async (id: string, title: string, agentKey?: string | null) => {
        if (get().selected?.id === id) return;

        if (agentKey) {
          // Agent conversation — fetch via AgentsApi
          set((state) => {
            state.isLoadingConversation = true;
            state.conversationError = null;
            state.selected = null;
          });
          try {
            const result = await ArchivedChatsApi.fetchAgentConversation(agentKey, id);
            const resolvedTitle = (result.conversation as any)?.title || title;
            set((state) => {
              state.selected = {
                id,
                title: resolvedTitle,
                messages: result.messages,
                agentKey,
              };
              state.isLoadingConversation = false;
            });
          } catch (err) {
            const message = err instanceof Error ? err.message : 'Failed to load conversation';
            set((state) => {
              state.conversationError = message;
              state.isLoadingConversation = false;
            });
          }
          return;
        }

        // Normal conversation — use cached messages if available
        const cachedMessages = get().messagesMap[id];
        if (cachedMessages !== undefined) {
          const resolvedTitle = get().conversations.find((c) => c.id === id)?.title || title;
          set((state) => {
            state.selected = { id, title: resolvedTitle, messages: cachedMessages, agentKey: null };
            state.isLoadingConversation = false;
            state.conversationError = null;
          });
          return;
        }

        set((state) => {
          state.isLoadingConversation = true;
          state.conversationError = null;
          state.selected = null;
        });
        try {
          const result = await ArchivedChatsApi.fetchConversation(id);
          const resolvedTitle = result.conversation?.title || title;
          set((state) => {
            state.selected = { id, title: resolvedTitle, messages: result.messages, agentKey: null };
            state.isLoadingConversation = false;
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Failed to load conversation';
          set((state) => {
            state.conversationError = message;
            state.isLoadingConversation = false;
          });
        }
      },

      loadMoreForAgent: async (agentKey: string, page: number) => {
        const group = get().agentGroups.find((g) => g.agentKey === agentKey);
        if (!group || group.isLoadingMore) return;

        set((state) => {
          const g = state.agentGroups.find((g) => g.agentKey === agentKey);
          if (g) g.isLoadingMore = true;
        });

        try {
          const result = await ArchivedChatsApi.fetchAgentArchivedConversations(agentKey, { page, limit: AGENT_CHATS_PAGE_SIZE });
          set((state) => {
            const g = state.agentGroups.find((g) => g.agentKey === agentKey);
            if (!g) return;
            const existingIds = new Set(g.conversations.map((c) => String(c.id)));
            for (const conv of result.conversations) {
              const cid = String(conv.id);
              if (!existingIds.has(cid)) {
                existingIds.add(cid);
                g.conversations.push(conv);
              }
            }
            g.pagination = result.pagination;
            g.isLoadingMore = false;
          });
        } catch {
          set((state) => {
            const g = state.agentGroups.find((g) => g.agentKey === agentKey);
            if (g) g.isLoadingMore = false;
          });
        }
      },

      removeConversation: (id: string, agentKey?: string | null) => {
        set((state) => {
          if (agentKey) {
            const group = state.agentGroups.find((g) => g.agentKey === agentKey);
            if (group) {
              group.conversations = group.conversations.filter((c) => c.id !== id);
              group.pagination.totalCount = Math.max(0, group.pagination.totalCount - 1);
            }
          } else {
            state.conversations = state.conversations.filter((c) => c.id !== id);
            state.assistantPagination.totalCount = Math.max(0, state.assistantPagination.totalCount - 1);
          }
          if (state.selected?.id === id) {
            state.selected = null;
          }
        });
      },

      clearSelection: () => {
        set((state) => {
          state.selected = null;
          state.conversationError = null;
        });
      },
    })),
    { name: 'archived-chats-store' }
  )
);

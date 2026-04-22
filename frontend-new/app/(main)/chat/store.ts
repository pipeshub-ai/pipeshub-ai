import { create } from 'zustand';
import { debugLog } from './debug-logger';
import {
  Conversation,
  ChatMode,
  QueryMode,
  AgentStrategy,
  ChatSettings,
  ConversationsListResponse,
  ChatSlot,
  MAX_SLOTS,
  SearchResultItem
} from './types';
import type { RecordDetailsResponse } from '@/knowledge-base/types';
import type { PreviewCitation } from '@/app/components/file-preview/types';
import type { AgentSidebarRowMenuAccess } from './sidebar/agent-sidebar-row-access';

/**
 * File preview state for citation preview in chat.
 */
export interface ChatPreviewFile {
  id: string;
  name: string;
  url: string;
  /**
   * Raw Blob for the streamed file. Populated only for renderers that are
   * better fed the binary data directly (DOCX via `docx-preview`) — avoids
   * the extra `URL.createObjectURL` + re-`fetch` roundtrip that was causing
   * the DOCX preview pane to stay blank.
   */
  blob?: Blob;
  type: string;
  size?: number;
  isLoading?: boolean;
  error?: string;
  recordDetails?: RecordDetailsResponse;
  /** Initial page to navigate to (from citation pageNum) */
  initialPage?: number;
  /** Bounding box for highlighting the cited region (normalized 0-1 coordinates) */
  highlightBox?: Array<{ x: number; y: number }>;
  /** Citations for the previewed record (used by the CitationsPanel) */
  citations?: PreviewCitation[];
  /**
   * Citation id the user actually clicked (from citation.citationId).
   * Used to seed the CitationsPanel so clicking `[2]` highlights citation [2],
   * not the first citation on the target page.
   */
  initialCitationId?: string;
}

/**
 * Temporary sidebar entry created when a new chat stream begins.
 *
 * Lifecycle:
 * 1. User sends message → addPendingConversation() creates entry with
 *    isGenerating=true, a timestamp ID, and title=null.
 * 2. Sidebar renders a shimmering "Generating Title…" item.
 * 3. SSE `complete` event arrives → resolvePendingConversation() fills in
 *    id, title, etc. from the backend response. isGenerating flips to false.
 * 4. The conversation is prepended to the `conversations` array (Your Chats),
 *    and `pendingConversation` is cleared.
 *
 * NOTE: The pending item is ALWAYS pushed into "Your Chats" (time-grouped).
 * TODO: Handle Shared Chats — if the backend returns isShared=true on
 *       the complete event, we may need to move it to sharedConversations.
 */
export interface PendingConversation {
  /** The slotId that owns this pending entry — used for sidebar click-to-switch */
  slotId: string;
  /** Null while generating; filled from SSE complete event */
  title: string | null;
  /** True while the SSE stream is in progress and title hasn't arrived */
  isGenerating: boolean;
  /** Epoch ms — used for time-group bucketing (always "Today") */
  createdAt: number;
}

// ── Helper: create a default empty slot ─────────────────────────────

function createDefaultSlot(convId: string | null): ChatSlot {
  const isNew = convId === null;
  return {
    convId,
    threadAgentId: null,
    agentStreamTools: null,
    isTemp: isNew,
    isInitialized: isNew,      // new chats have nothing to load
    hasLoaded: false,
    messages: [],
    isStreaming: false,
    streamingContent: '',
    streamingQuestion: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    userScrollOverride: false,
    savedScrollTop: null,
    savedScrollWasStreaming: false,
    activeExpandedMessageId: null,
    regenerateMessageId: null,
    pendingCollections: [],
    artifacts: [],
    abortController: null,
    lastAccessedAt: Date.now(),
  };
}

// ── Helper: generate a stable slot ID ───────────────────────────────

let slotCounter = 0;
function generateSlotId(): string {
  slotCounter += 1;
  return `slot-${Date.now()}-${slotCounter}`;
}

// ── Store interface ─────────────────────────────────────────────────

interface ChatState {
  // ── Slot dictionary ──
  slots: Record<string, ChatSlot>;
  activeSlotId: string | null;

  // ── URL sync ──
  hasConsumedUrlNavigation: boolean;

  // ── Sidebar state (global, not per-slot) ──
  conversations: Conversation[];
  sharedConversations: Conversation[];
  isConversationsLoading: boolean;
  conversationsError: string | null;
  pagination: ConversationsListResponse['pagination'] | null;
  pendingConversations: Record<string, PendingConversation>;
  /** IDs of conversations that just resolved from pending — triggers typing animation */
  newlyResolvedIds: Set<string>;
  isMoreChatsPanelOpen: boolean;
  moreChatsSectionType: 'shared' | 'your' | null;
  /** Agents browser panel (same shell as More Chats) */
  isAgentsSidebarOpen: boolean;
  /** Pagination state for the More Chats infinite scroll panel */
  moreChatsPagination: { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null;
  /** Bumped after a mutation (rename/delete/archive) to trigger sidebar refetch */
  conversationsVersion: number;

  /** When set, agent sidebar lists + streaming prepends apply to this agent */
  agentSidebarAgentId: string | null;
  agentConversations: Conversation[];
  agentConversationsPagination: ConversationsListResponse['pagination'] | null;
  isAgentConversationsLoading: boolean;
  agentConversationsError: string | null;
  isAgentMoreChatsPanelOpen: boolean;
  agentMoreChatsPagination: { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null;
  /** Tool fullNames for agent SSE payloads (from GET /agents/:id) */
  agentStreamTools: string[];
  /** Resolved agent name for the top chat header when `agentId` is in the URL */
  agentContextDisplayName: string | null;
  /** Access flags (canEdit / showViewAgent / …) for the agent in context — drives the chat header menu */
  agentContextAccess: AgentSidebarRowMenuAccess | null;

  // ── Global settings (apply to all chats) ──
  settings: ChatSettings;

  // ── File preview (global — only one preview open at a time) ──
  previewFile: ChatPreviewFile | null;
  previewMode: 'sidebar' | 'fullscreen';

  // ── Expansion panel (global — applies to the active chat input) ──
  expansionViewMode: 'inline' | 'overlay';

  // ── Cache ──
  collectionNamesCache: Record<string, string>;

  // ── Search state ──
  searchResults: SearchResultItem[];
  searchQuery: string;
  searchId: string | null;
  isSearching: boolean;
  searchError: string | null;

  // ── Slot actions ──
  /** Create a new slot. Returns the generated slotId. */
  createSlot: (convId: string | null) => string;
  /** Patch fields in a single slot. Only that slot's reference changes. */
  updateSlot: (slotId: string, patch: Partial<ChatSlot>) => void;
  /** Switch the active slot. Updates lastAccessedAt on the target. */
  setActiveSlot: (slotId: string) => void;
  /** Remove a slot from the dictionary entirely. */
  evictSlot: (slotId: string) => void;
  /** Assign a real convId to a temp slot (after server responds). */
  resolveSlotConvId: (slotId: string, realConvId: string) => void;
  /** Find a slot by its convId. O(n) scan but n ≤ MAX_SLOTS. */
  getSlotByConvId: (
    convId: string,
    opts?: { forAgentId: string | null }
  ) => { slotId: string; slot: ChatSlot } | null;
  /**
   * Clear the visible thread only. In-flight SSE for that slot (and all other
   * slots) keeps running — use this for New Chat / parallel conversations.
   */
  clearActiveSlot: () => void;

  // ── Sidebar actions ──
  setConversations: (conversations: Conversation[]) => void;
  setSharedConversations: (conversations: Conversation[]) => void;
  setIsConversationsLoading: (loading: boolean) => void;
  setConversationsError: (error: string | null) => void;
  setPagination: (pagination: ConversationsListResponse['pagination'] | null) => void;
  toggleMoreChatsPanel: (sectionType: 'shared' | 'your') => void;
  closeMoreChatsPanel: () => void;
  toggleAgentsSidebar: () => void;
  closeAgentsSidebar: () => void;
  setMoreChatsPagination: (p: { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null) => void;
  appendConversations: (convs: Conversation[]) => void;
  appendSharedConversations: (convs: Conversation[]) => void;
  moveConversationToTop: (conversationId: string) => void;
  removeConversation: (conversationId: string) => void;
  renameConversation: (conversationId: string, newTitle: string) => void;
  /** Bump the version counter to trigger a sidebar refetch */
  bumpConversationsVersion: () => void;

  setAgentSidebarAgentId: (id: string | null) => void;
  setAgentConversations: (conversations: Conversation[]) => void;
  setAgentConversationsPagination: (pagination: ConversationsListResponse['pagination'] | null) => void;
  setIsAgentConversationsLoading: (loading: boolean) => void;
  setAgentConversationsError: (error: string | null) => void;
  toggleAgentMoreChatsPanel: () => void;
  closeAgentMoreChatsPanel: () => void;
  setAgentMoreChatsPagination: (p: { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null) => void;
  appendAgentConversations: (convs: Conversation[]) => void;
  setAgentStreamTools: (tools: string[]) => void;
  setAgentContextDisplayName: (name: string | null) => void;
  setAgentContextAccess: (access: AgentSidebarRowMenuAccess | null) => void;

  addPendingConversation: (slotId: string) => void;
  clearNewlyResolvedId: (conversationId: string) => void;
  resolvePendingConversation: (
    slotId: string,
    conversation: Conversation,
    options?: { isAgentStream?: boolean }
  ) => void;
  clearPendingConversation: (slotId: string) => void;

  // ── URL sync actions ──
  setHasConsumedUrlNavigation: (consumed: boolean) => void;

  // ── Preview actions ──
  setPreviewFile: (file: ChatPreviewFile | null) => void;
  setPreviewMode: (mode: 'sidebar' | 'fullscreen') => void;
  clearPreview: () => void;

  // ── Settings actions ──
  setMode: (mode: ChatMode) => void;
  setQueryMode: (queryMode: QueryMode) => void;
  setAgentStrategy: (agentStrategy: AgentStrategy) => void;
  setFilters: (filters: { apps: string[]; kb: string[] }) => void;
  setExpansionViewMode: (mode: 'inline' | 'overlay') => void;
  setSelectedModelForCtx: (ctxKey: string, model: import('./types').ModelOverride | null) => void;
  setDefaultModelForCtx: (ctxKey: string, model: import('./types').ModelOverride | null) => void;
  setAvailableModelsForCtx: (ctxKey: string, models: import('./types').AvailableLlmModel[]) => void;

  // ── Search actions ──
  setSearchResults: (results: SearchResultItem[], searchId: string, query: string) => void;
  setIsSearching: (loading: boolean) => void;
  setSearchError: (error: string | null) => void;
  clearSearchResults: () => void;

  // ── Cache actions ──
  setCollectionNamesCache: (cache: Record<string, string>) => void;

  // ── Global reset ──
  reset: () => void;
}

// ── Initial state ───────────────────────────────────────────────────

const initialState = {
  slots: {} as Record<string, ChatSlot>,
  activeSlotId: null as string | null,
  hasConsumedUrlNavigation: false,

  conversations: [] as Conversation[],
  sharedConversations: [] as Conversation[],
  isConversationsLoading: false,
  conversationsError: null as string | null,
  pagination: null as ConversationsListResponse['pagination'] | null,
  pendingConversations: {} as Record<string, PendingConversation>,
  newlyResolvedIds: new Set<string>(),
  isMoreChatsPanelOpen: false,
  moreChatsSectionType: null as 'shared' | 'your' | null,
  isAgentsSidebarOpen: false,
  moreChatsPagination: null as { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null,
  conversationsVersion: 0,

  agentSidebarAgentId: null as string | null,
  agentConversations: [] as Conversation[],
  agentConversationsPagination: null as ConversationsListResponse['pagination'] | null,
  isAgentConversationsLoading: false,
  agentConversationsError: null as string | null,
  isAgentMoreChatsPanelOpen: false,
  agentMoreChatsPagination: null as { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null,
  agentStreamTools: [] as string[],
  agentContextDisplayName: null as string | null,
  agentContextAccess: null as AgentSidebarRowMenuAccess | null,

  settings: {
    mode: 'chat' as ChatMode,
    queryMode: 'agent' as QueryMode,
    agentStrategy: 'auto' as AgentStrategy,
    filters: {
      apps: [] as string[],
      kb: [] as string[],
    },
    selectedModels: {} as Record<string, import('./types').ModelOverride | null>,
    defaultModels: {} as Record<string, import('./types').ModelOverride | null>,
    availableModels: {} as Record<string, { models: import('./types').AvailableLlmModel[]; fetchedAt: number }>,
  },

  previewFile: null as ChatPreviewFile | null,
  previewMode: 'sidebar' as 'sidebar' | 'fullscreen',
  expansionViewMode: 'inline' as 'inline' | 'overlay',
  collectionNamesCache: {} as Record<string, string>,

  searchResults: [] as SearchResultItem[],
  searchQuery: '' as string,
  searchId: null as string | null,
  isSearching: false,
  searchError: null as string | null,
};

// ── Store creation ──────────────────────────────────────────────────

export const useChatStore = create<ChatState>((set, get) => ({
  ...initialState,

  // ── Slot actions ─────────────────────────────────────────────────

  createSlot: (convId) => {
    const slotId = generateSlotId();
    const slot = createDefaultSlot(convId);

    set((state) => {
      let newSlots: Record<string, ChatSlot> = { ...state.slots, [slotId]: slot };

      const evictOne = (): boolean => {
        const ids = Object.keys(newSlots);
        if (ids.length <= MAX_SLOTS) return false;
        const pool = ids.filter((id) => id !== slotId && id !== state.activeSlotId);
        if (pool.length === 0) return false;
        const idleFirst = pool.filter((id) => !newSlots[id].isStreaming);
        const candidates = idleFirst.length > 0 ? idleFirst : pool;
        let victim: string | null = null;
        let lruTime = Infinity;
        for (const id of candidates) {
          const t = newSlots[id].lastAccessedAt;
          if (t < lruTime) {
            lruTime = t;
            victim = id;
          }
        }
        if (!victim) return false;
        newSlots[victim]?.abortController?.abort();
        const { [victim]: _, ...rest } = newSlots;
        newSlots = rest;
        return true;
      };

      while (Object.keys(newSlots).length > MAX_SLOTS) {
        if (!evictOne()) break;
      }

      return { slots: newSlots };
    });

    return slotId;
  },

  updateSlot: (slotId, patch) => {
    set((state) => {
      const existing = state.slots[slotId];
      if (!existing) return state;
      return {
        slots: {
          ...state.slots,
          [slotId]: { ...existing, ...patch },
        },
      };
    });
  },

  setActiveSlot: (slotId) => {
    set((state) => {
      const existing = state.slots[slotId];
      if (!existing) return state;
      return {
        activeSlotId: slotId,
        slots: {
          ...state.slots,
          [slotId]: { ...existing, lastAccessedAt: Date.now() },
        },
      };
    });
  },

  evictSlot: (slotId) => {
    set((state) => {
      const slot = state.slots[slotId];
      if (!slot) return state;
      // Abort any in-flight stream
      slot.abortController?.abort();
      const newSlots = { ...state.slots };
      delete newSlots[slotId];
      const hadPending = Object.prototype.hasOwnProperty.call(state.pendingConversations, slotId);
      const { [slotId]: _pending, ...pendingRest } = state.pendingConversations;
      return {
        slots: newSlots,
        activeSlotId: state.activeSlotId === slotId ? null : state.activeSlotId,
        pendingConversations: hadPending ? pendingRest : state.pendingConversations,
      };
    });
  },

  resolveSlotConvId: (slotId, realConvId) => {
    set((state) => {
      const existing = state.slots[slotId];
      if (!existing) return state;
      return {
        slots: {
          ...state.slots,
          [slotId]: { ...existing, convId: realConvId, isTemp: false },
        },
      };
    });
  },

  getSlotByConvId: (convId, opts) => {
    const { slots } = get();
    for (const [slotId, slot] of Object.entries(slots)) {
      if (slot.convId !== convId) continue;
      if (opts === undefined) {
        return { slotId, slot };
      }
      const want = opts.forAgentId;
      if (want == null || want === '') {
        if (!slot.threadAgentId) return { slotId, slot };
      } else if (slot.threadAgentId === want) {
        return { slotId, slot };
      }
    }
    return null;
  },

  clearActiveSlot: () => set({ activeSlotId: null }),

  // ── Sidebar actions ──────────────────────────────────────────────

  setConversations: (conversations) => set({ conversations }),

  setSharedConversations: (conversations) => set({ sharedConversations: conversations }),

  setIsConversationsLoading: (loading) => set({ isConversationsLoading: loading }),

  setConversationsError: (error) => set({ conversationsError: error }),

  setPagination: (pagination) => set({ pagination }),

  toggleMoreChatsPanel: (sectionType) =>
    set((state) => {
      if (state.isMoreChatsPanelOpen && state.moreChatsSectionType === sectionType) {
        return { isMoreChatsPanelOpen: false, moreChatsSectionType: null };
      }
      // Reset pagination each time the panel opens so it starts fresh
      return {
        isMoreChatsPanelOpen: true,
        moreChatsSectionType: sectionType,
        moreChatsPagination: null,
        isAgentsSidebarOpen: false,
        isAgentMoreChatsPanelOpen: false,
        agentMoreChatsPagination: null,
      };
    }),

  closeMoreChatsPanel: () =>
    set({ isMoreChatsPanelOpen: false, moreChatsSectionType: null }),

  toggleAgentsSidebar: () =>
    set((state) => {
      if (state.isAgentsSidebarOpen) {
        return { isAgentsSidebarOpen: false };
      }
      return {
        isAgentsSidebarOpen: true,
        isMoreChatsPanelOpen: false,
        moreChatsSectionType: null,
        moreChatsPagination: null,
        isAgentMoreChatsPanelOpen: false,
        agentMoreChatsPagination: null,
      };
    }),

  closeAgentsSidebar: () => set({ isAgentsSidebarOpen: false }),

  setAgentSidebarAgentId: (id) =>
    set((state) => {
      if (id === null) {
        return {
          agentSidebarAgentId: null,
          agentConversations: [],
          agentConversationsPagination: null,
          agentConversationsError: null,
          isAgentMoreChatsPanelOpen: false,
          agentMoreChatsPagination: null,
          agentStreamTools: [],
          agentContextDisplayName: null,
          agentContextAccess: null,
          isAgentsSidebarOpen: false,
        };
      }
      if (state.agentSidebarAgentId === id) {
        return state.isAgentsSidebarOpen ? { isAgentsSidebarOpen: false } : state;
      }
      return {
        agentSidebarAgentId: id,
        agentConversations: [],
        agentConversationsPagination: null,
        agentConversationsError: null,
        isAgentMoreChatsPanelOpen: false,
        agentMoreChatsPagination: null,
        agentStreamTools: [],
        agentContextDisplayName: null,
        agentContextAccess: null,
        isAgentsSidebarOpen: false,
      };
    }),

  setAgentConversations: (conversations) => set({ agentConversations: conversations }),

  setAgentConversationsPagination: (pagination) => set({ agentConversationsPagination: pagination }),

  setIsAgentConversationsLoading: (loading) => set({ isAgentConversationsLoading: loading }),

  setAgentConversationsError: (error) => set({ agentConversationsError: error }),

  toggleAgentMoreChatsPanel: () =>
    set((state) => {
      if (state.isAgentMoreChatsPanelOpen) {
        return { isAgentMoreChatsPanelOpen: false, agentMoreChatsPagination: null };
      }
      return {
        isAgentMoreChatsPanelOpen: true,
        agentMoreChatsPagination: null,
        isMoreChatsPanelOpen: false,
        moreChatsSectionType: null,
        moreChatsPagination: null,
        isAgentsSidebarOpen: false,
      };
    }),

  closeAgentMoreChatsPanel: () =>
    set({ isAgentMoreChatsPanelOpen: false, agentMoreChatsPagination: null }),

  setAgentMoreChatsPagination: (p) => set({ agentMoreChatsPagination: p }),

  setMoreChatsPagination: (p) => set({ moreChatsPagination: p }),

  appendConversations: (convs) =>
    set((state) => {
      const existingIds = new Set(state.conversations.map((c) => c.id));
      const newOnes = convs.filter((c) => !existingIds.has(c.id));
      return { conversations: [...state.conversations, ...newOnes] };
    }),

  appendSharedConversations: (convs) =>
    set((state) => {
      const existingIds = new Set(state.sharedConversations.map((c) => c.id));
      const newOnes = convs.filter((c) => !existingIds.has(c.id));
      return { sharedConversations: [...state.sharedConversations, ...newOnes] };
    }),

  appendAgentConversations: (convs) =>
    set((state) => {
      const existingIds = new Set(state.agentConversations.map((c) => c.id));
      const newOnes = convs.filter((c) => !existingIds.has(c.id));
      return { agentConversations: [...state.agentConversations, ...newOnes] };
    }),

  setAgentStreamTools: (tools) => set({ agentStreamTools: tools }),

  setAgentContextDisplayName: (name) => set({ agentContextDisplayName: name }),

  setAgentContextAccess: (access) => set({ agentContextAccess: access }),

  moveConversationToTop: (conversationId) =>
    set((state) => {
      let next = state.conversations;
      const idx = state.conversations.findIndex((c) => c.id === conversationId);
      if (idx > 0) {
        const conv = state.conversations[idx];
        next = [conv, ...state.conversations.slice(0, idx), ...state.conversations.slice(idx + 1)];
      }
      let nextAgent = state.agentConversations;
      const aidx = state.agentConversations.findIndex((c) => c.id === conversationId);
      if (aidx > 0) {
        const conv = state.agentConversations[aidx];
        nextAgent = [conv, ...state.agentConversations.slice(0, aidx), ...state.agentConversations.slice(aidx + 1)];
      }
      if (next === state.conversations && nextAgent === state.agentConversations) return state;
      return { conversations: next, agentConversations: nextAgent };
    }),

  removeConversation: (conversationId) =>
    set((state) => ({
      conversations: state.conversations.filter((c) => c.id !== conversationId),
      sharedConversations: state.sharedConversations.filter((c) => c.id !== conversationId),
      agentConversations: state.agentConversations.filter((c) => c.id !== conversationId),
    })),

  renameConversation: (conversationId, newTitle) =>
    set((state) => ({
      conversations: state.conversations.map((c) =>
        c.id === conversationId ? { ...c, title: newTitle } : c
      ),
      sharedConversations: state.sharedConversations.map((c) =>
        c.id === conversationId ? { ...c, title: newTitle } : c
      ),
      agentConversations: state.agentConversations.map((c) =>
        c.id === conversationId ? { ...c, title: newTitle } : c
      ),
    })),

  bumpConversationsVersion: () =>
    set((state) => ({ conversationsVersion: state.conversationsVersion + 1 })),

  addPendingConversation: (slotId) =>
    set((state) => ({
      pendingConversations: {
        ...state.pendingConversations,
        [slotId]: {
          slotId,
          title: null,
          isGenerating: true,
          createdAt: Date.now(),
        },
      },
    })),

  resolvePendingConversation: (slotId, conversation, options) =>
    set((state) => {
      const { [slotId]: _removed, ...remaining } = state.pendingConversations;
      const slotForPending = state.slots[slotId];
      const isAgentStream =
        options?.isAgentStream ??
        Boolean(slotForPending?.threadAgentId);
      const nextMain = isAgentStream
        ? state.conversations
        : [conversation, ...state.conversations.filter((c) => c.id !== conversation.id)];
      const nextAgent = isAgentStream
        ? [conversation, ...state.agentConversations.filter((c) => c.id !== conversation.id)]
        : state.agentConversations;
      const nextNewlyResolved = new Set(state.newlyResolvedIds);
      nextNewlyResolved.add(conversation.id);
      return {
        pendingConversations: remaining,
        conversations: nextMain,
        agentConversations: nextAgent,
        newlyResolvedIds: nextNewlyResolved,
      };
    }),

  clearNewlyResolvedId: (conversationId) =>
    set((state) => {
      if (!state.newlyResolvedIds.has(conversationId)) return state;
      const next = new Set(state.newlyResolvedIds);
      next.delete(conversationId);
      return { newlyResolvedIds: next };
    }),

  clearPendingConversation: (slotId) =>
    set((state) => {
      const { [slotId]: _removed, ...remaining } = state.pendingConversations;
      return { pendingConversations: remaining };
    }),

  // ── URL sync ─────────────────────────────────────────────────────

  setHasConsumedUrlNavigation: (consumed) => set({ hasConsumedUrlNavigation: consumed }),



  // ── Settings actions ─────────────────────────────────────────────

  setPreviewFile: (file) => set({ previewFile: file }),

  setPreviewMode: (mode) => set({ previewMode: mode }),

  setExpansionViewMode: (mode) => set({ expansionViewMode: mode }),

  clearPreview: () =>
    set((state) => {
      // Revoke blob URL if present
      if (state.previewFile?.url?.startsWith('blob:')) {
        URL.revokeObjectURL(state.previewFile.url);
      }
      return { previewFile: null, previewMode: 'sidebar' };
    }),

  setMode: (mode) => set((state) => ({
    settings: { ...state.settings, mode },
  })),

  setQueryMode: (queryMode) => set((state) => ({
    settings: { ...state.settings, queryMode },
  })),

  setAgentStrategy: (agentStrategy) => set((state) => ({
    settings: { ...state.settings, agentStrategy },
  })),

  setFilters: (filters) => set((state) => ({
    settings: { ...state.settings, filters },
  })),

  setSelectedModelForCtx: (ctxKey, model) => set((state) => ({
    settings: {
      ...state.settings,
      selectedModels: { ...state.settings.selectedModels, [ctxKey]: model },
    },
  })),

  setDefaultModelForCtx: (ctxKey, model) => set((state) => ({
    settings: {
      ...state.settings,
      defaultModels: { ...state.settings.defaultModels, [ctxKey]: model },
    },
  })),

  setAvailableModelsForCtx: (ctxKey, models) => set((state) => ({
    settings: {
      ...state.settings,
      availableModels: {
        ...state.settings.availableModels,
        [ctxKey]: { models, fetchedAt: Date.now() },
      },
    },
  })),

  // ── Search actions ──────────────────────────────────────────────

  setSearchResults: (results, searchId, query) =>
    set({ searchResults: results, searchId, searchQuery: query }),

  setIsSearching: (loading) => set({ isSearching: loading }),

  setSearchError: (error) => set({ searchError: error }),

  clearSearchResults: () =>
    set({ searchResults: [], searchQuery: '', searchId: null, searchError: null }),

  // ── Cache ────────────────────────────────────────────────────────

  setCollectionNamesCache: (cache) =>
    set((state) => ({
      collectionNamesCache: { ...state.collectionNamesCache, ...cache },
    })),

  // ── Reset ────────────────────────────────────────────────────────

  reset: () => {
    // Abort all in-flight streams before resetting
    const { slots } = get();
    for (const slot of Object.values(slots)) {
      slot.abortController?.abort();
    }
    set(initialState);
  },
}));

/**
 * Sentinel context key for the non-agent (Assistant) chat. Using a Unicode
 * private-use sentinel avoids collision with any real agent id.
 */
export const ASSISTANT_CTX = '__assistant__';

/** Build the context key from an (effective) agent id or null. */
export const ctxKeyFromAgent = (agentId: string | null | undefined): string =>
  agentId && agentId.trim() ? agentId : ASSISTANT_CTX;

/**
 * Resolve the model that should be used for `ctxKey`: the user's selection
 * if present, else the context default. Reads the current store snapshot.
 */
export function getEffectiveModel(
  ctxKey: string,
): import('./types').ModelOverride | null {
  const { selectedModels, defaultModels } = useChatStore.getState().settings;
  return selectedModels[ctxKey] ?? defaultModels[ctxKey] ?? null;
}

// ── Store-write diff subscriber (debug only) ────────────────────
// Logs which top-level fields changed per set() call. This lets us
// correlate store writes with component re-renders in the debug output.
if (typeof window !== 'undefined') {
  // debugLog imported at top of file
  const trackedFields = [
    'slots', 'activeSlotId', 'conversations', 'sharedConversations',
    'isConversationsLoading', 'conversationsError', 'pagination',
    'pendingConversations', 'isMoreChatsPanelOpen', 'moreChatsSectionType', 'isAgentsSidebarOpen', 'moreChatsPagination',
    'agentSidebarAgentId', 'agentConversations', 'agentConversationsPagination',
    'isAgentConversationsLoading', 'agentConversationsError', 'isAgentMoreChatsPanelOpen', 'agentMoreChatsPagination',
    'agentStreamTools', 'agentContextDisplayName', 'agentContextAccess',
    'settings', 'previewFile', 'previewMode', 'expansionViewMode',
    'collectionNamesCache', 'conversationsVersion',
    'searchResults', 'searchQuery', 'searchId', 'isSearching', 'searchError',
  ] as const;

  useChatStore.subscribe((state, prev) => {
    const changed: string[] = [];
    for (const field of trackedFields) {
      if (!Object.is((state as unknown as Record<string, unknown>)[field], (prev as unknown as Record<string, unknown>)[field])) {
        changed.push(field);
      }
    }
    if (changed.length > 0) {
      debugLog.storeWrite(changed);
    }
  });
}

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { Conversation, ConversationMessage } from './types';
import { ArchivedChatsApi } from './api';

// ======================================================
// Types
// ======================================================

interface SelectedConversationState {
  id: string;
  title: string;
  messages: ConversationMessage[];
}

interface ArchivedChatsState {
  // -- List --
  conversations: Conversation[];
  isLoadingList: boolean;
  listError: string | null;

  /**
   * Messages extracted from the archived list response, keyed by conversation ID.
   * Populated by loadArchivedConversations so that loadConversation doesn't need
   * a second API call.
   */
  messagesMap: Record<string, ConversationMessage[]>;

  // -- Selected conversation --
  selected: SelectedConversationState | null;
  isLoadingConversation: boolean;
  conversationError: string | null;
}

interface ArchivedChatsActions {
  /** Fetch the full list of archived conversations. */
  loadArchivedConversations: () => Promise<void>;

  /**
   * Load a single conversation's messages for display.
   * Clears the current selection first if a different conv is picked.
   */
  loadConversation: (id: string, title: string) => Promise<void>;

  /** Remove a conversation from the list (after restore or permanent delete). */
  removeConversation: (id: string) => void;

  /** Clear the selected conversation state. */
  clearSelection: () => void;
}

type ArchivedChatsStore = ArchivedChatsState & ArchivedChatsActions;

// ======================================================
// Initial state
// ======================================================

const initialState: ArchivedChatsState = {
  conversations: [],
  isLoadingList: false,
  listError: null,
  messagesMap: {},
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
          const { conversations, messagesMap } = await ArchivedChatsApi.fetchArchivedConversations();
          set((state) => {
            state.conversations = conversations;
            state.messagesMap = messagesMap;
            state.isLoadingList = false;
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Failed to load archived chats';
          set((state) => {
            state.listError = message;
            state.isLoadingList = false;
          });
        }
      },

      loadConversation: async (id: string, title: string) => {
        // Skip if this conversation is already loaded
        if (get().selected?.id === id) return;

        // The archived list endpoint already embeds messages — use them directly
        // to avoid a second round-trip.
        const cachedMessages = get().messagesMap[id];
        if (cachedMessages !== undefined) {
          const resolvedTitle =
            get().conversations.find((c) => c.id === id)?.title || title;
          set((state) => {
            state.selected = { id, title: resolvedTitle, messages: cachedMessages };
            state.isLoadingConversation = false;
            state.conversationError = null;
          });
          return;
        }

        // Fallback: fetch individually if somehow not in the map
        set((state) => {
          state.isLoadingConversation = true;
          state.conversationError = null;
          state.selected = null;
        });
        try {
          const result = await ArchivedChatsApi.fetchConversation(id);
          const resolvedTitle = result.conversation?.title || title;
          set((state) => {
            state.selected = { id, title: resolvedTitle, messages: result.messages };
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

      removeConversation: (id: string) => {
        set((state) => {
          state.conversations = state.conversations.filter((c) => c.id !== id);
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

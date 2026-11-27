'use client';

import { create } from 'zustand';
import { isGoneError } from '@/lib/api/api-error';
import { JobsApi, type SessionRoute } from '@/lib/harness-jobs/jobs-api';
import type { ConversationGrants } from '@/lib/contracts/harness/generated/ext/pipeshub/api/conversation-grants';

export type ChatGrant = ConversationGrants['grants'][number];

export const CHAT_GRANTS_BUTTON_ID = 'chat-grants-button';
export const CHAT_GRANTS_PANEL_ID = 'chat-grants-panel';

export type RevokeOutcome = 'removed' | 'failed';

interface ChatGrantsState {
  /** What each chat holds, by conversation id; absent: not read, or not this person's to read (owner only). */
  byChat: Record<string, ChatGrant[]>;
  /** The grant being taken back, by conversation id. */
  removing: Record<string, string | null>;
  /** The chat whose list is open; null: closed. Never remembered across chats. */
  openFor: string | null;
  load: (session: SessionRoute) => Promise<void>;
  revoke: (session: SessionRoute, grantId: string) => Promise<RevokeOutcome>;
  open: (conversationId: string) => void;
  close: () => void;
}

/**
 * Bumped by every write, so a read that started before a revoke cannot put the revoked grant back when it lands
 * after it.
 */
const generation: Record<string, number> = {};
const bump = (id: string) => (generation[id] = (generation[id] ?? 0) + 1);

/**
 * "Permissions in this chat" (ADR-GRANT-01 D8): what "Allow for this chat" allows in the chat on screen, shared by
 * its top-bar button and its list so both change together. Both routes are the owner's alone: for anyone else the
 * read answers 404 and the chat has no entry, so nothing is shown.
 */
export const useChatGrantsStore = create<ChatGrantsState>((set, get) => ({
  byChat: {},
  removing: {},
  openFor: null,

  load: async (session) => {
    const id = session.sessionId;
    const at = generation[id] ?? 0;
    try {
      const { grants } = await JobsApi.grants(session);
      if ((generation[id] ?? 0) !== at) return;
      set((s) => ({ byChat: { ...s.byChat, [id]: grants } }));
    } catch {
      if ((generation[id] ?? 0) !== at) return;
      set((s) => {
        const { [id]: _gone, ...rest } = s.byChat;
        return { byChat: rest };
      });
    }
  },

  revoke: async (session, grantId) => {
    const id = session.sessionId;
    set((s) => ({ removing: { ...s.removing, [id]: grantId } }));
    let outcome: RevokeOutcome;
    try {
      await JobsApi.revokeGrant(session, grantId);
      outcome = 'removed';
    } catch (error) {
      // Already taken back (another tab, another device): the row is stale and what it said is already true.
      outcome = isGoneError(error) ? 'removed' : 'failed';
    }
    if (outcome === 'removed') {
      bump(id);
      set((s) => ({ byChat: { ...s.byChat, [id]: (s.byChat[id] ?? []).filter((g) => g.grantId !== grantId) } }));
    }
    set((s) => ({ removing: { ...s.removing, [id]: null } }));
    if (outcome === 'removed') void get().load(session);
    return outcome;
  },

  open: (conversationId) => set({ openFor: conversationId }),
  close: () => set({ openFor: null }),
}));

/** Focus goes back to the top-bar button when the list closes. */
export function focusChatGrantsButton(): void {
  if (typeof document === 'undefined') return;
  document.getElementById(CHAT_GRANTS_BUTTON_ID)?.focus();
}

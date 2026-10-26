'use client';

import { create } from 'zustand';
import type { AttachmentRef, ChatSettings } from '@/chat/types';

// ─────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────

/** A node of the knowledge hierarchy to scope the chat to. */
export interface PendingChatNode {
  id: string;
  name: string;
  nodeType: string;
  connector: string;
}

/**
 * Page-specific context that the host page can attach to a pending chat.
 * Designed to be extensible — each page populates the fields it cares about.
 */
export interface ChatWidgetPageContext {
  /** Collections to scope the chat to (KB IDs + display names) */
  collections?: Array<{ id: string; name: string }>;
  /** Rows ticked on the host page; they replace what the composer had selected. */
  selectedNodes?: PendingChatNode[];
  /** Specific record IDs selected by the user */
  selectedRecordIds?: string[];
  /** Human-readable source label (e.g., "Engineering" collection) */
  sourceLabel?: string;
}

/**
 * One-shot transfer buffer between the page hosting the chat widget
 * and the chat page. Set before navigation, consumed on chat page mount.
 *
 * Attachments are uploaded by the widget composer at the moment the user
 * adds them (not at navigation time), so by the time this context is set
 * every entry in `attachments` is already a server-assigned `AttachmentRef`
 * ready to be forwarded to the runtime.
 */
export interface PendingChatContext {
  /** The user's message text; empty when the page hands over only a selection. */
  message: string;
  /** Server-assigned refs for attachments uploaded by the widget. */
  attachments?: AttachmentRef[];
  /** Chat settings snapshot (mode, queryMode, agentStrategy) */
  settings?: Partial<ChatSettings>;
  /** Page-specific context (collections, selected records, etc.) */
  pageContext: ChatWidgetPageContext;
  /** The route the user navigated from (e.g. '/knowledge-base') */
  referrerPage: string;
}

// ─────────────────────────────────────────────────────────
// Store
// ─────────────────────────────────────────────────────────

interface PendingChatStore {
  pending: PendingChatContext | null;

  /** Set the pending context before navigating to /chat */
  setPending: (ctx: PendingChatContext) => void;

  /**
   * Atomically read and clear the pending context.
   * Returns the context if one was set, or null.
   */
  consumePending: () => PendingChatContext | null;
}

export const usePendingChatStore = create<PendingChatStore>((set, get) => ({
  pending: null,

  setPending: (ctx) => set({ pending: ctx }),

  consumePending: () => {
    const current = get().pending;
    if (current) {
      set({ pending: null });
    }
    return current;
  },
}));

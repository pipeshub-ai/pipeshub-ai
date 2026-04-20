'use client';

import { create } from 'zustand';
import type { UploadedFile, ChatSettings } from '@/chat/types';

// ─────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────

/**
 * Page-specific context that the host page can attach to a pending chat.
 * Designed to be extensible — each page populates the fields it cares about.
 */
export interface ChatWidgetPageContext {
  /** Collections to scope the chat to (KB IDs + display names) */
  collections?: Array<{ id: string; name: string }>;
  /** Specific record IDs selected by the user */
  selectedRecordIds?: string[];
  /** Human-readable source label (e.g., "Engineering" collection) */
  sourceLabel?: string;
}

/**
 * One-shot transfer buffer between the page hosting the chat widget
 * and the chat page. Set before navigation, consumed on chat page mount.
 */
export interface PendingChatContext {
  /** The user's message text */
  message: string;
  /** Uploaded files from the widget (temporary attachments) */
  uploadedFiles?: UploadedFile[];
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

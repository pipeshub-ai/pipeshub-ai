'use client';

import { create } from 'zustand';

export const BACKGROUND_AGENTS_PANEL_ID = 'chat-background-agents';
export const BACKGROUND_AGENTS_BUTTON_ID = 'chat-background-agents-button';

interface BackgroundAgentsState {
  /** The list is open for this conversation; another conversation opens with it closed. */
  openFor: string | null;
  /** The request whose view is open in the list, by its run; null: the list itself. */
  request: string | null;
  open: (conversationId: string) => void;
  close: () => void;
  openRequest: (runId: string) => void;
  back: () => void;
}

/** The chat's Background agents list (design §6B.43): opened only from its top-bar button, never remembered. */
export const useBackgroundAgentsStore = create<BackgroundAgentsState>((set) => ({
  openFor: null,
  request: null,
  open: (conversationId) => set({ openFor: conversationId, request: null }),
  close: () => set({ openFor: null, request: null }),
  openRequest: (runId) => set({ request: runId }),
  back: () => set({ request: null }),
}));

/** Focus goes back to the top-bar button when the list closes. */
export function focusBackgroundAgentsButton(): void {
  if (typeof document === 'undefined') return;
  document.getElementById(BACKGROUND_AGENTS_BUTTON_ID)?.focus();
}

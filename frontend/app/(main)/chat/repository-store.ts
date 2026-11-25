/**
 * The repository state the composer shares (WP-26 GIT-FLOW): each saved chat's repository, the one held for a new
 * chat's first message, whether the picker is open, and the engine choices its greyed reasons read.
 */
import { create } from 'zustand';
import { RepositoryApi, type ChatRepositoryView, type RepositoryChoice } from './repository-api';
import type { CodingEngineView } from '@/app/(main)/workspace/ai-accounts/types';

/** A repository chosen before a new chat's first message; that message carries it to the server. */
export interface PendingRepository extends RepositoryChoice {
  /** The agent whose new chat it was chosen in; null for the assistant. */
  agentId: string | null;
  fullName: string;
}

type Session = { agentId?: string; sessionId: string };

interface RepositoryState {
  /** Each saved chat's repository by session id; null: it has none. Absent until read. */
  views: Record<string, ChatRepositoryView | null>;
  pending: PendingRepository | null;
  pickerOpen: boolean;
  /** The org's and the person's engine choices, read when the menu opens. */
  engines: CodingEngineView | null;
  load: (session: Session) => Promise<void>;
  setView: (sessionId: string, view: ChatRepositoryView | null) => void;
  setPending: (pending: PendingRepository | null) => void;
  loadEngines: () => Promise<void>;
  openPicker: () => void;
  closePicker: () => void;
}

export const useRepositoryStore = create<RepositoryState>((set) => ({
  views: {},
  pending: null,
  pickerOpen: false,
  engines: null,
  load: async (session) => {
    try {
      const view = await RepositoryApi.get(session);
      set((s) => ({ views: { ...s.views, [session.sessionId]: view } }));
    } catch {
      // Not the owner or not a harness chat: nothing to show; the server refuses a change anyway.
    }
  },
  setView: (sessionId, view) => set((s) => ({ views: { ...s.views, [sessionId]: view } })),
  setPending: (pending) => set({ pending }),
  loadEngines: async () => {
    try {
      const { ModelAccountsApi } = await import('@/app/(main)/workspace/ai-accounts/api');
      const engines = await ModelAccountsApi.codingEngine();
      set({ engines });
    } catch {
      // Unknown: the row stays usable and the server's refusal says why.
    }
  },
  openPicker: () => set({ pickerOpen: true }),
  closePicker: () => set({ pickerOpen: false }),
}));

/** The new chat's held repository, for its agent (null: the assistant). */
export function heldRepository(pending: PendingRepository | null, agentId: string | null): PendingRepository | null {
  return pending && pending.agentId === agentId ? pending : null;
}

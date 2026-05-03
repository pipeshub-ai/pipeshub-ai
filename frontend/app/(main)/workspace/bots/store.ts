import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { devtools } from 'zustand/middleware';
import type { SlackBotConfig, AgentOption, PanelView } from './types';

// ========================================
// Store shape
// ========================================

interface BotsState {
  // ── Data ──
  slackBotConfigs: SlackBotConfig[];
  agents: AgentOption[];

  // ── UI ──
  isLoading: boolean;
  error: string | null;
  searchQuery: string;

  // ── Panel ──
  panelOpen: boolean;
  panelView: PanelView;
  /** null = create mode, string = edit mode (config id) */
  editingBotId: string | null;
}

interface BotsActions {
  setConfigs: (configs: SlackBotConfig[]) => void;
  setAgents: (agents: AgentOption[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setSearchQuery: (query: string) => void;

  openPanel: () => void;
  closePanel: () => void;
  setPanelView: (view: PanelView) => void;
  setEditingBot: (id: string | null) => void;

  reset: () => void;
}

// ========================================
// Initial state
// ========================================

const initialState: BotsState = {
  slackBotConfigs: [],
  agents: [],
  isLoading: false,
  error: null,
  searchQuery: '',
  panelOpen: false,
  panelView: 'type-selector',
  editingBotId: null,
};

// ========================================
// Store
// ========================================

export const useBotsStore = create<BotsState & BotsActions>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setConfigs: (configs) =>
        set((state) => {
          state.slackBotConfigs = configs;
        }),

      setAgents: (agents) =>
        set((state) => {
          state.agents = agents;
        }),

      setLoading: (loading) =>
        set((state) => {
          state.isLoading = loading;
        }),

      setError: (error) =>
        set((state) => {
          state.error = error;
        }),

      setSearchQuery: (query) =>
        set((state) => {
          state.searchQuery = query;
        }),

      openPanel: () =>
        set((state) => {
          state.panelOpen = true;
          state.panelView = 'type-selector';
          state.editingBotId = null;
        }),

      closePanel: () =>
        set((state) => {
          state.panelOpen = false;
          state.panelView = 'type-selector';
          state.editingBotId = null;
        }),

      setPanelView: (view) =>
        set((state) => {
          state.panelView = view;
        }),

      setEditingBot: (id) =>
        set((state) => {
          state.editingBotId = id;
          state.panelView = 'slack-form';
          state.panelOpen = true;
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'bots-store' }
  )
);

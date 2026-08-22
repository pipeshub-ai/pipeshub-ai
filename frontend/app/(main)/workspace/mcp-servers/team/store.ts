'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { McpMyServerEntry, McpServerTemplate } from '../types';

// ========================================
// State
// ========================================

interface ConfigPanelState {
  open: boolean;
  mode: 'create' | 'edit';
  /** Set in edit mode. */
  editingInstance: McpMyServerEntry | null;
  /** Set when creating from a catalog card. */
  prefillTemplate: McpServerTemplate | null;
}

interface McpTeamState {
  templates: McpServerTemplate[];
  /**
   * Org instances merged with the *current admin's own* auth status + tools —
   * fetched via `getMyMcpServers` so the admin can authenticate/discover tools
   * for themselves the same way the personal page does.
   */
  instances: McpMyServerEntry[];

  isLoading: boolean;
  searchQuery: string;

  configPanel: ConfigPanelState;

  deleteTarget: McpMyServerEntry | null;
}

// ========================================
// Actions
// ========================================

interface McpTeamActions {
  setTemplates: (templates: McpServerTemplate[]) => void;
  setInstances: (instances: McpMyServerEntry[]) => void;
  setLoading: (loading: boolean) => void;
  setSearchQuery: (query: string) => void;

  openCreateFromTemplate: (template: McpServerTemplate) => void;
  openCreateCustom: () => void;
  openEditInstance: (instance: McpMyServerEntry) => void;
  closeConfigPanel: () => void;

  openDeleteDialog: (instance: McpMyServerEntry) => void;
  closeDeleteDialog: () => void;

  reset: () => void;
}

// ========================================
// Initial state
// ========================================

const initialConfigPanel: ConfigPanelState = {
  open: false,
  mode: 'create',
  editingInstance: null,
  prefillTemplate: null,
};

const initialState: McpTeamState = {
  templates: [],
  instances: [],
  isLoading: false,
  searchQuery: '',
  configPanel: initialConfigPanel,
  deleteTarget: null,
};

// ========================================
// Store
// ========================================

export const useMcpTeamStore = create<McpTeamState & McpTeamActions>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setTemplates: (templates) =>
        set((s) => {
          s.templates = templates;
        }),
      setInstances: (instances) =>
        set((s) => {
          s.instances = instances;
        }),
      setLoading: (loading) =>
        set((s) => {
          s.isLoading = loading;
        }),
      setSearchQuery: (query) =>
        set((s) => {
          s.searchQuery = query;
        }),

      openCreateFromTemplate: (template) =>
        set((s) => {
          s.configPanel = {
            open: true,
            mode: 'create',
            editingInstance: null,
            prefillTemplate: template,
          };
        }),
      openCreateCustom: () =>
        set((s) => {
          s.configPanel = { open: true, mode: 'create', editingInstance: null, prefillTemplate: null };
        }),
      openEditInstance: (instance) =>
        set((s) => {
          s.configPanel = { open: true, mode: 'edit', editingInstance: instance, prefillTemplate: null };
        }),
      closeConfigPanel: () =>
        set((s) => {
          s.configPanel = initialConfigPanel;
        }),

      openDeleteDialog: (instance) =>
        set((s) => {
          s.deleteTarget = instance;
        }),
      closeDeleteDialog: () =>
        set((s) => {
          s.deleteTarget = null;
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'mcp-team-store' }
  )
);

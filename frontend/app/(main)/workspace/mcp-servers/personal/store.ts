'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { McpAuthMode, McpMyServerEntry, McpServerTemplate } from '../types';

interface AuthDialogState {
  open: boolean;
  instance: McpMyServerEntry | null;
  mode: McpAuthMode | null;
}

/** Same shape the shared `McpInstanceConfigPanel` takes. */
interface ConfigPanelState {
  open: boolean;
  mode: 'create' | 'edit';
  editingInstance: McpMyServerEntry | null;
  prefillTemplate: McpServerTemplate | null;
}

interface McpPersonalState {
  /** The org's instances plus the caller's own personal ones. */
  instances: McpMyServerEntry[];
  templates: McpServerTemplate[];
  isLoading: boolean;
  searchQuery: string;
  authDialog: AuthDialogState;
  addDialogOpen: boolean;
  configPanel: ConfigPanelState;
  deleteTarget: McpMyServerEntry | null;
}

interface McpPersonalActions {
  setInstances: (instances: McpMyServerEntry[]) => void;
  setTemplates: (templates: McpServerTemplate[]) => void;
  setLoading: (loading: boolean) => void;
  setSearchQuery: (query: string) => void;

  openAuthDialog: (instance: McpMyServerEntry) => void;
  closeAuthDialog: () => void;

  openAddDialog: () => void;
  closeAddDialog: () => void;
  openCreateFromTemplate: (template: McpServerTemplate) => void;
  openCreateCustom: () => void;
  openEditInstance: (instance: McpMyServerEntry) => void;
  closeConfigPanel: () => void;

  openDeleteDialog: (instance: McpMyServerEntry) => void;
  closeDeleteDialog: () => void;

  reset: () => void;
}

const initialAuthDialog: AuthDialogState = { open: false, instance: null, mode: null };

const initialConfigPanel: ConfigPanelState = {
  open: false,
  mode: 'create',
  editingInstance: null,
  prefillTemplate: null,
};

const initialState: McpPersonalState = {
  instances: [],
  templates: [],
  isLoading: false,
  searchQuery: '',
  authDialog: initialAuthDialog,
  addDialogOpen: false,
  configPanel: initialConfigPanel,
  deleteTarget: null,
};

export const useMcpPersonalStore = create<McpPersonalState & McpPersonalActions>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setInstances: (instances) =>
        set((s) => {
          s.instances = instances;
        }),
      setTemplates: (templates) =>
        set((s) => {
          s.templates = templates;
        }),
      setLoading: (loading) =>
        set((s) => {
          s.isLoading = loading;
        }),
      setSearchQuery: (query) =>
        set((s) => {
          s.searchQuery = query;
        }),

      openAuthDialog: (instance) =>
        set((s) => {
          s.authDialog = { open: true, instance, mode: instance.authMode };
        }),
      closeAuthDialog: () =>
        set((s) => {
          s.authDialog = initialAuthDialog;
        }),

      openAddDialog: () =>
        set((s) => {
          s.addDialogOpen = true;
        }),
      closeAddDialog: () =>
        set((s) => {
          s.addDialogOpen = false;
        }),
      openCreateFromTemplate: (template) =>
        set((s) => {
          s.addDialogOpen = false;
          s.configPanel = { open: true, mode: 'create', editingInstance: null, prefillTemplate: template };
        }),
      openCreateCustom: () =>
        set((s) => {
          s.addDialogOpen = false;
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
    { name: 'mcp-personal-store' }
  )
);

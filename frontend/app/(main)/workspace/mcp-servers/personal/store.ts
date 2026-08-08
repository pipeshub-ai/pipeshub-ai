'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { McpAuthMode, McpMyServerEntry } from '../types';

interface AuthDialogState {
  open: boolean;
  instance: McpMyServerEntry | null;
  mode: McpAuthMode | null;
}

interface McpPersonalState {
  instances: McpMyServerEntry[];
  isLoading: boolean;
  searchQuery: string;
  authDialog: AuthDialogState;
}

interface McpPersonalActions {
  setInstances: (instances: McpMyServerEntry[]) => void;
  setLoading: (loading: boolean) => void;
  setSearchQuery: (query: string) => void;

  openAuthDialog: (instance: McpMyServerEntry) => void;
  closeAuthDialog: () => void;

  reset: () => void;
}

const initialAuthDialog: AuthDialogState = { open: false, instance: null, mode: null };

const initialState: McpPersonalState = {
  instances: [],
  isLoading: false,
  searchQuery: '',
  authDialog: initialAuthDialog,
};

export const useMcpPersonalStore = create<McpPersonalState & McpPersonalActions>()(
  devtools(
    immer((set) => ({
      ...initialState,

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

      openAuthDialog: (instance) =>
        set((s) => {
          s.authDialog = { open: true, instance, mode: instance.authMode };
        }),
      closeAuthDialog: () =>
        set((s) => {
          s.authDialog = initialAuthDialog;
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'mcp-personal-store' }
  )
);

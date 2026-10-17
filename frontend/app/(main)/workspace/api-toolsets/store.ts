'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { ApiToolset } from './types';

/** `editingId` null with `open` is the "add" flow. */
interface EditorState {
  open: boolean;
  editingId: string | null;
}

interface ApiToolsetsState {
  instances: ApiToolset[];
  isLoading: boolean;
  editor: EditorState;
  connectTarget: ApiToolset | null;
  deleteTarget: ApiToolset | null;
}

interface ApiToolsetsActions {
  setInstances: (instances: ApiToolset[]) => void;
  setLoading: (loading: boolean) => void;
  openCreate: () => void;
  openEdit: (toolset: ApiToolset) => void;
  closeEditor: () => void;
  setConnectTarget: (toolset: ApiToolset | null) => void;
  setDeleteTarget: (toolset: ApiToolset | null) => void;
  reset: () => void;
}

const closedEditor: EditorState = { open: false, editingId: null };

const initialState: ApiToolsetsState = {
  instances: [],
  isLoading: false,
  editor: closedEditor,
  connectTarget: null,
  deleteTarget: null,
};

export const useApiToolsetsStore = create<ApiToolsetsState & ApiToolsetsActions>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setInstances: (instances) =>
        set((s) => {
          s.instances = instances;
          // The open dialog follows the fresh copy, so its connected state is current.
          if (s.connectTarget) s.connectTarget = instances.find((i) => i.id === s.connectTarget!.id) ?? null;
        }),
      setLoading: (loading) =>
        set((s) => {
          s.isLoading = loading;
        }),
      openCreate: () =>
        set((s) => {
          s.editor = { open: true, editingId: null };
        }),
      openEdit: (toolset) =>
        set((s) => {
          s.editor = { open: true, editingId: toolset.id };
        }),
      closeEditor: () =>
        set((s) => {
          s.editor = closedEditor;
        }),
      setConnectTarget: (toolset) =>
        set((s) => {
          s.connectTarget = toolset;
        }),
      setDeleteTarget: (toolset) =>
        set((s) => {
          s.deleteTarget = toolset;
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'api-toolsets-store' },
  ),
);

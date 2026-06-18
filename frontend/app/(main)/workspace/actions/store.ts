import { create } from 'zustand';
import type { RegistryToolsetRow } from '@/app/(main)/toolsets/api';

interface ActionsState {
  createFor: RegistryToolsetRow | null;
  postCreateNoticeOpen: boolean;
  openSetupPanel: (row: RegistryToolsetRow) => void;
  closeSetupPanel: () => void;
  setPostCreateNoticeOpen: (open: boolean) => void;
  reset: () => void;
}

export const useActionsStore = create<ActionsState>()((set) => ({
  createFor: null,
  postCreateNoticeOpen: false,
  openSetupPanel: (row) => set({ createFor: row }),
  closeSetupPanel: () => set({ createFor: null }),
  setPostCreateNoticeOpen: (open) => set({ postCreateNoticeOpen: open }),
  reset: () => set({ createFor: null, postCreateNoticeOpen: false }),
}));

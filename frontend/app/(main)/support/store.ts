'use client';

import { create } from 'zustand';

interface SupportDialogState {
  isOpen: boolean;
  /** Changes on every open, so the dialog remounts with an empty form. */
  openCount: number;
  open: () => void;
  close: () => void;
}

export const useSupportDialogStore = create<SupportDialogState>((set) => ({
  isOpen: false,
  openCount: 0,
  open: () => set((state) => ({ isOpen: true, openCount: state.openCount + 1 })),
  close: () => set({ isOpen: false }),
}));

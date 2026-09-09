'use client';

import { create } from 'zustand';

interface FeedbackDialogState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
}

export const useFeedbackDialogStore = create<FeedbackDialogState>((set) => ({
  isOpen: false,
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
}));

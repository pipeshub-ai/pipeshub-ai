'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface EnterprisePromptsState {
  /** The admin closed the Enterprise card in the workspace sidebar; it shows as a one-line link. */
  sidebarCardDismissed: boolean;
  dismissSidebarCard: () => void;
}

/** Remembered per browser: hiding the card is a convenience, and losing it only shows it again. */
export const useEnterprisePromptsStore = create<EnterprisePromptsState>()(
  persist(
    (set) => ({
      sidebarCardDismissed: false,
      dismissSidebarCard: () => set({ sidebarCardDismissed: true }),
    }),
    {
      name: 'pipeshub-enterprise-prompts',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ sidebarCardDismissed: state.sidebarCardDismissed }),
    },
  ),
);

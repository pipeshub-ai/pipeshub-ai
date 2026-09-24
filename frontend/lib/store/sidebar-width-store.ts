'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { SIDEBAR_WIDTH } from '@/app/components/sidebar/constants';

interface SidebarWidthState {
  sidebarWidth: number;
  setSidebarWidth: (width: number) => void;
  /** Whether the nav sidebar is collapsed (hidden) — session-only, not persisted. */
  isNavCollapsed: boolean;
  setNavCollapsed: (collapsed: boolean) => void;
  /**
   * Number of page-level expand controls currently mounted. The app shell
   * shows its own fallback only when this is 0, so a collapsed sidebar can
   * always be restored no matter which route the user lands on.
   */
  pageExpandControls: number;
  registerPageExpandControl: () => void;
  unregisterPageExpandControl: () => void;
}

export const useSidebarWidthStore = create<SidebarWidthState>()(
  persist(
    (set) => ({
      sidebarWidth: SIDEBAR_WIDTH,
      setSidebarWidth: (width) => set({ sidebarWidth: width }),
      isNavCollapsed: false,
      setNavCollapsed: (collapsed) => set({ isNavCollapsed: collapsed }),
      pageExpandControls: 0,
      registerPageExpandControl: () =>
        set((s) => ({ pageExpandControls: s.pageExpandControls + 1 })),
      unregisterPageExpandControl: () =>
        set((s) => ({ pageExpandControls: Math.max(0, s.pageExpandControls - 1) })),
    }),
    {
      name: 'pipeshub-sidebar-width',
      storage: createJSONStorage(() => localStorage),
      // Only persist sidebarWidth; collapse state is intentionally session-only
      partialize: (state) => ({ sidebarWidth: state.sidebarWidth }),
    }
  )
);

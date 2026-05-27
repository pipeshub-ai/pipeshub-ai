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
}

export const useSidebarWidthStore = create<SidebarWidthState>()(
  persist(
    (set) => ({
      sidebarWidth: SIDEBAR_WIDTH,
      setSidebarWidth: (width) => set({ sidebarWidth: width }),
      isNavCollapsed: false,
      setNavCollapsed: (collapsed) => set({ isNavCollapsed: collapsed }),
    }),
    {
      name: 'pipeshub-sidebar-width',
      storage: createJSONStorage(() => localStorage),
      // Only persist sidebarWidth; collapse state is intentionally session-only
      partialize: (state) => ({ sidebarWidth: state.sidebarWidth }),
    }
  )
);

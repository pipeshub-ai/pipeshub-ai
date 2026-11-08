'use client';

import { useEffect, useState } from 'react';
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { SIDEBAR_WIDTH } from '@/app/components/sidebar/constants';
import { BELOW_DESKTOP_MAX_PX } from '@/lib/hooks/use-is-mobile';

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

const SIDEBAR_MAX_VIEWPORT_FRACTION = 0.4;

/** Width actually applied: a width saved on a desktop must not eat a tablet. */
export function clampSidebarWidth(width: number, innerWidth: number): number {
  // Below desktop the stored width is capped so the page keeps room.
  return innerWidth <= BELOW_DESKTOP_MAX_PX
    ? Math.min(width, Math.round(innerWidth * SIDEBAR_MAX_VIEWPORT_FRACTION))
    : width;
}

export function useAppliedSidebarWidth(): number {
  const sidebarWidth = useSidebarWidthStore((s) => s.sidebarWidth);
  const [innerWidth, setInnerWidth] = useState(() =>
    typeof window === 'undefined' ? Infinity : window.innerWidth,
  );
  useEffect(() => {
    const onResize = () => setInnerWidth(window.innerWidth);
    onResize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return clampSidebarWidth(sidebarWidth, innerWidth);
}

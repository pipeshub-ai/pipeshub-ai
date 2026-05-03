'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { SIDEBAR_WIDTH } from '@/app/components/sidebar/constants';

interface SidebarWidthState {
  sidebarWidth: number;
  setSidebarWidth: (width: number) => void;
}

export const useSidebarWidthStore = create<SidebarWidthState>()(
  persist(
    (set) => ({
      sidebarWidth: SIDEBAR_WIDTH,
      setSidebarWidth: (width) => set({ sidebarWidth: width }),
    }),
    {
      name: 'pipeshub-sidebar-width',
      storage: createJSONStorage(() => localStorage),
    }
  )
);

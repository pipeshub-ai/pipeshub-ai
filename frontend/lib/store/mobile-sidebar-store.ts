import { useEffect } from 'react';
import { create } from 'zustand';

interface MobileSidebarState {
  isOpen: boolean;
  /** Mounted sidebars that can render as a mobile drawer. The hamburger shows only when this is above 0. */
  registeredCount: number;
  open: () => void;
  close: () => void;
  toggle: () => void;
  /** Counts one mobile sidebar. Returns the unregister function. */
  register: () => () => void;
}

/**
 * Global store for controlling the mobile sidebar drawer.
 *
 * Required because the sidebar is a Next.js parallel route slot and cannot
 * receive props directly from the layout. Both the layout (hamburger button)
 * and the sidebar component (× close button) read/write this store.
 */
export const useMobileSidebarStore = create<MobileSidebarState>((set) => ({
  isOpen: false,
  registeredCount: 0,
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  register: () => {
    set((s) => ({ registeredCount: s.registeredCount + 1 }));
    let registered = true;
    return () => {
      if (!registered) return;
      registered = false;
      set((s) => ({ registeredCount: s.registeredCount - 1 }));
    };
  },
}));

/** Registers the calling sidebar as a mobile drawer while `active` is true. */
export function useMobileSidebarRegistration(active: boolean): void {
  const register = useMobileSidebarStore((s) => s.register);
  useEffect(() => (active ? register() : undefined), [active, register]);
}

import { create } from 'zustand';

interface MobileSidebarState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  toggle: () => void;
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
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
}));

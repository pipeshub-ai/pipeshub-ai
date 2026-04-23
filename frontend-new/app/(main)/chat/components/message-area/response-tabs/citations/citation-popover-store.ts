import { create } from 'zustand';

/**
 * List-scoped "which inline citation [N] popover is open".
 * Zustand lets each badge subscribe with `s.activeKey === instanceKey` so only
 * the opening/closing badges re-render (not the whole `MessageList` on every
 * open like React context did).
 */
type InlineCitationPopoverState = {
  activeKey: string | null;
  setActiveKey: (key: string | null) => void;
};

export const useInlineCitationPopoverStore = create<InlineCitationPopoverState>((set) => ({
  activeKey: null,
  setActiveKey: (key) => set({ activeKey: key }),
}));

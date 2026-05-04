import { create } from 'zustand';
import type { CitationData, CitationCallbacks } from './types';

/**
 * List-scoped "which inline citation [N] popover is open".
 *
 * We only ever render ONE inline-citation popover at a time — the store holds
 * the active marker's `instanceKey`, the anchor element it should be pinned to,
 * and the citation + callbacks to render inside. A single
 * `InlineCitationPopoverHost` (mounted once per `MessageList`) consumes this
 * state and renders the actual `Popover.Root`. This avoids the
 * multiple-open-popovers glitch that used to happen when two triggers briefly
 * coexisted in the DOM during `modal={false}` dismissal.
 */
type InlineCitationPopoverState = {
  activeKey: string | null;
  activeAnchor: HTMLElement | null;
  activeCitation: CitationData | null;
  activeCallbacks: CitationCallbacks | null;
  open: (params: {
    key: string;
    anchor: HTMLElement;
    citation: CitationData;
    callbacks?: CitationCallbacks;
  }) => void;
  close: (key?: string) => void;
  /**
   * Retained for orphan cleanup (e.g. when a message row is replaced by the
   * final server id after streaming). Prefer `open` / `close` for user
   * interactions.
   */
  setActiveKey: (key: string | null) => void;
};

export const useInlineCitationPopoverStore = create<InlineCitationPopoverState>((set, get) => ({
  activeKey: null,
  activeAnchor: null,
  activeCitation: null,
  activeCallbacks: null,
  open: ({ key, anchor, citation, callbacks }) =>
    set({
      activeKey: key,
      activeAnchor: anchor,
      activeCitation: citation,
      activeCallbacks: callbacks ?? null,
    }),
  close: (key) => {
    // If a specific key is passed, only close when it's still the active one.
    // Prevents a late "close" from one badge from wiping the host after a
    // different badge has already taken over.
    if (key != null && get().activeKey !== key) return;
    set({
      activeKey: null,
      activeAnchor: null,
      activeCitation: null,
      activeCallbacks: null,
    });
  },
  setActiveKey: (key) => {
    if (key == null) {
      set({
        activeKey: null,
        activeAnchor: null,
        activeCitation: null,
        activeCallbacks: null,
      });
    } else {
      set({ activeKey: key });
    }
  },
}));

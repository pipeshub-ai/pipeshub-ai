import { create } from 'zustand';
import { resetStoreOnSessionEnd } from '@/lib/api/session-cache';
import type { VerifyState } from './engine/revalidate';
import { DEFAULT_URL_STATE, type GraphUrlState } from './url-params';
import type { NodeId } from './types';

/**
 * Selection, filters, the trail and the view's health. Never nodes: those live in the
 * graph model next to the renderer, so a revoked node cannot linger in a store the
 * rest of the app can read.
 */
interface GraphState extends GraphUrlState {
  selection: NodeId | null;
  /** The ids visited, oldest first, and where the person is in them. */
  trail: NodeId[];
  trailIndex: number;
  /** 0 is full quality; see engine/tiers.ts. */
  qualityStep: number;
  verify: VerifyState;

  focusOn: (id: NodeId) => void;
  /** Move to a trail entry (the controller has already opened it). */
  jumpTo: (index: number) => void;
  back: () => void;
  forward: () => void;
  select: (id: NodeId | null) => void;
  setFilters: (filters: Partial<Pick<GraphUrlState, 'direction' | 'edgeTypes' | 'includeTests'>>) => void;
  /** Revoked or unavailable ids leave the selection, the trail and the focus. */
  forget: (ids: readonly NodeId[]) => void;
  stepDown: () => void;
  /** Back to full quality (the person asks for the picture again). */
  restoreQuality: () => void;
  setVerify: (verify: VerifyState) => void;
  reset: () => void;
}

const initial = {
  ...DEFAULT_URL_STATE,
  selection: null as NodeId | null,
  trail: [] as NodeId[],
  trailIndex: -1,
  qualityStep: 0,
  verify: 'ok' as VerifyState,
};

export const MAX_TRAIL = 100;

export const useGraphStore = create<GraphState>((set, get) => ({
  ...initial,

  focusOn: (id) => {
    const { trail, trailIndex, node } = get();
    if (node === id) return;
    // A new focus drops the forward entries, as a browser does.
    const kept = [...trail.slice(0, trailIndex + 1), id].slice(-MAX_TRAIL);
    set({ node: id, selection: id, trail: kept, trailIndex: kept.length - 1 });
  },

  jumpTo: (index) => {
    const { trail } = get();
    if (index >= 0 && index < trail.length) set({ trailIndex: index, node: trail[index], selection: trail[index] });
  },

  back: () => {
    const { trail, trailIndex } = get();
    if (trailIndex > 0) set({ trailIndex: trailIndex - 1, node: trail[trailIndex - 1], selection: trail[trailIndex - 1] });
  },

  forward: () => {
    const { trail, trailIndex } = get();
    if (trailIndex < trail.length - 1) {
      set({ trailIndex: trailIndex + 1, node: trail[trailIndex + 1], selection: trail[trailIndex + 1] });
    }
  },

  select: (selection) => set({ selection }),

  setFilters: (filters) => set(filters as Partial<GraphState>),

  forget: (ids) => {
    if (ids.length === 0) return;
    const gone = new Set(ids);
    const { trail, trailIndex, node, selection } = get();
    const kept = trail.filter((id) => !gone.has(id));
    // The position moves back by the number of removed entries at or before it.
    const removedBefore = trail.slice(0, trailIndex + 1).filter((id) => gone.has(id)).length;
    const focusGone = node !== null && gone.has(node);
    const back = Math.min(kept.length - 1, Math.max(-1, trailIndex - removedBefore));
    // When the entry that went was the first and entries remain after it, the view moves forward to the nearest one.
    const index = back < 0 && focusGone && kept.length > 0 ? 0 : back;
    set({
      trail: kept,
      trailIndex: index,
      selection: selection !== null && gone.has(selection) ? null : selection,
      node: focusGone ? (index >= 0 ? kept[index] : null) : node,
    });
  },

  stepDown: () => set((s) => ({ qualityStep: Math.min(s.qualityStep + 1, 5) })),

  restoreQuality: () => set({ qualityStep: 0 }),

  setVerify: (verify) => set({ verify }),

  reset: () => set({ ...initial }),
}));

resetStoreOnSessionEnd(useGraphStore);

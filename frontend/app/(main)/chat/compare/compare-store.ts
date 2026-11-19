'use client';

import { create } from 'zustand';
import type { ModelOverride } from '../types';
import { MAX_COMPARE_MODELS, type CompareTurn } from './types';

/**
 * Compare state lives beside the chat store, not in it: a chat that never
 * compares never touches it. `turns` is keyed by the real (primary) slot; the
 * branch's own streaming state is an ordinary chat slot.
 */
interface CompareState {
  enabled: boolean;
  /** The picked models in order; the first one answers as the normal (primary) stream. */
  models: ModelOverride[];
  turns: Record<string, CompareTurn>;
}

interface CompareActions {
  setEnabled: (enabled: boolean) => void;
  toggleModel: (model: ModelOverride) => void;
  setModels: (models: ModelOverride[]) => void;
}

export const sameModel = (a: ModelOverride, b: ModelOverride): boolean =>
  a.modelKey === b.modelKey && a.modelName === b.modelName;

export const useCompareStore = create<CompareState & CompareActions>()((set) => ({
  enabled: false,
  models: [],
  turns: {},
  setEnabled: (enabled) => set({ enabled }),
  setModels: (models) => set({ models: models.slice(0, MAX_COMPARE_MODELS) }),
  toggleModel: (model) =>
    set((s) => {
      if (s.models.some((m) => sameModel(m, model))) {
        return { models: s.models.filter((m) => !sameModel(m, model)) };
      }
      if (s.models.length >= MAX_COMPARE_MODELS) return s;
      return { models: [...s.models, model] };
    }),
}));

/** The compare turn that owns this slot as its branch, with the primary slot's id. */
export function turnOfBranchSlot(slotId: string): { primarySlotId: string; turn: CompareTurn } | null {
  for (const [primarySlotId, turn] of Object.entries(useCompareStore.getState().turns)) {
    if (turn.branchSlotId === slotId) return { primarySlotId, turn };
  }
  return null;
}

/**
 * The model a compared slot must keep using for follow-up requests of its turn
 * (a card answer, an approval), whatever the composer has selected.
 */
export function compareModelForSlot(slotId: string): ModelOverride | null {
  const own = useCompareStore.getState().turns[slotId];
  if (own?.models) return own.models[0];
  const branch = turnOfBranchSlot(slotId);
  return branch?.turn.models?.[1] ?? null;
}

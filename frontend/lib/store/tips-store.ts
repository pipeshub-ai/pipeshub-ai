'use client';

import { useEffect } from 'react';
import { create } from 'zustand';
import { NotificationsApi, type TipId } from '@/app/(main)/notifications/api';
import { useFeatureFlagsStore, selectChatMentionsEnabled } from '@/lib/store/feature-flags-store';

interface TipsState {
  /** false until the server list arrives; a tip is never shown before then, so a seen tip cannot flash. */
  loaded: boolean;
  seen: readonly string[];
}

interface TipsActions {
  /** Reads `tipsSeen` once per session; concurrent callers share the request. */
  ensureLoaded: () => Promise<void>;
  /** Optimistic: hidden at once, then persisted. A failed write keeps it hidden for this session. */
  markSeen: (tipId: TipId) => void;
}

let loadPromise: Promise<void> | null = null;

export const useTipsStore = create<TipsState & TipsActions>()((set, get) => ({
  loaded: false,
  seen: [],
  ensureLoaded: () => {
    if (get().loaded) return Promise.resolve();
    loadPromise ??= NotificationsApi.getPreferences()
      .then((prefs) =>
        set((s) => ({ loaded: true, seen: [...new Set([...(prefs.tipsSeen ?? []), ...s.seen])] })),
      )
      .catch(() => undefined)
      .finally(() => {
        loadPromise = null;
      });
    return loadPromise;
  },
  markSeen: (tipId) => {
    if (get().seen.includes(tipId)) return;
    set((s) => ({ seen: [...s.seen, tipId] }));
    void NotificationsApi.markTipSeen(tipId).catch(() => undefined);
  },
}));

export function resetTipsStoreForTests(): void {
  loadPromise = null;
  useTipsStore.setState({ loaded: false, seen: [] });
}

/**
 * `visible` is true only with the mentions flag on, after the server list has loaded, and while the tip is unseen.
 * With the flag off nothing is fetched.
 */
export function useTip(tipId: TipId): { visible: boolean; markSeen: () => void } {
  const enabled = useFeatureFlagsStore(selectChatMentionsEnabled);
  const loaded = useTipsStore((s) => s.loaded);
  const seen = useTipsStore((s) => s.seen.includes(tipId));
  const ensureLoaded = useTipsStore((s) => s.ensureLoaded);
  const markSeen = useTipsStore((s) => s.markSeen);
  useEffect(() => {
    if (enabled) void ensureLoaded();
  }, [enabled, ensureLoaded]);
  return { visible: enabled && loaded && !seen, markSeen: () => markSeen(tipId) };
}

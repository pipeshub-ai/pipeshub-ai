'use client';

import { useEffect, useMemo } from 'react';
import { create } from 'zustand';
import { fetchPendingApprovals } from './api';
import { useChatStore } from './store';
import type { PendingApprovalItem } from './types';

const POLL_MS = 30_000;

// Answered here, and when: a list fetched before the server claimed the call must not bring it
// back, but a call whose answer never arrived returns with a later poll.
const answered = new Map<string, number>();
const CLAIM_GRACE_MS = 15_000;
// Bumped by every refresh, put and drop: a fetch that finishes after a newer change is stale.
let generation = 0;

interface PendingApprovalsState {
  items: PendingApprovalItem[];
  refresh: () => Promise<void>;
  /** Takes a call off the list once it is answered. */
  drop: (approvalId: string) => void;
  /** Puts a call back, first, with the card the server sent last (a refused edit's errors). */
  put: (item: PendingApprovalItem) => void;
  /** Drops the calls whose time ran out. */
  prune: () => void;
  /** How many of the current chat's own approval cards are open, so the composer's list stays out of their way. */
  inlineOpen: number;
  markInline: (delta: 1 | -1) => void;
}

const live = (item: PendingApprovalItem) =>
  item.card.expiresAt > Date.now() && !(Date.now() - (answered.get(item.card.approvalId) ?? -Infinity) < CLAIM_GRACE_MS);

export const usePendingApprovals = create<PendingApprovalsState>((set) => ({
  items: [],
  refresh: async () => {
    const mine = ++generation;
    try {
      const items = await fetchPendingApprovals();
      if (mine === generation) set({ items: items.filter(live) });
    } catch {
      // A failed poll keeps the list it had; the next one tries again.
    }
  },
  drop: (approvalId) => {
    answered.set(approvalId, Date.now());
    generation++;
    set((s) => ({ items: s.items.filter((i) => i.card.approvalId !== approvalId) }));
  },
  put: (item) => {
    answered.delete(item.card.approvalId);
    generation++;
    set((s) => ({ items: [item, ...s.items.filter((i) => i.card.approvalId !== item.card.approvalId)] }));
  },
  prune: () => set((s) => ({ items: s.items.filter(live) })),
  inlineOpen: 0,
  markInline: (delta) => set((s) => ({ inlineOpen: Math.max(0, s.inlineOpen + delta) })),
}));

/** Keeps the list fresh while the composer is shown: on mount, on window focus, every 30s while
 * anything waits, and drops a call the moment it expires. */
export function usePendingApprovalsSync(): void {
  const items = usePendingApprovals((s) => s.items);
  const waiting = items.length > 0;

  useEffect(() => {
    const refresh = () => void usePendingApprovals.getState().refresh();
    refresh();
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, []);

  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => void usePendingApprovals.getState().refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [waiting]);

  useEffect(() => {
    if (!items.length) return;
    const next = Math.min(...items.map((i) => i.card.expiresAt));
    const timer = setTimeout(() => usePendingApprovals.getState().prune(), Math.max(0, next - Date.now()));
    return () => clearTimeout(timer);
  }, [items]);
}

/** The calls waiting in chats other than the one on screen (its own call is shown inline). */
export function useOtherChatApprovals(): PendingApprovalItem[] {
  const items = usePendingApprovals((s) => s.items);
  const currentConvId = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.convId ?? null : null));
  return useMemo(() => items.filter((i) => i.conversationId !== currentConvId), [items, currentConvId]);
}

/** Test-only: forget what was answered. */
export function resetPendingApprovalsForTest(): void {
  answered.clear();
  usePendingApprovals.setState({ items: [], inlineOpen: 0 });
}

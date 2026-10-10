import { useCallback, useEffect, useState } from 'react';
import { useChatStore } from '../store';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import { isElectron } from '@/lib/electron';
import { CollaborationApi } from '../collaboration-api';
import { FEED_NOT_MODIFIED, type ConversationRef, type FeedPage } from '../collaboration-types';
import { isAccessLostError, retryAfterSecondsOf } from '../utils/conversation-errors';
import { applyFeedPage, feedCursor } from '../utils/apply-feed-page';
import { applyRetryAfter, nextPollDelayMs, withJitter } from '../utils/sync-backoff';
import { joinSyncLeadership } from '../utils/sync-leader';
import { saveDraft } from '../utils/draft-storage';
import { refreshFeedForSlot } from '../utils/collab-send';

/** An Electron window out of focus this long stops polling (UX-12). */
export const ELECTRON_BLUR_PAUSE_MS = 60_000;
/** A page with `hasMore` is followed up at once, at most this many times per tick. */
const MAX_CATCH_UP_PAGES = 10;
/** A tab that joins as a follower fetches once to fill whatever it missed while hidden or on another chat. */
const FOLLOWER_CATCH_UP_MS = 1_000;

export interface UseConversationSyncResult {
  /** Epoch ms of the last page that changed anything; `null` before the first. */
  lastSyncedAt: number | null;
  /** The last poll's error while it keeps failing; `null` after a success. */
  error: unknown;
}

/** False for a visible window the user is not working in (another window or app has focus). */
function windowFocused(): boolean {
  return typeof document === 'undefined' || document.hasFocus();
}

/** True while the tab can be seen: visible, and for Electron focused within the last minute. */
function useTabActive(): boolean {
  const [active, setActive] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden');
  useEffect(() => {
    let blurTimer: ReturnType<typeof setTimeout> | null = null;
    let visible = document.visibilityState !== 'hidden';
    let blurredTooLong = false;
    const update = () => setActive(visible && !blurredTooLong);
    const onVisibility = () => {
      visible = document.visibilityState !== 'hidden';
      update();
    };
    const onBlur = () => {
      if (!isElectron()) return;
      blurTimer = setTimeout(() => {
        blurredTooLong = true;
        update();
      }, ELECTRON_BLUR_PAUSE_MS);
    };
    const onFocus = () => {
      if (blurTimer) clearTimeout(blurTimer);
      blurTimer = null;
      blurredTooLong = false;
      update();
    };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);
    update();
    return () => {
      if (blurTimer) clearTimeout(blurTimer);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('focus', onFocus);
    };
  }, []);
  return active;
}

/**
 * Keeps the active collaborative chat in step with the server by polling the feed with its `rev`
 * (a 304 costs one indexed read and no store write).
 *
 * Polls only when the flag is on, the slot is collaborative and active, the user's own stream is idle,
 * and the tab is visible. One tab per conversation polls and shares what it gets with the others.
 * A 404/403 marks the slot `accessLost` and stops. Leaving a lost slot evicts it.
 */
export function useConversationSync(slotId: string | null | undefined): UseConversationSyncResult {
  const flagOn = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const convId = useChatStore((s) => (slotId ? s.slots[slotId]?.convId ?? null : null));
  const agentKey = useChatStore((s) => (slotId ? s.slots[slotId]?.threadAgentId ?? null : null));
  const isTemp = useChatStore((s) => (slotId ? s.slots[slotId]?.isTemp ?? false : false));
  const isCollaborative = useChatStore((s) => (slotId ? s.slots[slotId]?.access?.isCollaborative ?? false : false));
  const isStreaming = useChatStore((s) => (slotId ? s.slots[slotId]?.isStreaming ?? false : false));
  const accessLost = useChatStore((s) => (slotId ? s.slots[slotId]?.accessLost ?? false : false));
  const isActive = useChatStore((s) => slotId !== null && slotId !== undefined && s.activeSlotId === slotId);
  const tabActive = useTabActive();
  const [state, setState] = useState<UseConversationSyncResult>({ lastSyncedAt: null, error: null });

  const enabled = Boolean(
    flagOn && slotId && convId && !isTemp && isCollaborative && isActive && !isStreaming && !accessLost && tabActive,
  );

  const markAccessLost = useCallback((id: string) => {
    const store = useChatStore.getState();
    const queued = store.slots[id]?.queuedSend;
    const conv = store.slots[id]?.convId;
    if (queued && conv) saveDraft(conv, queued.query);
    store.updateSlot(id, { accessLost: true, queuedSend: null });
  }, []);

  useEffect(() => {
    if (!enabled || !slotId || !convId) return;
    const ref: ConversationRef = agentKey
      ? { kind: 'agent', agentKey, id: convId }
      : { kind: 'chat', id: convId };
    let disposed = false;
    let cursor = -1;
    let lastActivityAt = Date.now();
    let leading = false;
    const catchUp = setTimeout(() => {
      if (!disposed && !leading) void refreshFeedForSlot(slotId);
    }, FOLLOWER_CATCH_UP_MS);

    const leadership = joinSyncLeadership(convId, {
      onLost: () => {
        if (!disposed) markAccessLost(slotId);
      },
      onPage: (page, afterSeq) => {
        if (disposed) return;
        // The page starts past what this tab has: fetch the gap instead of skipping it.
        if (afterSeq !== undefined && afterSeq > feedCursor(slotId, -1)) {
          void refreshFeedForSlot(slotId);
          return;
        }
        lastActivityAt = page.lastActivityAt;
        if (applyFeedPage(slotId, page)) setState({ lastSyncedAt: Date.now(), error: null });
      },
      onLead: () => {
        leading = true;
        let timer: ReturnType<typeof setTimeout> | null = null;
        let controller: AbortController | null = null;
        let failures = 0;
        let stopped = false;

        let ticking = false;

        const schedule = (retryAfter?: number) => {
          if (stopped) return;
          const delay = nextPollDelayMs({
            visible: true,
            idleMs: Date.now() - lastActivityAt,
            failures,
            focused: windowFocused(),
          });
          if (delay === null) return;
          timer = setTimeout(tick, applyRetryAfter(withJitter(delay), retryAfter));
        };

        // A window regaining focus should not wait out the slow cadence it was on.
        const onFocus = () => {
          if (stopped || ticking || failures > 0) return;
          if (timer) clearTimeout(timer);
          schedule();
        };
        window.addEventListener('focus', onFocus);

        const tick = async () => {
          if (stopped) return;
          ticking = true;
          controller = new AbortController();
          const { signal } = controller;
          try {
            for (let page = 0; page < MAX_CATCH_UP_PAGES; page += 1) {
              const slot = useChatStore.getState().slots[slotId];
              if (!slot) return;
              const from = feedCursor(slotId, cursor);
              const result = await CollaborationApi.fetchFeed(ref, { afterSeq: from, rev: slot.rev }, signal);
              if (stopped) return;
              failures = 0;
              if (result === FEED_NOT_MODIFIED) break;
              const feed: FeedPage = result;
              lastActivityAt = feed.lastActivityAt;
              for (const m of feed.messages) cursor = Math.max(cursor, m.seq);
              if (applyFeedPage(slotId, feed)) setState({ lastSyncedAt: Date.now(), error: null });
              leadership.publish(feed, from);
              if (!feed.hasMore) break;
            }
            setState((prev) => (prev.error === null ? prev : { ...prev, error: null }));
            ticking = false;
            schedule();
          } catch (error) {
            if (stopped || signal.aborted) return;
            ticking = false;
            if (isAccessLostError(error)) {
              stopped = true;
              markAccessLost(slotId);
              leadership.publishLost();
              return;
            }
            failures += 1;
            setState((prev) => ({ ...prev, error }));
            schedule(retryAfterSecondsOf(error));
          }
        };

        void tick();
        return () => {
          stopped = true;
          leading = false;
          window.removeEventListener('focus', onFocus);
          if (timer) clearTimeout(timer);
          controller?.abort();
        };
      },
    });

    return () => {
      disposed = true;
      clearTimeout(catchUp);
      leadership.stop();
    };
  }, [enabled, slotId, convId, agentKey, markAccessLost]);

  useEffect(() => {
    if (!slotId) return undefined;
    return () => {
      const store = useChatStore.getState();
      const slot = store.slots[slotId];
      if (!slot?.accessLost) return;
      store.evictSlot(slotId);
      if (slot.convId) store.removeConversation(slot.convId);
    };
  }, [slotId]);

  return state;
}

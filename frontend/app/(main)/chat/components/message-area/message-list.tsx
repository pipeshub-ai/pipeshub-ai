'use client';

import React, { useEffect, useLayoutEffect, useRef, useMemo, useCallback, useState } from 'react';
import { useAui, useAuiState } from '@assistant-ui/react';
import { Flex, Box } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ChatResponse } from './chat-response';
import { chatResponseProps, useLatestArtifactVersions, useSlotStreamingProps } from './chat-response-props';
import type { ChatResponseContext } from './chat-response-props';
import { chatExtension, chatSlots } from '@/config';
import type { ChatResponseLayout } from '../../chat-extension';
import { useChatStore } from '../../store';
import { debugLog } from '../../debug-logger';
import { ASK_MORE_QUESTION_SETS, chatMessageColumnStyle } from '../../constants';
import { useIsBelowDesktop, useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { CitationMaps } from './response-tabs/citations';
import { emptyCitationMaps, useCitationActions, isCitationPopoverKeyStillValid } from './response-tabs/citations';
import { useInlineCitationPopoverStore } from './response-tabs/citations/citation-popover-store';
import { InlineCitationPopoverHost } from './response-tabs/citations/inline-citation-popover-host';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ProgressiveBlur } from '@/app/components/ui/progressive-blur';
import { loadOlderMessagesForSlot } from '../../streaming';
import { buildMessagePairs, keepUnchangedPairs, retryTurnMessage } from './message-pairs';
import type { MessagePair, RetryTurn } from './message-pairs';
import { ErrorBoundary, ErrorFallback } from '@/app/components/ui/error-boundary';
import { shouldShowJumpToNewest } from './jump-to-newest';
import { JumpToLatest } from './jump-to-latest';
import { QuestionRail, questionLandmark } from './question-rail';
import { RowRenderKeyContext, useLiveRowAliases } from './row-render-key';
import { FOLLOWS_END_ATTR, keepOnScreen, type ScreenHold } from './keep-on-screen';
import {
  AnswerPanelScope,
  READING_END_PX,
  READING_LINE,
  onJumpRequest,
  readingKeyAt,
  setReadingAnswer,
  useAnswerPanelStore,
} from '../answer-panel/answer-panel-store';

// Stable empty references to avoid re-renders from selector fallbacks.
// `?? []` or `?? null` in a selector body creates a new ref every call,
// defeating Object.is comparison.
const EMPTY_ARRAY: never[] = [];
/** Streaming: distance from bottom (px) to count as flush for resuming tail-follow */
const STREAMING_RESUME_DIST_FLUSH_PX = 4;
/** Streaming: wheel deltaY must be more negative than this to opt out (avoids trackpad jitter) */
const STREAMING_WHEEL_UP_OPT_OUT_MIN_DELTA = 2;
/** Streaming: opt out when scrollTop falls this far below last tail-sync anchor */
const STREAMING_OPT_OUT_ANCHOR_PX = 4;
/**
 * Streaming: the most the view follows the answer in one frame. Text grows a line at a time, well under this; a block
 * that lands whole (a table, a code block, a formula) is followed over the next frames instead of in one step, so it
 * slides into view as other text does. With reduced motion it is one step.
 */
const STREAMING_FOLLOW_STEP_MAX_PX = 40;
/** Touch: min upward scroll (read older) during SSE to stop tail-follow */
const STREAMING_TOUCH_SCROLL_UP_THRESHOLD_PX = 8;
/** Idle (non-streaming): near-bottom band for scroll-lock hysteresis */
const IDLE_TAIL_BOTTOM_ZONE_PX = 80;
const IDLE_SCROLLED_UP_ZONE_PX = 96;
/** Idle: ms after hitting bottom before "scrolled up" can latch */
const IDLE_SCROLL_LOCK_GRACE_MS = 160;
/** Distance from top (px) at which we trigger loading older messages */
const LOAD_OLDER_THRESHOLD_PX = 120;

function isStreamingFlushWithBottom(el: HTMLElement): boolean {
  const { scrollTop, scrollHeight, clientHeight } = el;
  const dist = scrollHeight - scrollTop - clientHeight;
  return dist <= STREAMING_RESUME_DIST_FLUSH_PX;
}

/** True when the reader asked the system for less motion: every scroll move is then one step. */
function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Fallback for the page motion role and its easing, if the tokens cannot be read. */
const GLIDE_FALLBACK_MS = 320;
const GLIDE_FALLBACK_EASE: [number, number, number, number] = [0.25, 0.1, 0.25, 1];

/**
 * The page motion role (duration and easing) read from the theme tokens, so the
 * glide at send follows the same timing as every other view change and drops to
 * 0ms when the reader asks for less motion.
 */
function readPageMotion(): { ms: number; ease: [number, number, number, number] } {
  if (typeof window === 'undefined') return { ms: GLIDE_FALLBACK_MS, ease: GLIDE_FALLBACK_EASE };
  const root = window.getComputedStyle(document.documentElement);
  const rawMs = root.getPropertyValue('--motion-page').trim();
  const parsedMs = rawMs.endsWith('ms') ? parseFloat(rawMs) : rawMs.endsWith('s') ? parseFloat(rawMs) * 1000 : NaN;
  const ms = Number.isFinite(parsedMs) ? parsedMs : GLIDE_FALLBACK_MS;
  const match = root.getPropertyValue('--motion-ease').match(/cubic-bezier\(([^)]+)\)/);
  const points = match?.[1].split(',').map((n) => parseFloat(n));
  const ease = points && points.length === 4 && points.every(Number.isFinite)
    ? (points as [number, number, number, number])
    : GLIDE_FALLBACK_EASE;
  return { ms, ease };
}

/** Progress (0-1) along a CSS cubic-bezier curve at time fraction `t`. */
function cubicBezierAt([x1, y1, x2, y2]: [number, number, number, number], t: number): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  const curve = (a: number, b: number, u: number) => 3 * a * u * (1 - u) ** 2 + 3 * b * u ** 2 * (1 - u) + u ** 3;
  // Find the curve parameter whose x is `t` (bisection: monotonic in x for CSS easings).
  let lo = 0;
  let hi = 1;
  let u = t;
  for (let i = 0; i < 20; i += 1) {
    const x = curve(x1, x2, u);
    if (Math.abs(x - t) < 1e-4) break;
    if (x < t) lo = u; else hi = u;
    u = (lo + hi) / 2;
  }
  return curve(y1, y2, u);
}

const EMPTY_STRING = '';
const EMPTY_CITATION_MAPS: CitationMaps = emptyCitationMaps();

/** The tallest the fade behind the composer gets; it never reaches above the composer's dock. */
const COMPOSER_BLUR_HEIGHT_PX = 180;
/** Calls `read` just before each commit changes the DOM under it: the moment React gives to read what is on screen. */
class BeforeCommit extends React.Component<{ read: () => void; children: React.ReactNode }> {
  getSnapshotBeforeUpdate() {
    this.props.read();
    return null;
  }

  componentDidUpdate() {}

  render() {
    return this.props.children;
  }
}

export function MessageList({
  composer,
  topInset,
}: {
  composer?: React.ReactNode;
  /**
   * The list runs under the chat's top bar: its content starts this far down, and a question brought to the top
   * (a send, the rail, reopening the chat) rests there, below the bar's fade.
   */
  topInset?: string;
} = {}) {
  // ── Active slot ID (needed for save/restore on conversation switch) ──
  const activeSlotId = useChatStore((s) => s.activeSlotId);
  // ── Slot-scoped selectors (narrow — only active slot fields) ──
  const isStreaming = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.isStreaming ?? false : false
  );
  const streamingQuestion = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.streamingQuestion || EMPTY_STRING : EMPTY_STRING
  );
  const pendingCollections = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.pendingCollections || EMPTY_ARRAY : EMPTY_ARRAY
  );
  const regenerateMessageId = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.regenerateMessageId ?? null : null
  );
  const isInitialized = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.isInitialized ?? false : false
  );
  const remoteRunInProgress = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.remoteRunInProgress ?? false : false
  );
  const isLoadingConversation = useChatStore((s) =>
    s.activeSlotId ? !s.slots[s.activeSlotId]?.isInitialized : false
  );
  // streamingContent + currentStatusMessage are now passed as props to ChatResponse
  // (only the streaming instance gets non-empty values). Previously ChatResponse
  // subscribed to these directly, causing ALL instances to re-render on every rAF flush.
  const streaming = useSlotStreamingProps(activeSlotId);
  const { streamingContent, currentStatusMessage, streamingCitationMaps, streamingArtifacts, streamingParts } = streaming;
  const messagePagination = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.messagePagination ?? null : null
  );
  const hasOlderMessages = messagePagination?.hasOlderMessages ?? false;
  const isLoadingOlder = messagePagination?.isLoadingOlder ?? false;
  // ── Render-reason tracking ──────────────────────────────────────
  debugLog.tick('[chat] [MessageList]');
  const prevMsgListRef = useRef<Record<string, unknown>>({});
  const currentMsgListVals: Record<string, unknown> = {
    isStreaming, streamingQuestion, streamingCitationMaps,
    pendingCollections, regenerateMessageId, isInitialized, isLoadingConversation,
    streamingContent, currentStatusMessage, streamingParts,
  };
  const msgListReasons: string[] = [];
  for (const [k, v] of Object.entries(currentMsgListVals)) {
    if (!Object.is(v, prevMsgListRef.current[k])) msgListReasons.push(k);
  }
  if (msgListReasons.length > 0) {
    debugLog.reason('[chat] [MessageList]', msgListReasons);
  }
  prevMsgListRef.current = currentMsgListVals;

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const spacerRef = useRef<HTMLDivElement>(null);
  const composerDockRef = useRef<HTMLDivElement>(null);
  const citationCallbacks = useCitationActions();
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const prevPairCountRef = useRef(0);
  const lastMessageObserverRef = useRef<ResizeObserver | null>(null);
  const lastMessageKeyRef = useRef<string | null>(null);

  // ── Load-older refs ────────────────────────────────────────────────
  // Stale-closure-safe mirrors for the scroll handler (no re-render on change)
  const hasOlderMessagesRef = useRef(hasOlderMessages);
  hasOlderMessagesRef.current = hasOlderMessages;
  const isLoadingOlderRef = useRef(isLoadingOlder);
  isLoadingOlderRef.current = isLoadingOlder;
  /** True while an async loadOlderMessagesForSlot call is in-flight. */
  const loadOlderInFlightRef = useRef(false);
  /**
   * Key of the first message pair from the previous render.
   * Compared in Effect #4 to distinguish a prepend (first key changes)
   * from a user-send append (first key stays the same).
   */
  const prevFirstPairKeyRef = useRef<string | null>(null);

  // ── Scroll infrastructure ──────────────────────────────────────────
  // All scroll actions flow through `executeScroll` (the single source of
  // truth). Refs are used instead of state to avoid rerenders on every
  // scroll event or programmatic animation.
  const isScrolledUpRef = useRef(false);
  const isAutoScrollingRef = useRef(false);
  const autoScrollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasPerformedInitialScrollRef = useRef(false);
  const prevActiveSlotIdRef = useRef<string | null>(null);
  const wasStreamingRef = useRef(isStreaming);
  /** Detects isStreaming false→true so we clear scroll lock before tail sync (layout phase). */
  const prevIsStreamingForLockRef = useRef(false);
  /** The slot the stream-start check last ran for: a run already going in a chat just opened is not a send. */
  const prevLockSlotIdRef = useRef<string | null>(null);
  // Render-time sync (not useEffect) so the value is current when
  // synchronous ResizeObserver callbacks fire in the same commit phase.
  const isStreamingRef = useRef(isStreaming);
  isStreamingRef.current = isStreaming;
  // Idle (non-streaming) only: timestamp of last "at bottom" hit for hysteresis.
  // The streaming path uses `lastTailSyncScrollTopRef` + flush / anchor checks instead.
  const lockClearedAtMsRef = useRef<number>(0);
  /**
   * Last `scrollTop` applied by `syncStreamingMessageTail` while following SSE.
   * Comparing the live `scrollTop` to this catches even small user nudges upward;
   * a large "distance from bottom" zone alone wrongly treats those as still
   * pinned and keeps snapping the viewport down.
   * -1 = no tail sync applied yet for this streaming session.
   */
  const lastTailSyncScrollTopRef = useRef<number>(-1);
  /** Coalesces ResizeObserver bursts to one layout pass per animation frame */
  const streamingLayoutRafRef = useRef<number | null>(null);
  /** Coalesces resume tail-sync from scroll/wheel so at most one rAF runs at a time */
  const resumeTailSyncRafRef = useRef<number | null>(null);
  /** The glide that brings a just-sent question to the top; null when none runs. */
  const sendGlideRafRef = useRef<number | null>(null);
  /** The next frame of a tail-follow spread over frames (`STREAMING_FOLLOW_STEP_MAX_PX`); null when none is due. */
  const followCatchUpRafRef = useRef<number | null>(null);
  /** The tail-follow as it is now, for that next frame to call. */
  const syncTailRef = useRef<(catchingUp?: boolean) => void>(() => {});
  /**
   * True from opening a finished chat until the next question: the chat lands on
   * its end, so no blank space is added under the last answer.
   */
  const spacerHeldAtZeroRef = useRef(false);
  /** The scroll position the reader last had, from the scroll event. */
  const readerScrollTopRef = useRef<number | null>(null);
  /** The least spacer that keeps the reader where they were after the transcript got shorter; 0 when none. */
  const spacerFloorRef = useRef(0);
  /** The transcript column. */
  const contentColumnRef = useRef<HTMLDivElement>(null);
  /** Holds the answer in place after its run ended (`keepOnScreen`); null when nothing is held. */
  const runEndHoldRef = useRef<ScreenHold | null>(null);
  /** Where the newest answer was on screen just before the commit that ends its run; null once read. */
  const liveAnswerTopRef = useRef<number | null>(null);
  /** How far the newest row reached below its answer's text when last followed with no status line under it. */
  const belowAnswerRef = useRef<{ key: string | null; px: number } | null>(null);

  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  // The question rail, as rooms draw it: from 1024px, where the column has room beside it.
  const isBelowDesktop = useIsBelowDesktop();
  // From 1024px the column beside the chat takes each answer's tabs, open or shut.
  const panelScope = useMemo(() => ({ column: !isBelowDesktop }), [isBelowDesktop]);

  // Reactive thread messages plus the client used to append follow-up messages
  const threadMessages = useAuiState((s) => s.thread.messages);
  const aui = useAui();

  // Track threadMessages changes as a render reason
  const prevThreadMsgsRef = useRef(threadMessages);
  if (threadMessages !== prevThreadMsgsRef.current) {
    debugLog.reason('[chat] [MessageList]', ['threadMessages']);
    prevThreadMsgsRef.current = threadMessages;
  }

  // Callback ref setter for message elements
  const setMessageRef = useCallback((key: string, el: HTMLDivElement | null) => {
    if (el) {
      messageRefs.current.set(key, el);
    } else {
      messageRefs.current.delete(key);
    }
  }, []);

  // Build message pairs (user question + assistant answer) in chronological order
  const previousPairsRef = useRef<Map<string, MessagePair>>(new Map());
  const messagePairs = useMemo<MessagePair[]>(
    () =>
      keepUnchangedPairs(
        previousPairsRef.current,
        buildMessagePairs(threadMessages, {
          isStreaming,
          streamingQuestion,
          pendingCollections,
          regenerateMessageId,
          remoteRunInProgress,
          emptyCitationMaps: EMPTY_CITATION_MAPS,
        })
      ),
    [threadMessages, isStreaming, streamingQuestion, pendingCollections, regenerateMessageId, remoteRunInProgress]
  );
  useEffect(() => {
    previousPairsRef.current = new Map(messagePairs.map((pair) => [pair.key, pair]));
  }, [messagePairs]);

  // ── One row on screen across the hand-off ──────────────────────────
  // When the run ends the streamed reply is swapped for the saved one, which has another key. Drawn under a new key
  // the whole turn would mount afresh (its trail too, which would open, grow and fold again). The saved row takes the
  // key the running one had instead: same place, same question. Only the React key changes; everything else still
  // uses the pair's own key.
  const { renderKeyOf, atRunEnd, awaitingSavedRow } = useLiveRowAliases(messagePairs, activeSlotId);

  // The newest answer is running: `null` while the list is empty for a moment.
  const lastPairLive = messagePairs.length > 0 ? messagePairs[messagePairs.length - 1].isStreaming : null;
  const lastPairLiveRef = useRef<{ slotId: string | null; live: boolean }>({ slotId: null, live: false });

  // ── The hand-off's in-between moment ──────────────────────────────
  // The run's end reaches the list before the saved rows do: for a commit the thread is empty, or still holds the
  // streamed row with the run marked over (the slot updates at once, the thread a commit later). Drawn, the turn would
  // unmount, or its answer and trail would vanish and mount afresh. The rows already on screen are kept instead, for at
  // most two frames, so that moment is never painted; a thread that really is empty shows as such after that.
  const heldBodyRef = useRef<{ slotId: string | null; body: React.ReactElement | null }>({ slotId: null, body: null });
  const [holdExpired, setHoldExpired] = useState(false);
  const betweenRows = messagePairs.length === 0 || awaitingSavedRow;
  if (holdExpired && !betweenRows) setHoldExpired(false);
  const holdingBody =
    betweenRows
    && !holdExpired
    && heldBodyRef.current.body !== null
    && heldBodyRef.current.slotId === activeSlotId
    // Only at a run's end: a row was live, and the list has not moved on from it.
    && atRunEnd;

  const streamingRowIds = useMemo(
    () => messagePairs.filter((pair) => pair.isStreaming).map((pair) => pair.key),
    [messagePairs],
  );
  const latestArtifactVersions = useLatestArtifactVersions(
    threadMessages,
    isStreaming ? streamingArtifacts : null,
    streamingRowIds,
  );

  const lastPairKey = messagePairs[messagePairs.length - 1]?.key ?? null;

  // Ref-mirror of messagePairs — lets scroll effects read the latest pairs
  // without having the full array in their dependency list (which would cause
  // effect #2 to re-run and cancel its rAF on every render during streaming).
  const messagePairsRef = useRef(messagePairs);
  messagePairsRef.current = messagePairs;

  /** The newest answer's text box, as the rows were last drawn. */
  const lastAnswerBox = useCallback((): Element | null => {
    const key = lastMessageKeyRef.current;
    return (key && messageRefs.current.get(key)?.querySelector('.answer-prose')) || null;
  }, []);
  // The commit that ends the newest answer's run may itself move the answer (its trail's last status line gone, the
  // saved row drawn): where it was is read before that commit changes anything.
  const readBeforeRunEnd = useCallback(() => {
    const seen = lastPairLiveRef.current;
    const live = messagePairsRef.current[messagePairsRef.current.length - 1]?.isStreaming;
    if (!seen.live || live !== false || seen.slotId !== useChatStore.getState().activeSlotId) return;
    liveAnswerTopRef.current = lastAnswerBox()?.getBoundingClientRect().top ?? null;
  }, [lastAnswerBox]);

  // ── Jump to newest ─────────────────────────────────────────────────
  const [showJumpToNewest, setShowJumpToNewest] = useState(false);
  // Height of the sticky composer dock, so the jump button sits above it, not on it.
  const [composerDockHeight, setComposerDockHeight] = useState(0);
  const updateJumpToNewestVisibility = useCallback(() => {
    const container = scrollContainerRef.current;
    const key = lastMessageKeyRef.current;
    const lastEl = key ? messageRefs.current.get(key) : null;
    // While the view is still catching up with a block that landed whole, the newest text is on its way into view.
    if (!container || !lastEl || followCatchUpRafRef.current !== null) {
      setShowJumpToNewest(false);
      return;
    }
    setShowJumpToNewest(
      shouldShowJumpToNewest(
        lastEl.getBoundingClientRect().bottom,
        container.getBoundingClientRect().bottom,
      ),
    );
  }, []);

  // Clear orphaned inline-citation key when a message row is replaced (e.g. after stream complete)
  const citationOpenKey = useInlineCitationPopoverStore((s) => s.activeKey);
  const setCitationKey = useInlineCitationPopoverStore((s) => s.setActiveKey);
  useEffect(() => {
    if (!citationOpenKey) return;
    const rowKeys = messagePairsRef.current.map((p) => p.key);
    if (!isCitationPopoverKeyStillValid(citationOpenKey, rowKeys)) {
      setCitationKey(null);
    }
  }, [citationOpenKey, messagePairs, setCitationKey]);

  // Stale-closure-safe mirror of isMobile for use in useCallback/useMemo
  // functions that intentionally have empty (or non-isMobile) dep arrays.
  // Updated synchronously on every render so it's always current.
  const isMobileRef = useRef(isMobile);
  isMobileRef.current = isMobile;

  // ── Diagnostic: log message pair count for rendering pipeline debugging ──
  if (process.env.NODE_ENV === 'development') {
    const assistantMsgs = threadMessages.filter(m => m.role === 'assistant').length;
    const userMsgs = threadMessages.filter(m => m.role === 'user').length;
    if (messagePairs.length === 0 && threadMessages.length > 0) {
      console.warn(
        '[debugging] [MessageList] 0 pairs but',
        threadMessages.length, 'thread msgs (user:', userMsgs, ', assistant:', assistantMsgs, ')',
        'isStreaming:', isStreaming
      );
    }
  }

  /**
   * How far below the scroller's top a question brought to the top rests. Under the top bar that is the list's inset
   * plus the column's own top padding, the depth the first question rests at, so every question lands at one height
   * below the fade. Otherwise the scroller's edge, as before.
   */
  const topInsetRef = useRef(topInset);
  topInsetRef.current = topInset;
  const questionRestOffset = useCallback((container: HTMLElement): number => {
    const inset = parseFloat(window.getComputedStyle(container).paddingTop) || 0;
    if (!topInsetRef.current) return inset;
    const column = contentColumnRef.current;
    return inset + (column ? parseFloat(window.getComputedStyle(column).paddingTop) || 0 : 0);
  }, []);

  // ── The column beside the chat: the answer being read, and the picker's jump ──
  // The answer read is the last whose top passed 40% of the box above the composer; at the end of the list, the
  // newest. A pick (the picker, an answer's Sources) holds until the reader scrolls on.
  const dockHeightRef = useRef(composerDockHeight);
  dockHeightRef.current = composerDockHeight;
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el || !activeSlotId) return undefined;
    const slotId = activeSlotId;
    let frame = 0;
    let held = false;
    let spied: string | undefined;
    const run = () => {
      frame = 0;
      if (held) return;
      const line = el.getBoundingClientRect().top + Math.max(0, el.clientHeight - dockHeightRef.current) * READING_LINE;
      const answers = Array.from(el.querySelectorAll<HTMLElement>('[data-answer-id]'), (row) => ({
        key: row.dataset.answerId ?? '',
        top: row.getBoundingClientRect().top,
      }));
      const key = readingKeyAt(answers, line, el.scrollHeight - el.clientHeight - el.scrollTop <= READING_END_PX);
      if (!key) return;
      spied = key;
      setReadingAnswer(slotId, key);
    };
    const soon = () => {
      if (!frame) frame = requestAnimationFrame(run);
    };
    const release = () => {
      held = false;
    };
    const unsubscribe = useAnswerPanelStore.subscribe((state) => {
      const key = state.readingAnswer[slotId];
      if (key !== undefined && key !== spied) held = true;
    });
    const offJump = onJumpRequest(slotId, (key) => {
      const row = el.querySelector(`[data-answer-id=${JSON.stringify(key)}]`);
      if (!row) return;
      const top = row.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - questionRestOffset(el);
      // Instant, never a glide: the pick lands where it was asked to.
      el.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
    });
    // Rows added or taken away (an older page above, a saved reply in a streamed one's place) move the answers with
    // no scroll at all when the list sits at its very top, where the browser does not anchor it: read them again.
    const isRow = (node: Node) =>
      node instanceof HTMLElement && (node.hasAttribute('data-answer-id') || node.querySelector('[data-answer-id]') !== null);
    const rows = new MutationObserver((records) => {
      for (const r of records) {
        // A change inside an answer (one streaming) is not a row coming or going: one lookup, and on.
        if (r.target instanceof Element && r.target.closest('[data-answer-id]')) continue;
        if (Array.from(r.addedNodes).some(isRow) || Array.from(r.removedNodes).some(isRow)) {
          soon();
          return;
        }
      }
    });
    rows.observe(el, { childList: true, subtree: true });
    const inputs = ['wheel', 'touchmove', 'keydown'] as const;
    el.addEventListener('scroll', soon, { passive: true });
    for (const type of inputs) el.addEventListener(type, release, { passive: true });
    soon();
    return () => {
      cancelAnimationFrame(frame);
      rows.disconnect();
      unsubscribe();
      offJump();
      el.removeEventListener('scroll', soon);
      for (const type of inputs) el.removeEventListener(type, release);
    };
  }, [activeSlotId, questionRestOffset]);

  // ── Spacer recalculation (direct DOM mutation — no React state) ────
  // During streaming, ResizeObserver fires ~60/sec. Writing to a ref
  // avoids a React rerender on every frame. The spacer div always exists
  // in the DOM (never conditionally rendered) so the ref is stable.
  const recalcSpacerHeight = useCallback((measured?: {
    containerHeight: number;
    lastMessageHeight: number;
    scrollHeight: number;
    scrollTop: number;
    composerHeight: number;
  }) => {
    /**
     * The spacer never shrinks below what keeps the reader's current position reachable: a
     * shrink there would make the browser clamp scrollTop, and the text on screen would jump
     * (as when the run's end swaps and folds the answer, which can land while the list still
     * counts the run as streaming). While an answer grows the spacer shrinks by what it grows,
     * which this allows. The send glide sizes its own space.
     */
    const holdReader = (next: number): number => {
      const container = scrollContainerRef.current;
      const spacer = spacerRef.current;
      if (!container || !spacer || sendGlideRafRef.current !== null) return next;
      const current = parseFloat(spacer.style.minHeight) || 0;
      if (next >= current) return next;
      const floor = current + container.scrollTop + container.clientHeight - container.scrollHeight;
      return Math.max(next, floor);
    };

    if (messageRefs.current.size === 0 || !lastMessageKeyRef.current || !scrollContainerRef.current) {
      if (spacerRef.current) spacerRef.current.style.minHeight = `${holdReader(0)}px`;
      debugLog.spacer('no messages or no container → height=0');
      return;
    }

    const element = messageRefs.current.get(lastMessageKeyRef.current);
    const container = scrollContainerRef.current;

    if (!element) {
      if (spacerRef.current) spacerRef.current.style.minHeight = `${holdReader(0)}px`;
      debugLog.spacer('no element for last message → height=0');
      return;
    }

    // A caller that already measured this frame (e.g. `syncStreamingMessageTail`)
    // passes its numbers in so this doesn't force a second synchronous layout
    // pass for the same geometry — a write (the minHeight assignment below)
    // sandwiched between two independent read passes is what causes thrashing.
    const containerHeight = measured?.containerHeight ?? container.clientHeight;
    const lastMessageHeight = measured?.lastMessageHeight ?? element.getBoundingClientRect().height;
    const totalScrollHeight = measured?.scrollHeight ?? container.scrollHeight;
    const scrollTop = measured?.scrollTop ?? container.scrollTop;
    // The composer dock is in this scroller's flow (sticky at the bottom), so
    // it already pads the tail; the spacer only makes up the rest.
    const composerHeight = measured?.composerHeight ?? composerDockRef.current?.offsetHeight ?? 0;
    const isOverflowing = totalScrollHeight > containerHeight;

    let needed = 0;
    // On mobile: skip the spacer entirely. The mobile chat area is small enough
    // that the "scroll last message to top" affordance adds far more blank space
    // than it saves — the Figma mobile spec shows content flush against the input.
    // On desktop: add spacer only when content overflows or user has scrolled
    // away from the top (avoids unnecessary scrollbar on short conversations).
    if (!isMobileRef.current && !spacerHeldAtZeroRef.current && (isOverflowing || scrollTop > 0)) {
      // A question at the top rests below the list's top inset, and the column's own clearance under the last
      // answer already counts, so that much less space is needed under it.
      const inset = questionRestOffset(container);
      const column = contentColumnRef.current;
      const clearance = column ? parseFloat(window.getComputedStyle(column).paddingBottom) || 0 : 0;
      needed = Math.max(0, containerHeight - inset - clearance - composerHeight - lastMessageHeight);
    }
    // After the transcript got shorter under the reader, keep at least the space that holds them there.
    needed = holdReader(Math.max(needed, spacerFloorRef.current));

    if (spacerRef.current) {
      const next = `${needed}px`;
      if (spacerRef.current.style.minHeight !== next) {
        spacerRef.current.style.minHeight = next;
      }
    }
    debugLog.spacer(`containerH=${containerHeight} composerH=${composerHeight} lastMsgH=${Math.round(lastMessageHeight)} overflow=${isOverflowing} → spacer=${needed}`);
  }, [questionRestOffset]);

  // ── The answer's end: the text the reader is on stays where it is ──
  // When the newest answer stops running, its trail folds (and the composer gives up its Stop). The answer is held
  // where it was on screen through that, so the content above it moves instead of the text being read. First, should
  // something outside the column have shortened the range in this very commit, the reader is put back where they were.
  useLayoutEffect(() => {
    if (lastPairLive === null) return;
    const seen = lastPairLiveRef.current;
    lastPairLiveRef.current = { slotId: activeSlotId, live: lastPairLive };
    if (seen.slotId !== activeSlotId || !seen.live || lastPairLive) return;
    const container = scrollContainerRef.current;
    if (!container) return;

    // A layout read earlier in this commit (a child measuring itself) may already have pulled the position in: the
    // spacer makes up what is missing and the reader is put back.
    const keepTop = readerScrollTopRef.current;
    if (keepTop !== null && container.scrollTop < keepTop + 0.5) {
      const short = keepTop - (container.scrollHeight - container.clientHeight);
      const spacer = spacerRef.current;
      if (short > 0.5 && spacer) {
        const next = (parseFloat(spacer.style.minHeight) || 0) + short;
        spacerFloorRef.current = next;
        spacer.style.minHeight = `${next}px`;
      }
      if (container.scrollTop < keepTop - 0.5) {
        isAutoScrollingRef.current = true;
        container.scrollTop = keepTop;
        queueMicrotask(() => {
          isAutoScrollingRef.current = false;
        });
      }
    }

    const lastKey = messagePairsRef.current[messagePairsRef.current.length - 1]?.key;
    const answer = lastKey ? messageRefs.current.get(lastKey)?.querySelector('.answer-prose') : null;
    // Where it was before this commit: what the commit itself moved is put back too, before it is painted.
    const from = liveAnswerTopRef.current;
    liveAnswerTopRef.current = null;
    runEndHoldRef.current?.stop();
    runEndHoldRef.current = null;
    if (!answer) return;
    // Only an answer on screen is held: a reader up-thread keeps what they are reading, by the browser's own anchoring.
    // Behind the docked composer is not on screen: an answer still under it rises into view as the trail above folds,
    // and the trail the reader is on stays where it is.
    const view = container.getBoundingClientRect();
    const box = answer.getBoundingClientRect();
    const moved = from === null ? 0 : from - box.top;
    const readableBottom = view.bottom - (composerDockRef.current?.offsetHeight ?? 0);
    if (box.bottom + moved > view.top && box.top + moved < readableBottom) {
      runEndHoldRef.current = keepOnScreen(container, answer, isAutoScrollingRef, from ?? undefined);
    }
  }, [lastPairLive, activeSlotId]);
  useEffect(() => () => runEndHoldRef.current?.stop(), []);
  // Every commit while the answer is held: what the commit moved is put back before it is painted.
  useLayoutEffect(() => {
    runEndHoldRef.current?.sync();
  });

  /**
   * ══════════════════════════════════════════════════════════════════
   * executeScroll — Single Source of Truth for all scroll actions.
   * ══════════════════════════════════════════════════════════════════
   *
   * All scroll operations in the message list MUST go through this
   * function. It handles:
   *   • Target calculation (top-of-last-message vs bottom-of-container)
   *   • User scroll-lock bypass (`chatBecameActive`)
   *   • 80/20 jump animation (instant 80%, smooth 20%)
   *   • Protection against smooth-scroll animations false-triggering
   *     the user scroll-lock detector
   *
   * @param options.target           Where to scroll
   * @param options.behavior         How to animate
   * @param options.chatBecameActive If true, resets isScrolledUp (new context)
   */
  const executeScroll = useCallback((options: {
    target: 'top-of-last-message' | 'bottom-of-container';
    behavior: 'auto' | 'smooth' | '80/20-jump';
    chatBecameActive?: boolean;
  }) => {
    if (!scrollContainerRef.current) {
      return;
    }
    // With reduced motion every move is one step.
    const behavior = prefersReducedMotion() ? 'auto' : options.behavior;

    // chatBecameActive resets the user scroll lock — new context
    if (options.chatBecameActive) {
      isScrolledUpRef.current = false;
    } else if (isScrolledUpRef.current) {
      return; // user has scrolled up — don't interfere
    }

    const container = scrollContainerRef.current;
    let targetScrollTop = 0;

    if (options.target === 'top-of-last-message') {
      if (messageRefs.current.size === 0 || !lastMessageKeyRef.current) return;
      const element = messageRefs.current.get(lastMessageKeyRef.current);
      if (!element) return;

      const containerRect = container.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();
      const topBuffer = questionRestOffset(container);
      targetScrollTop = Math.max(0, elementRect.top - containerRect.top + container.scrollTop - topBuffer);
    } else {
      // bottom-of-container
      targetScrollTop = container.scrollHeight - container.clientHeight;
    }

    // Guard programmatic scrolls so handleScroll does not treat them as the user
    // leaving the tail. Smooth / 80-20 need a long window (500ms) for in-flight
    // animation events. Instant tail-follow runs every rAF during streaming — a
    // 100ms timeout kept isAutoScrollingRef true almost continuously and made
    // scroll feel sticky; a macrotask deferral is enough for sync scroll events.
    if (behavior === 'smooth' || behavior === '80/20-jump') {
      if (autoScrollTimeoutRef.current) clearTimeout(autoScrollTimeoutRef.current);
      isAutoScrollingRef.current = true;
      autoScrollTimeoutRef.current = setTimeout(() => {
        isAutoScrollingRef.current = false;
        autoScrollTimeoutRef.current = null;
      }, 500);
    } else {
      if (autoScrollTimeoutRef.current) {
        clearTimeout(autoScrollTimeoutRef.current);
        autoScrollTimeoutRef.current = null;
      }
      isAutoScrollingRef.current = true;
      autoScrollTimeoutRef.current = setTimeout(() => {
        isAutoScrollingRef.current = false;
        autoScrollTimeoutRef.current = null;
      }, 0);
    }

    // Execute the scroll
    if (behavior === '80/20-jump') {
      // Instant jump to 80%, then smooth glide to 100%
      container.scrollTop = targetScrollTop * 0.8;
      requestAnimationFrame(() => {
        container.scrollTo({ top: targetScrollTop, behavior: 'smooth' });
      });
    } else if (behavior === 'auto') {
      // Direct assignment: one step, whatever scroll-behavior the page sets
      container.scrollTop = targetScrollTop;
    } else {
      container.scrollTo({ top: targetScrollTop, behavior });
    }
  }, [questionRestOffset]);

  /**
   * Instant tail-follow while SSE is active: runs after DOM/layout knows the new
   * message height, without going through executeScroll’s timers. Paired with
   * useLayoutEffect (same frame as streaming markdown commit) so the viewport
   * does not paint one frame “behind” the growing answer.
   */
  /**
   * The first question already rests at the top, under the column's own top padding: aiming its
   * row at the scroller's edge would slide it up by that padding and back once the run ends.
   */
  const atRestTop = useCallback((top: number): number => {
    const column = contentColumnRef.current;
    const pad = column ? parseFloat(window.getComputedStyle(column).paddingTop) || 0 : 0;
    return top <= pad + 0.5 ? 0 : top;
  }, []);

  const syncStreamingMessageTail = useCallback((catchingUp = false) => {
    if (!isStreamingRef.current || isScrolledUpRef.current) {
      scrollContainerRef.current?.removeAttribute(FOLLOWS_END_ATTR);
      return;
    }
    // The run's end holds the answer where it was: what grows at the end is not followed.
    if (runEndHoldRef.current && !runEndHoldRef.current.done) return;

    const container = scrollContainerRef.current;
    const key = lastMessageKeyRef.current;
    const lastEl = key ? messageRefs.current.get(key) : null;
    if (!container || !lastEl) return;

    // One read pass for this frame, shared with `recalcSpacerHeight` below —
    // avoids two independent getBoundingClientRect()/scrollHeight passes
    // (with the spacer's minHeight write forcing a reflow in between) on
    // every streaming flush.
    const containerRect = container.getBoundingClientRect();
    const elementRect = lastEl.getBoundingClientRect();
    const containerHeight = container.clientHeight;
    const scrollTop = container.scrollTop;
    const scrollHeight = container.scrollHeight;
    const composerHeight = composerDockRef.current?.offsetHeight ?? 0;
    // The status line under an answer with no tool steps ("Thinking…" once a text turn ends, or while the stream is
    // quiet) is not followed: it shows in the clearance under the answer, so its coming and going moves nothing, the
    // run's end included. The row is measured as it ends without it: the answer's end, and what was last under it.
    let statusLine = 0;
    const answer = lastEl.querySelector('.answer-prose');
    if (answer) {
      const below = elementRect.bottom - answer.getBoundingClientRect().bottom;
      const seen = belowAnswerRef.current;
      if (!lastEl.querySelector('[data-status-line]')) belowAnswerRef.current = { key, px: below };
      else if (seen?.key === key) statusLine = Math.max(0, below - seen.px);
    }
    const rowHeight = elementRect.height - statusLine;

    recalcSpacerHeight({
      containerHeight,
      lastMessageHeight: rowHeight,
      scrollHeight,
      scrollTop,
      composerHeight,
    });

    // The send glide owns the scroll position until it lands.
    if (sendGlideRafRef.current !== null) return;
    // A follow spread over frames takes its next step in its own frame: no second step in this one.
    if (followCatchUpRafRef.current !== null && !catchingUp) return;

    // The composer dock overlays the bottom of this scroller.
    const visibleHeight = containerHeight - composerHeight;

    // The question rests at the top until the answer's end, with the column's clearance under it, reaches the
    // composer; from then on the end is followed. Measured where the row's top rests (below the top inset), the two
    // meet at that point, so the hand-over moves nothing.
    const rowTop = elementRect.top - containerRect.top + scrollTop;
    const titleTop = atRestTop(Math.max(0, rowTop - questionRestOffset(container)));
    const column = contentColumnRef.current;
    const clearance = column ? parseFloat(window.getComputedStyle(column).paddingBottom) || 0 : 0;
    const bottom = scrollHeight - statusLine - containerHeight;
    const atRest = rowTop - titleTop + rowHeight + clearance <= visibleHeight;
    const nextTop = atRest
      // The browser keeps a question at rest no lower than the bottom (on a phone, with no blank space under the
      // answer, it sits there): while the line shows, no lower than the bottom without it.
      ? statusLine > 0 ? Math.min(titleTop, Math.max(0, bottom)) : titleTop
      : bottom;
    // Said on the scroller for what joins the answer above its end: while the end is followed, a step that joins the
    // trail is kept in place by this follow in the same frame, so it joins at once instead of opening (`agent-activity`).
    container.toggleAttribute(FOLLOWS_END_ATTR, !atRest);

    // Already at the target position — skip the write (and the scroll-event
    // churn / layout it triggers).
    if (Math.abs(nextTop - scrollTop) < 1) {
      lastTailSyncScrollTopRef.current = scrollTop;
      return;
    }

    // While the view follows the answer (it is where the last follow put it), a step down longer than a line or two
    // (a block that landed whole) is taken over the next frames. Opening a chat whose answer is running is one step.
    const following = lastTailSyncScrollTopRef.current >= 0 && Math.abs(lastTailSyncScrollTopRef.current - scrollTop) < 2;
    const spread = following && nextTop - scrollTop > STREAMING_FOLLOW_STEP_MAX_PX && !prefersReducedMotion();
    isAutoScrollingRef.current = true;
    container.scrollTop = spread ? scrollTop + STREAMING_FOLLOW_STEP_MAX_PX : nextTop;
    if (spread) {
      followCatchUpRafRef.current = requestAnimationFrame(() => {
        followCatchUpRafRef.current = null;
        syncTailRef.current(true);
      });
    }
    lastTailSyncScrollTopRef.current = container.scrollTop;
    // Where the reader now is, before the scroll event says so: the run's end may come first.
    readerScrollTopRef.current = container.scrollTop;
    queueMicrotask(() => {
      isAutoScrollingRef.current = false;
    });
  }, [recalcSpacerHeight, atRestTop, questionRestOffset]);
  useLayoutEffect(() => {
    syncTailRef.current = syncStreamingMessageTail;
  }, [syncStreamingMessageTail]);

  const cancelSendGlide = useCallback(() => {
    if (sendGlideRafRef.current !== null) {
      cancelAnimationFrame(sendGlideRafRef.current);
      sendGlideRafRef.current = null;
    }
  }, []);

  /**
   * Brings the newest question to the top of the view after a send: one glide on
   * the page motion role, then tail-follow takes over. The target is read every
   * frame, so a spacer or trail that grows meanwhile does not leave it short.
   * With reduced motion it is a single step.
   */
  const glideToLastMessageTop = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    cancelSendGlide();

    const targetTop = () => {
      const key = lastMessageKeyRef.current;
      const lastEl = key ? messageRefs.current.get(key) : null;
      const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
      if (!lastEl) return Math.min(container.scrollTop, maxTop);
      const topBuffer = questionRestOffset(container);
      const top = lastEl.getBoundingClientRect().top - container.getBoundingClientRect().top
        + container.scrollTop - topBuffer;
      return Math.min(maxTop, atRestTop(Math.max(0, top)));
    };

    const settle = () => {
      sendGlideRafRef.current = null;
      lastTailSyncScrollTopRef.current = container.scrollTop;
      queueMicrotask(() => {
        isAutoScrollingRef.current = false;
      });
      if (isStreamingRef.current) syncStreamingMessageTail();
    };

    const { ms, ease } = readPageMotion();
    if (ms <= 0 || prefersReducedMotion()) {
      // One step, taken once the new question's row is in the list.
      const firstKey = lastMessageKeyRef.current;
      const jump = () => {
        isAutoScrollingRef.current = true;
        container.scrollTop = targetTop();
        readerScrollTopRef.current = container.scrollTop;
        settle();
      };
      sendGlideRafRef.current = requestAnimationFrame(() => {
        if (lastMessageKeyRef.current !== firstKey || !isStreamingRef.current) {
          jump();
          return;
        }
        sendGlideRafRef.current = requestAnimationFrame(jump);
      });
      return;
    }

    let from = container.scrollTop;
    let startedAt = performance.now();
    let glidingToKey = lastMessageKeyRef.current;
    const step = (now: number) => {
      // The reader scrolled away: stop where they are.
      if (isScrolledUpRef.current) {
        sendGlideRafRef.current = null;
        isAutoScrollingRef.current = false;
        return;
      }
      // The new question's row arrived after the glide began: aim at it from here.
      if (lastMessageKeyRef.current !== glidingToKey) {
        glidingToKey = lastMessageKeyRef.current;
        from = container.scrollTop;
        startedAt = now;
      }
      const progress = Math.min(1, (now - startedAt) / ms);
      isAutoScrollingRef.current = true;
      container.scrollTop = from + (targetTop() - from) * cubicBezierAt(ease, progress);
      lastTailSyncScrollTopRef.current = container.scrollTop;
      readerScrollTopRef.current = container.scrollTop;
      if (progress < 1) {
        sendGlideRafRef.current = requestAnimationFrame(step);
      } else {
        settle();
      }
    };
    isAutoScrollingRef.current = true;
    sendGlideRafRef.current = requestAnimationFrame(step);
  }, [cancelSendGlide, syncStreamingMessageTail, atRestTop, questionRestOffset]);

  const scheduleResumeTailSync = useCallback(() => {
    if (resumeTailSyncRafRef.current !== null) return;
    resumeTailSyncRafRef.current = requestAnimationFrame(() => {
      resumeTailSyncRafRef.current = null;
      if (!isStreamingRef.current || isScrolledUpRef.current) return;
      syncStreamingMessageTail();
    });
  }, [syncStreamingMessageTail]);

  /** Shared by `handleScroll` and passive `wheel` — single place for flush + resume. */
  const runStreamingBottomResume = useCallback(
    (scrollTop: number) => {
      const wasScrolledUp = isScrolledUpRef.current;
      isScrolledUpRef.current = false;
      lastTailSyncScrollTopRef.current = scrollTop;
      if (wasScrolledUp) {
        scheduleResumeTailSync();
      }
    },
    [scheduleResumeTailSync],
  );

  // ── Load older messages trigger ────────────────────────────────────
  // Fires the async load. The browser's native CSS scroll anchoring
  // (overflow-anchor: auto) keeps the viewport stable when messages are
  // prepended — no manual scrollTop adjustment needed.
  const triggerLoadOlderMessages = useCallback(() => {
    if (loadOlderInFlightRef.current) return;
    if (!hasOlderMessagesRef.current || isLoadingOlderRef.current) return;
    const targetSlotId = useChatStore.getState().activeSlotId;
    if (!targetSlotId) return;

    loadOlderInFlightRef.current = true;
    loadOlderMessagesForSlot(targetSlotId).finally(() => {
      loadOlderInFlightRef.current = false;
    });
  }, []);

  // ── User scroll detection ─────────────────────────────────────────
  // Streaming: opt out when `scrollTop` drops meaningfully below the last
  // tail-sync position; resume when flush with the doc bottom (`dist` only —
  // equivalent to near-max for non-subpixel cases). Resume MUST run before the
  // opt-out check so a stale anchor cannot block "scroll back down to follow."
  const handleScroll = useCallback(() => {
    if (!scrollContainerRef.current) return;

    const container = scrollContainerRef.current;
    const { scrollTop, scrollHeight, clientHeight } = container;
    readerScrollTopRef.current = scrollTop;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;

    updateJumpToNewestVisibility();

    if (isStreamingRef.current) {
      const flushWithBottom = isStreamingFlushWithBottom(container);

      // User can reach the bottom while programmatic tail-sync still holds the
      // guard flag; always evaluate resume so follow turns back on.
      if (isAutoScrollingRef.current && !flushWithBottom) return;

      if (flushWithBottom) {
        runStreamingBottomResume(scrollTop);
        return;
      }

      if (isAutoScrollingRef.current) return;

      const anchor = lastTailSyncScrollTopRef.current;
      if (anchor >= 0 && scrollTop < anchor - STREAMING_OPT_OUT_ANCHOR_PX) {
        isScrolledUpRef.current = true;
      }
      return;
    }

    if (isAutoScrollingRef.current) return;

    if (distFromBottom <= IDLE_TAIL_BOTTOM_ZONE_PX) {
      isScrolledUpRef.current = false;
      lockClearedAtMsRef.current = Date.now();
      return;
    }

    if (distFromBottom >= IDLE_SCROLLED_UP_ZONE_PX) {
      const msSinceClear = Date.now() - lockClearedAtMsRef.current;
      if (msSinceClear > IDLE_SCROLL_LOCK_GRACE_MS) {
        isScrolledUpRef.current = true;
      }
    }

    // Load older messages when scrolled near the top (after initial scroll done)
    if (
      scrollTop < LOAD_OLDER_THRESHOLD_PX &&
      hasPerformedInitialScrollRef.current &&
      !isStreamingRef.current
    ) {
      triggerLoadOlderMessages();
    }
  }, [runStreamingBottomResume, triggerLoadOlderMessages, updateJumpToNewestVisibility]);

  // Stream start: clear scroll lock in layout phase (before paint), not in useEffect,
  // so the first tail sync is not skipped. On every streaming commit, pin the tail
  // before paint so the viewport never lags one frame behind the markdown.
  //
  // At the start of a run the question glides to the top instead of jumping there
  // in one frame; tail-follow then only moves when the answer outgrows the view.
  useLayoutEffect(() => {
    const streamStarted = isStreaming && !prevIsStreamingForLockRef.current;
    // A run that starts in the chat on screen, after it has opened, is a question just sent. Opening a chat whose
    // answer is already running follows its tail instead.
    const sentHere =
      streamStarted && hasPerformedInitialScrollRef.current && prevLockSlotIdRef.current === activeSlotId;
    prevLockSlotIdRef.current = activeSlotId;
    if (streamStarted) {
      isScrolledUpRef.current = false;
      lastTailSyncScrollTopRef.current = -1;
      // A new question brings the blank space back that lets it reach the top.
      runEndHoldRef.current?.stop();
      spacerHeldAtZeroRef.current = false;
      spacerFloorRef.current = 0;
      // A Regenerate keeps its row: what was under the earlier answer says nothing about this one.
      belowAnswerRef.current = null;
    }
    prevIsStreamingForLockRef.current = isStreaming;

    if (!isStreaming) {
      scrollContainerRef.current?.removeAttribute(FOLLOWS_END_ATTR);
      return;
    }
    if (sentHere) {
      // Size the spacer first so the question's top is reachable, then glide to it. A glide the
      // send already started keeps going: it reads its target every frame, so it is not restarted.
      recalcSpacerHeight();
      if (sendGlideRafRef.current === null) glideToLastMessageTop();
      return;
    }
    syncStreamingMessageTail();
  }, [
    isStreaming,
    streamingContent,
    currentStatusMessage?.id,
    currentStatusMessage?.message,
    streamingCitationMaps,
    streamingParts,
    syncStreamingMessageTail,
    recalcSpacerHeight,
    glideToLastMessageTop,
    activeSlotId,
  ]);

  // ── rAF-coalesced resize handler for ResizeObserver ───────────────
  // Spacer + tail on size changes that are not always tied to store updates
  // (code blocks, images, fonts). While streaming, tail sync also runs from
  // useLayoutEffect above; this path catches layout-only growth the same frame.
  //
  // Streaming Tracker: short message → title at top; tall → bottom edge.
  const throttledResize = useMemo(() => {
    return () => {
      // While it streams, the answer's end is followed now, in the frame it grew in: the answer reveals its text a
      // little every frame between store updates, so growth is often seen only here, and a frame later the new line
      // would first be drawn one line lower. The tail touches the spacer and the scroll, not this row, so no loop.
      if (isStreamingRef.current) syncStreamingMessageTail();
      if (streamingLayoutRafRef.current !== null) return;
      streamingLayoutRafRef.current = requestAnimationFrame(() => {
        streamingLayoutRafRef.current = null;
        if (isStreamingRef.current) {
          syncStreamingMessageTail();
        } else {
          recalcSpacerHeight();
        }
        updateJumpToNewestVisibility();
      });
    };
  }, [recalcSpacerHeight, syncStreamingMessageTail, updateJumpToNewestVisibility]);

  // ── Cleanup auto-scroll timeout on unmount ──
  useEffect(() => {
    return () => {
      if (autoScrollTimeoutRef.current) clearTimeout(autoScrollTimeoutRef.current);
      if (streamingLayoutRafRef.current !== null) {
        cancelAnimationFrame(streamingLayoutRafRef.current);
        streamingLayoutRafRef.current = null;
      }
      if (resumeTailSyncRafRef.current !== null) {
        cancelAnimationFrame(resumeTailSyncRafRef.current);
        resumeTailSyncRafRef.current = null;
      }
      if (sendGlideRafRef.current !== null) {
        cancelAnimationFrame(sendGlideRafRef.current);
        sendGlideRafRef.current = null;
      }
      if (followCatchUpRafRef.current !== null) {
        cancelAnimationFrame(followCatchUpRafRef.current);
        followCatchUpRafRef.current = null;
      }
    };
  }, []);

  // Passive wheel + touch on the scroll container so tail-follow stops as soon as
  // the user moves to read up-thread (wheel / touch), not only after `scroll` hysteresis.
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent) => {
      if (!isStreamingRef.current) return;
      if (
        ev.deltaY < -STREAMING_WHEEL_UP_OPT_OUT_MIN_DELTA &&
        el.scrollTop > 0
      ) {
        isScrolledUpRef.current = true;
        return;
      }
      if (ev.deltaY > 0 && isStreamingFlushWithBottom(el)) {
        runStreamingBottomResume(el.scrollTop);
      }
    };
    let touchScrollTop0 = 0;
    const onTouchStart = () => {
      touchScrollTop0 = el.scrollTop;
    };
    const onTouchEnd = () => {
      if (!isStreamingRef.current) return;
      if (el.scrollTop < touchScrollTop0 - STREAMING_TOUCH_SCROLL_UP_THRESHOLD_PX) {
        isScrolledUpRef.current = true;
      }
    };
    el.addEventListener('wheel', onWheel, { passive: true });
    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchend', onTouchEnd, { passive: true });
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchend', onTouchEnd);
    };
  }, [runStreamingBottomResume]);

  // ═══════════════════════════════════════════════════════════════════
  //  SCROLL EFFECTS — Ordered by lifecycle priority
  // ═══════════════════════════════════════════════════════════════════

  // ── 0. Slot switch: save outgoing state, reset incoming ───────────
  // MUST be declared before the init-scroll effect so that
  // `hasPerformedInitialScrollRef` is reset before init checks it.
  useEffect(() => {
    const currentSlotId = activeSlotId;
    const prevSlotId = prevActiveSlotIdRef.current;

    // Save outgoing slot's scroll position + user lock
    if (prevSlotId && prevSlotId !== currentSlotId && scrollContainerRef.current) {
      useChatStore.getState().updateSlot(prevSlotId, {
        savedScrollTop: scrollContainerRef.current.scrollTop,
        userScrollOverride: isScrolledUpRef.current,
        // Record whether the slot was actively streaming at save-time.
        // Effect #1 uses this to decide whether to restore the position or
        // treat the chat as historical when we come back.
        savedScrollWasStreaming: isStreamingRef.current,
      });
    }

    // Reset scroll tracking for the incoming slot
    if (currentSlotId !== prevSlotId) {
      useInlineCitationPopoverStore.getState().setActiveKey(null);
      hasPerformedInitialScrollRef.current = false;
      isScrolledUpRef.current = false;
      runEndHoldRef.current?.stop();
      spacerHeldAtZeroRef.current = false;
      spacerFloorRef.current = 0;
      readerScrollTopRef.current = null;
      lockClearedAtMsRef.current = 0;
      lastTailSyncScrollTopRef.current = -1;
      // Align wasStreamingRef with the incoming slot's current streaming state
      // so Effect #5 (streaming-completion handler) doesn't fire spuriously.
      // Without this, wasStreamingRef retains the outgoing slot's value and
      // can trigger a completion scroll on the wrong chat — or miss the real
      // completion when we navigate back to a chat that finished while away.
      wasStreamingRef.current = isStreamingRef.current;
    }

    prevActiveSlotIdRef.current = currentSlotId;
  }, [activeSlotId]);

  // ── 1. Initialization & Activation: first scroll for a slot ───────
  // Fires when messages become available (isInitialized + pairs > 0)
  // after `hasPerformedInitialScrollRef` was reset by the slot-switch
  // effect above. Handles: historic load, switch to cached slot,
  // and switch into an actively streaming slot.
  useEffect(() => {
    if (!isInitialized || messagePairs.length === 0) return;
    if (hasPerformedInitialScrollRef.current) return;

    hasPerformedInitialScrollRef.current = true;

    // Check for saved scroll position (returning to a previously viewed chat)
    const store = useChatStore.getState();
    const slot = activeSlotId ? store.slots[activeSlotId] : null;
    const savedTop = slot?.savedScrollTop;

    // Determine whether the last message is actively streaming RIGHT NOW.
    // Hoisted above the savedTop block so it can be used in both branches.
    const lastPair = messagePairs[messagePairs.length - 1];
    const isCurrentlyStreaming = lastPair?.isStreaming ?? false;

    if (savedTop !== null && savedTop !== undefined) {
      // If the slot was streaming when we left but has since finished, the
      // savedTop is a mid-stream position that is now stale. Scroll to the
      // bottom of the container so the user sees the completed answer and
      // Ask More / action bar — matching where they'd be if they had stayed.
      const wasStreamingWhenSaved = slot?.savedScrollWasStreaming ?? false;
      if (wasStreamingWhenSaved && !isCurrentlyStreaming) {
        // Opening lands in one step, before the reader starts reading.
        spacerHeldAtZeroRef.current = true;
        recalcSpacerHeight();
        requestAnimationFrame(() => requestAnimationFrame(() => {
          executeScroll({
            target: 'bottom-of-container',
            behavior: 'auto',
            chatBecameActive: true,
          });
        }));
        return;
      } else {
        // Restore exact position without animation
        recalcSpacerHeight();
        requestAnimationFrame(() => {
          if (scrollContainerRef.current) {
            scrollContainerRef.current.scrollTo({ top: savedTop, behavior: 'auto' });
          }
          // Restore the user scroll-lock state from the slot
          isScrolledUpRef.current = slot?.userScrollOverride ?? false;
        });
        return;
      }
    }

    // No saved position (or stale mid-stream position discarded above) —
    // land on the end of the conversation in one step: the last answer and its
    // toolbar just above the composer, with no blank space added under them.
    // A run still streaming keeps following its tail.
    spacerHeldAtZeroRef.current = !isCurrentlyStreaming;
    recalcSpacerHeight();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      executeScroll({
        target: 'bottom-of-container',
        behavior: 'auto',
        chatBecameActive: true,
      });
    }));
  }, [isInitialized, messagePairs.length, activeSlotId, recalcSpacerHeight, executeScroll]);

  // ── 2. ResizeObserver on last message element ─────────────────────
  // Tracks the last message's DOM size changes (streaming content growing,
  // Ask More appearing, etc.) and runs the throttled spacer recalc +
  // bottom-tracking scroll.
  //
  // IMPORTANT: `messagePairs` (the full array) is intentionally NOT in the
  // deps — only `messagePairs.length` is. During streaming, `useAuiState()` may
  // return a new `threadMessages` reference on every render (even with the
  // same logical content), which would invalidate the `messagePairs` useMemo
  // and produce a new array reference ~60fps. If the full array were a dep,
  // this effect would re-run on every render, its cleanup would cancel the
  // pending rAF each time, and the ResizeObserver would never be connected.
  // We read the latest pairs via `messagePairsRef.current` inside the effect.
  //
  // The last pair's key IS a dep, and by value, so it costs nothing during
  // streaming (the placeholder assistant's key does not change while it
  // streams). It is needed because the last row can be replaced without the
  // count moving: Stop before the first token swaps the placeholder row for
  // the unanswered-question row, and without this the observer would stay on
  // the element that just left the DOM.
  useEffect(() => {
    // Clean up previous observer
    if (lastMessageObserverRef.current) {
      lastMessageObserverRef.current.disconnect();
      lastMessageObserverRef.current = null;
    }

    const pairs = messagePairsRef.current;

    if (pairs.length === 0) {
      lastMessageKeyRef.current = null;
      // Do NOT zero the spacer here. When the streaming→final message swap
      // happens, assistant-ui briefly produces 0 pairs then immediately
      // recovers to 1. If we zero the spacer in that window, scrollHeight
      // shrinks, the browser clamps scrollTop to 0, and the user sees a
      // jarring snap-to-top before effect #5 smooth-scrolls back down.
      // The spacer will be correctly recalculated by recalcSpacerHeight()
      // once pairs recover. On a genuine fresh conversation the spacer
      // starts at 0 already, so this omission is safe.
      return;
    }

    const lastPair = pairs[pairs.length - 1];
    lastMessageKeyRef.current = lastPair.key;

    // Wait a frame for the element to be in the DOM
    const rafId = requestAnimationFrame(() => {
      const element = messageRefs.current.get(lastPair.key);
      if (!element) {
        return;
      }

      // Initial calculation
      recalcSpacerHeight();

      // Observe size changes (streaming content growing) — throttled
      const observer = new ResizeObserver(() => {
        throttledResize();
      });
      observer.observe(element);
      lastMessageObserverRef.current = observer;
    });

    return () => {
      cancelAnimationFrame(rafId);
      if (streamingLayoutRafRef.current !== null) {
        cancelAnimationFrame(streamingLayoutRafRef.current);
        streamingLayoutRafRef.current = null;
      }
      if (lastMessageObserverRef.current) {
        lastMessageObserverRef.current.disconnect();
        lastMessageObserverRef.current = null;
      }
    };
  }, [messagePairs.length, lastPairKey, recalcSpacerHeight, throttledResize]);

  useEffect(() => {
    if (messagePairs.length === 0) setShowJumpToNewest(false);
  }, [messagePairs.length]);

  // ── 3. ResizeObserver on scroll container (window resize) and composer dock ──
  const hasComposer = composer != null;
  // Measured before paint: the blur is capped at this height, so a first frame at 0 would show
  // the last lines of the transcript unblurred until the effect below ran.
  useLayoutEffect(() => {
    setComposerDockHeight(composerDockRef.current?.offsetHeight ?? 0);
  }, [hasComposer]);
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const measureDock = () => setComposerDockHeight(composerDockRef.current?.offsetHeight ?? 0);
    measureDock();
    const observer = new ResizeObserver(() => {
      recalcSpacerHeight();
      measureDock();
    });
    observer.observe(container);
    if (composerDockRef.current) observer.observe(composerDockRef.current);

    return () => observer.disconnect();
  }, [recalcSpacerHeight, hasComposer]);

  // ── 4. New message pair added (user sends a message) ──────────────
  useEffect(() => {
    // Read current first pair key via the ref-mirror (always current in effects)
    const currentFirstKey = messagePairsRef.current[0]?.key ?? null;

    if (!hasPerformedInitialScrollRef.current) {
      // Haven't done initial scroll yet — skip to avoid double-scrolling
      prevPairCountRef.current = messagePairs.length;
      prevFirstPairKeyRef.current = currentFirstKey;
      return;
    }

    const pairsIncreased =
      messagePairs.length > prevPairCountRef.current &&
      // Guard: prevPairCount > 0 prevents a transient 0-pairs state (caused by
      // assistant-ui's streaming→final message replacement) from being mistaken
      // for a freshly sent message, which would incorrectly snap scroll to top.
      prevPairCountRef.current > 0 &&
      messagePairs.length > 0;

    if (pairsIncreased) {
      // Distinguish user-send (append) from load-older (prepend):
      // - Append: first pair key is UNCHANGED (new pair went to the end)
      // - Prepend: first pair key CHANGED (older pairs inserted at the front)
      const isAppend = currentFirstKey === prevFirstPairKeyRef.current;

      if (isAppend) {
        // A new question brings back the space that lets it reach the top.
        spacerHeldAtZeroRef.current = false;
        recalcSpacerHeight();
        // A send that started a run is already gliding (stream-start effect above).
        if (sendGlideRafRef.current === null && !isStreamingRef.current) {
          // User actively sent a message → bypass scroll lock
          isScrolledUpRef.current = false;
          glideToLastMessageTop();
        }
      }
      // Prepend: browser's native overflow-anchor preserves the viewport position.
    }

    prevPairCountRef.current = messagePairs.length;
    prevFirstPairKeyRef.current = currentFirstKey;
  }, [messagePairs.length, recalcSpacerHeight, glideToLastMessageTop]);

  // ── 5. Streaming completion ───────────────────────────────────────
  // ChatGPT/Claude-style: after the last token, do **not** auto smooth-scroll
  // to the bottom. Users who were reading up-thread or viewing a citation
  // would be jumped away from that context. The hold at the answer's end
  // keeps the answer in place through the swap and the fold; we just refresh the spacer.
  useEffect(() => {
    if (wasStreamingRef.current && !isStreaming) {
      const raf = requestAnimationFrame(() => {
        recalcSpacerHeight();
      });
      wasStreamingRef.current = isStreaming;
      return () => cancelAnimationFrame(raf);
    }
    wasStreamingRef.current = isStreaming;
  }, [isStreaming, recalcSpacerHeight]);

  // ── Ask More: randomly pick one question set per new message pair ──
  // TODO: Move this to the backend, also remove from il8n jsons since it's meant to be dynamic content
  const askMoreSetIndexRef = useRef<{ count: number; index: number }>({ count: -1, index: 0 });
  if (askMoreSetIndexRef.current.count !== messagePairs.length) {
    askMoreSetIndexRef.current = {
      count: messagePairs.length,
      // eslint-disable-next-line react-hooks/purity -- intentional render-time ref update: pick a stable random set per message-pair count change
      index: Math.floor(Math.random() * 1_000_000),
    };
  }
  const askMoreQuestions = useMemo(() => {
    if (messagePairs.length === 0) return [];
    // t() with returnObjects:true is unreliable for nested arrays in strict TS —
    // read the resource bundle directly to get the raw JSON array.
    const bundle = i18n.getResourceBundle(i18n.language, 'translation') as Record<string, unknown> | undefined;
    const sets = (bundle?.chat as Record<string, unknown> | undefined)?.askMoreQuestionSets as string[][] | undefined;
    const activeSets = Array.isArray(sets) && sets.length > 0 ? sets : ASK_MORE_QUESTION_SETS;
    const setIndex = askMoreSetIndexRef.current.index % activeSets.length;
    return activeSets[setIndex];
  }, [messagePairs.length, i18n.language]);

  // Whether to show Ask More suggestions
  const showAskMore = messagePairs.length > 0 && !isStreaming && !isLoadingConversation;

  // Handle Ask More question click — send through runtime
  const handleAskMoreClick = useCallback(
    (question: string) => {
      aui.thread().append({
        role: 'user',
        content: [{ type: 'text', text: question }],
        startRun: true,
      });
    },
    [aui]
  );

  // Lands on the end of the latest message and, if an answer is streaming, re-arms tail-follow.
  const handleJumpToNewest = useCallback(() => {
    lastTailSyncScrollTopRef.current = -1;
    executeScroll({
      target: 'bottom-of-container',
      behavior: 'smooth',
      chatBecameActive: true,
    });
  }, [executeScroll]);

  const { showComposer } = chatSlots.useChatAccess(activeSlotId);
  const handleRetry = useCallback(
    (turn: RetryTurn) => aui.thread().append(retryTurnMessage(turn)),
    [aui]
  );

  const responseContext: ChatResponseContext = {
    citationCallbacks,
    streaming,
    latestArtifactVersions,
    canSend: showComposer,
    onRetry: handleRetry,
  };
  const renderResponse = (pair: MessagePair, isLast: boolean, layout?: ChatResponseLayout) => (
    <ErrorBoundary
      resetKeys={[pair.key, pair.messageId]}
      fallback={(error, reset) => <ErrorFallback error={error} reset={reset} compact />}
    >
      <ChatResponse {...chatResponseProps(pair, isLast, responseContext, layout)} renderKey={renderKeyOf(pair.key)} />
    </ErrorBoundary>
  );

  const body = holdingBody && heldBodyRef.current.body ? heldBodyRef.current.body : (
    <chatSlots.MessageListBody
      pairs={messagePairs}
      isStreaming={isStreaming}
      renderResponse={renderResponse}
      setMessageRef={setMessageRef}
      renderDefault={() => messagePairs.map((pair, index) => {
        const isLast = index === messagePairs.length - 1;
        return (
          <div
            key={renderKeyOf(pair.key)}
            ref={(el) => setMessageRef(pair.key, el)}
            // The column beside the chat reads which answer is on screen by this.
            data-answer-id={pair.key}
            // A stop on the question rail.
            data-landmark={questionLandmark(chatExtension.question.toPlainText(pair.question))}
          >
            {renderResponse(pair, isLast)}

            {/* Ask More — follow-up suggestions after the last bot response.
                Placed inside the last message wrapper so the ResizeObserver
                accounts for its height in the spacer calculation.

                TEMPORARILY DISABLED: the follow-up questions are hardcoded
                (see `ASK_MORE_QUESTION_SETS` in ../../constants) and are not
                yet generated from the actual conversation. Re-enable once
                the suggestions are dynamically produced by the backend. */}
            {/*
            {isLast && showAskMore && (
              <Box style={{ marginTop: 'var(--space-6)' }}>
                <AskMore
                  questions={askMoreQuestions}
                  onQuestionClick={handleAskMoreClick}
                />
              </Box>
            )}
            */}
          </div>
        );
      })}
    />
  );
  // The rows drawn now: what the list keeps on screen through the in-between moment at a run's end.
  useLayoutEffect(() => {
    if (holdingBody || betweenRows) return;
    heldBodyRef.current = { slotId: activeSlotId, body };
  });
  useEffect(() => {
    if (!holdingBody) return;
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setHoldExpired(true));
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, [holdingBody]);

  // The button belongs to the viewport, so it sits beside the scroller, not inside it.
  return (
    <Box style={{ position: 'relative', flex: 1, minHeight: 0, width: '100%', display: 'flex', flexDirection: 'column' }}>
    <Box
      ref={scrollContainerRef}
      onScroll={handleScroll}
      className="chat-message-scroll"
      style={{
        // Full chat-panel width so the scrollbar sits on the pane edge, not
        // against the message column. Content is centered in the shared column.
        flex: 1,
        minHeight: 0,
        width: '100%',
        overflowY: 'auto',
        overflowX: 'hidden',
        overscrollBehavior: 'contain',
        // Never smooth here: the list corrects its own position, and a smooth
        // correction would show as a slide. Moves that should glide ask for it.
        scrollBehavior: 'auto',
        // The tail is pinned manually during streaming (syncStreamingMessageTail
        // writes scrollTop every frame) — native scroll anchoring would otherwise
        // fight those writes whenever streamed table/content geometry shifts.
        overflowAnchor: isStreaming ? 'none' : 'auto',
        // Column so the composer dock can sit at the bottom of a short transcript.
        display: 'flex',
        flexDirection: 'column',
        // Under the top bar: the transcript scrolls beneath its fade; at rest it starts below it.
        paddingTop: topInset,
      }}
    >
      <Box
        ref={contentColumnRef}
        // Side gutter from the shared column class (16px each side), so it holds when a preview narrows the pane.
        className="chat-column-gutter"
        style={{
          ...chatMessageColumnStyle(),
          paddingLeft: undefined,
          paddingRight: undefined,
          flexShrink: 0,
          margin: '0 auto',
          paddingTop: 'var(--space-4)',
          // With the composer's own 12px above it, the last answer's actions end 60px over the composer card:
          // clear of the fade behind it, as in the design, so at the end they are sharp.
          paddingBottom: 'var(--space-8)',
        }}
      >
        <Flex direction="column" gap="6">
          {/* Loading older messages indicator — shown at the very top while fetching */}
          {isLoadingOlder && (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-4)', flexShrink: 0 }}>
              <LottieLoader variant="loader" size={32} />
            </Flex>
          )}

          {isLoadingConversation && (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
              <LottieLoader variant="loader" size={48} showLabel />
            </Flex>
          )}

          <RowRenderKeyContext.Provider value={renderKeyOf}>
            <BeforeCommit read={readBeforeRunEnd}>
              <AnswerPanelScope.Provider value={panelScope}>{body}</AnswerPanelScope.Provider>
            </BeforeCommit>
          </RowRenderKeyContext.Provider>
        </Flex>
      </Box>

      {/* Dynamic bottom spacer: ensures the last message can always be scrolled to
          the top even when its content is shorter than the viewport.
          Always rendered (never conditional) so the ref is stable for direct
          DOM mutation by recalcSpacerHeight — avoids React rerenders. */}
      <div
        ref={spacerRef}
        style={{
          minHeight: 0,
          flexShrink: 0,
        }}
        aria-hidden="true"
      />

      {/* Inside the scroller, not a sibling: the scrollbar runs to the bottom of
          the pane, the transcript passes behind the composer, and a wheel over
          the composer scrolls the transcript. */}
      {hasComposer && (
        <Box
          ref={composerDockRef}
          style={{
            position: 'sticky',
            bottom: 0,
            marginTop: 'auto',
            flexShrink: 0,
            zIndex: 2,
            overflowAnchor: 'none',
            // Only the composer column takes clicks; the gutters on either side
            // leave the transcript underneath selectable.
            pointerEvents: 'none',
          }}
        >
          {/* The transcript fades into the page behind the composer. Capped at the dock: a taller band would sit over
              the last lines the reader is following as an answer streams in. Static, and it never takes a click. */}
          <ProgressiveBlur height={Math.min(COMPOSER_BLUR_HEIGHT_PX, composerDockHeight)} scrim="var(--surface-raised)" />
          <Flex
            direction="column"
            align="center"
            style={{
              position: 'relative',
              paddingBottom: isMobile ? 'max(var(--space-2), env(safe-area-inset-bottom, 0px))' : 0,
            }}
          >
            {composer}
          </Flex>
        </Box>
      )}

      {/* Single inline-citation popover host for the whole message list. It's
          driven by the zustand store so only one popover is ever rendered,
          even when the answer contains many citation badges. */}
      <InlineCitationPopoverHost />
    </Box>

    {!isBelowDesktop && (
      <QuestionRail
        scrollerRef={scrollContainerRef}
        label={t('chat.questionRail')}
        // A list the page starts below the bar has its first question at the column's own padding. Under the bar, the
        // rail's default.
        top={topInset ? undefined : 'var(--space-4)'}
        dockHeight={composerDockHeight}
        // The reader's own move to a question: one glide, or one step with reduced motion.
        onJump={(row) => {
          const container = scrollContainerRef.current;
          if (!container) return;
          // Rests below the list's top inset, as a question brought to the top by a send does.
          const inset = questionRestOffset(container);
          const top = row.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - inset;
          container.scrollTo({ top: Math.max(0, top), behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
        }}
      />
    )}

    {showJumpToNewest && (
      <Flex
        justify="center"
        style={{
          position: 'absolute',
          insetInline: 0,
          // 8px above the composer card: the dock's height less the composer column's 12px top padding.
          bottom: `calc(${composerDockHeight}px - var(--space-3) + var(--space-2))`,
          zIndex: 2,
          // Only the pill takes the pointer; the transcript beside it stays selectable.
          pointerEvents: 'none',
        }}
      >
        <JumpToLatest label={t('chat.latestMessages')} onJump={handleJumpToNewest} />
      </Flex>
    )}
    </Box>
  );
}

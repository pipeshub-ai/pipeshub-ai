'use client';

import React, { useEffect, useLayoutEffect, useRef, useMemo, useCallback } from 'react';
import { useThread, useThreadRuntime } from '@assistant-ui/react';
import { Flex, Box } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ChatResponse } from './chat-response';
import { AskMore } from './ask-more';
import { useChatStore } from '../../store';
import { debugLog } from '../../debug-logger';
import { ASK_MORE_QUESTION_SETS } from '../../constants';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { AppliedFilters, ChatArtifact } from '../../types';
import type { ConfidenceLevel, ModelInfo } from '../../types';
import type { CitationMaps } from './response-tabs/citations';
import { emptyCitationMaps, useCitationActions, isCitationPopoverKeyStillValid } from './response-tabs/citations';
import { useInlineCitationPopoverStore } from './response-tabs/citations/citation-popover-store';
import { InlineCitationPopoverHost } from './response-tabs/citations/inline-citation-popover-host';
import { LottieLoader } from '@/app/components/ui/lottie-loader';

// Stable empty references to avoid re-renders from selector fallbacks.
// `?? []` or `?? null` in a selector body creates a new ref every call,
// defeating Object.is comparison.
const EMPTY_ARRAY: never[] = [];
const STABLE_EMPTY_ARTIFACTS: ChatArtifact[] = [];
const CHAT_INPUT_RESERVED = 160; // height reserved for the chat input overlay
const EMPTY_STRING = '';
const EMPTY_CITATION_MAPS: CitationMaps = emptyCitationMaps();

/**
 * Extract text content from assistant-ui message content array
 */
function extractTextContent(content: readonly { type: string; text?: string }[]): string {
  return content
    .filter((part) => part.type === 'text' && part.text)
    .map((part) => part.text)
    .join('');
}


interface MessagePair {
  key: string;
  /** Backend _id of the bot_response message (used for regenerate) */
  messageId?: string;
  question: string;
  answer: string;
  citationMaps: CitationMaps;
  confidence?: ConfidenceLevel;
  isStreaming: boolean;
  modelInfo?: ModelInfo;
  feedbackInfo?: { value?: 'like' | 'dislike' };
  /** Collections attached to this message (from user message metadata) */
  collections?: Array<{ id: string; name: string }>;
  appliedFilters?: AppliedFilters;
  /** ISO timestamp of when the user sent this query */
  createdAt?: string;
}

export function MessageList() {
  // ── Slot-scoped selectors (narrow — only active slot fields) ──
  const isStreaming = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.isStreaming ?? false : false
  );
  const streamingQuestion = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.streamingQuestion || EMPTY_STRING : EMPTY_STRING
  );
  const streamingCitationMaps = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.streamingCitationMaps ?? null : null
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
  const isLoadingConversation = useChatStore((s) =>
    s.activeSlotId ? !s.slots[s.activeSlotId]?.isInitialized : false
  );
  // streamingContent + currentStatusMessage are now passed as props to ChatResponse
  // (only the streaming instance gets non-empty values). Previously ChatResponse
  // subscribed to these directly, causing ALL instances to re-render on every rAF flush.
  const streamingContent = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.streamingContent || EMPTY_STRING : EMPTY_STRING
  );
  const currentStatusMessage = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.currentStatusMessage ?? null : null
  );
  const streamingArtifacts = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.artifacts ?? STABLE_EMPTY_ARTIFACTS : STABLE_EMPTY_ARTIFACTS
  );

  // ── Render-reason tracking ──────────────────────────────────────
  debugLog.tick('[chat] [MessageList]');
  const prevMsgListRef = useRef<Record<string, unknown>>({});
  const currentMsgListVals: Record<string, unknown> = {
    isStreaming, streamingQuestion, streamingCitationMaps,
    pendingCollections, regenerateMessageId, isInitialized, isLoadingConversation,
    streamingContent, currentStatusMessage,
  };
  const msgListReasons: string[] = [];
  for (const [k, v] of Object.entries(currentMsgListVals)) {
    if (!Object.is(v, prevMsgListRef.current[k])) msgListReasons.push(k);
  }
  if (msgListReasons.length > 0) {
    debugLog.reason('[chat] [MessageList]', msgListReasons);
  }
  prevMsgListRef.current = currentMsgListVals;

  // ── Active slot ID (needed for save/restore on conversation switch) ──
  const activeSlotId = useChatStore((s) => s.activeSlotId);

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const spacerRef = useRef<HTMLDivElement>(null);
  const citationCallbacks = useCitationActions();
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const prevPairCountRef = useRef(0);
  const lastMessageObserverRef = useRef<ResizeObserver | null>(null);
  const lastMessageKeyRef = useRef<string | null>(null);
  // Tracks the last scroll position during streaming so we can restore it
  // if the atomic content replacement (streaming→final) causes a position jump.
  const streamingScrollTopRef = useRef<number>(0);

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
  // Render-time sync (not useEffect) so the value is current when
  // synchronous ResizeObserver callbacks fire in the same commit phase.
  const isStreamingRef = useRef(isStreaming);
  isStreamingRef.current = isStreaming;
  // Timestamp (ms) of the last time we treated the viewport as pinned to the
  // live tail (`distFromBottom` in the bottom zone). Crossing into the "reading
  // up" zone within a short window after that is ignored so bounce / layout
  // jitter does not flip `isScrolledUp` while we still intend to follow SSE.
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

  // Render-time: record scroll position on every render during streaming so
  // the useLayoutEffect below always has the freshest value to restore from.
  if (isStreaming && scrollContainerRef.current) {
    streamingScrollTopRef.current = scrollContainerRef.current.scrollTop;
  }

  const { i18n } = useTranslation();
  const isMobile = useIsMobile();

  // Use useThread to get reactive thread state
  const thread = useThread();
  const threadRuntime = useThreadRuntime();

  // Track thread.messages changes as a render reason
  const prevThreadMsgsRef = useRef(thread.messages);
  if (thread.messages !== prevThreadMsgsRef.current) {
    debugLog.reason('[chat] [MessageList]', ['thread.messages']);
    prevThreadMsgsRef.current = thread.messages;
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
  const messagePairs = useMemo<MessagePair[]>(() => {
    const pairs: MessagePair[] = [];
    const messages = thread.messages;
    // Only the *last* assistant in the thread can be the live SSE target for a
    // new send. (Never use `!content` alone: agent threads can retain empty
    // `content` on older rows after a bad load or edge case, which would paint
    // the current stream + citations onto every such row.)
    let lastAssistantIndex = -1;
    for (let j = 0; j < messages.length; j += 1) {
      if (messages[j].role === 'assistant') {
        lastAssistantIndex = j;
      }
    }

    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      if (msg.role === 'assistant') {
        const content = extractTextContent(msg.content as { type: string; text?: string }[]);

        const metadata = (msg as { metadata?: { custom?: {
          messageId?: string;
          citationMaps?: CitationMaps;
          confidence?: ConfidenceLevel;
          modelInfo?: ModelInfo;
          feedbackInfo?: { value?: 'like' | 'dislike' };
        } } }).metadata?.custom as {
          messageId?: string;
          citationMaps?: CitationMaps;
          confidence?: ConfidenceLevel;
          modelInfo?: ModelInfo;
          feedbackInfo?: { value?: 'like' | 'dislike' };
        } | undefined;

        // Find preceding user message
        const prevMsg = i > 0 ? messages[i - 1] : null;
        const question = prevMsg?.role === 'user'
          ? extractTextContent(prevMsg.content as { type: string; text?: string }[])
          : 'Question';

        // Check if this message is being regenerated
        const isBeingRegenerated = !!regenerateMessageId && metadata?.messageId === regenerateMessageId;

        // Live stream attaches only to the last assistant in the thread when
        // its user message matches the query we sent (see placeholder assistant
        // in `streamMessageForSlot`).
        const isLastAssistant = i === lastAssistantIndex;
        const isCurrentlyStreaming =
          isStreaming && isLastAssistant && question === streamingQuestion;

        // appliedFilters from the preceding user message metadata
        const userMsgCustom = prevMsg?.metadata?.custom as {
          collections?: Array<{ id: string; name: string }>;
          appliedFilters?: AppliedFilters;
          createdAt?: string;
        } | undefined;
        const userMessageCollections = userMsgCustom?.collections as Array<{ id: string; name: string }> | undefined;
        const userMessageAppliedFilters = userMsgCustom?.appliedFilters as AppliedFilters | undefined;
        const userCreatedAt = userMsgCustom?.createdAt;


        pairs.push({
          key: msg.id ?? `asst-${i}`,
          messageId: metadata?.messageId,
          question,
          answer: isBeingRegenerated ? '' : content,
          citationMaps: (isCurrentlyStreaming || isBeingRegenerated)
            ? EMPTY_CITATION_MAPS
            : (metadata?.citationMaps || EMPTY_CITATION_MAPS),
          confidence: metadata?.confidence,
          isStreaming: isCurrentlyStreaming || isBeingRegenerated,
          modelInfo: metadata?.modelInfo,
          feedbackInfo: metadata?.feedbackInfo,
          // Use streaming collections for the temp message; user metadata for the final message
          collections: isCurrentlyStreaming
            ? (pendingCollections.length > 0 ? pendingCollections : userMessageCollections)
            : userMessageCollections,
          appliedFilters: userMessageAppliedFilters,
          createdAt: userCreatedAt,
        });
      }
    }

    return pairs;
  }, [thread.messages, isStreaming, streamingQuestion, pendingCollections, regenerateMessageId]);

  // Ref-mirror of messagePairs — lets scroll effects read the latest pairs
  // without having the full array in their dependency list (which would cause
  // effect #2 to re-run and cancel its rAF on every render during streaming).
  const messagePairsRef = useRef(messagePairs);
  messagePairsRef.current = messagePairs;

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
    const assistantMsgs = thread.messages.filter(m => m.role === 'assistant').length;
    const userMsgs = thread.messages.filter(m => m.role === 'user').length;
    if (messagePairs.length === 0 && thread.messages.length > 0) {
      console.warn(
        '[debugging] [MessageList] 0 pairs but',
        thread.messages.length, 'thread msgs (user:', userMsgs, ', assistant:', assistantMsgs, ')',
        'isStreaming:', isStreaming
      );
    }
  }

  // ── Spacer recalculation (direct DOM mutation — no React state) ────
  // During streaming, ResizeObserver fires ~60/sec. Writing to a ref
  // avoids a React rerender on every frame. The spacer div always exists
  // in the DOM (never conditionally rendered) so the ref is stable.
  const recalcSpacerHeight = useCallback(() => {
    if (messageRefs.current.size === 0 || !lastMessageKeyRef.current || !scrollContainerRef.current) {
      if (spacerRef.current) spacerRef.current.style.minHeight = '0';
      debugLog.spacer('no messages or no container → height=0');
      return;
    }

    const element = messageRefs.current.get(lastMessageKeyRef.current);
    const container = scrollContainerRef.current;

    if (!element) {
      if (spacerRef.current) spacerRef.current.style.minHeight = '0';
      debugLog.spacer('no element for last message → height=0');
      return;
    }

    const containerHeight = container.clientHeight;
    const lastMessageRect = element.getBoundingClientRect();
    const totalScrollHeight = container.scrollHeight;
    const isOverflowing = totalScrollHeight > containerHeight;

    let needed = 0;
    // On mobile: skip the spacer entirely. The mobile chat area is small enough
    // that the "scroll last message to top" affordance adds far more blank space
    // than it saves — the Figma mobile spec shows content flush against the input.
    // On desktop: add spacer only when content overflows or user has scrolled
    // away from the top (avoids unnecessary scrollbar on short conversations).
    if (!isMobileRef.current && (isOverflowing || container.scrollTop > 0)) {
      needed = Math.max(0, containerHeight - lastMessageRect.height);
    }

    if (spacerRef.current) {
      spacerRef.current.style.minHeight = `${needed}px`;
    }
    debugLog.spacer(`containerH=${containerHeight} lastMsgH=${Math.round(lastMessageRect.height)} overflow=${isOverflowing} → spacer=${needed}`);
  }, []);

  // Ref filled after `syncStreamingMessageTail` is defined — see assignment below.
  const syncStreamingMessageTailRef = useRef<() => void>(() => {});

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
      const topBuffer = parseFloat(window.getComputedStyle(container).paddingTop) || 0;
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
    if (options.behavior === 'smooth' || options.behavior === '80/20-jump') {
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
    if (options.behavior === '80/20-jump') {
      // Instant jump to 80%, then smooth glide to 100%
      container.scrollTo({ top: targetScrollTop * 0.8, behavior: 'auto' });
      requestAnimationFrame(() => {
        container.scrollTo({ top: targetScrollTop, behavior: 'smooth' });
      });
    } else if (options.behavior === 'auto') {
      // Direct assignment: avoids global `scroll-behavior: smooth` affecting scrollTo
      container.scrollTop = targetScrollTop;
    } else {
      container.scrollTo({ top: targetScrollTop, behavior: options.behavior });
    }
  }, []);

  /**
   * Instant tail-follow while SSE is active: runs after DOM/layout knows the new
   * message height, without going through executeScroll’s timers. Paired with
   * useLayoutEffect (same frame as streaming markdown commit) so the viewport
   * does not paint one frame “behind” the growing answer.
   */
  const syncStreamingMessageTail = useCallback(() => {
    if (!isStreamingRef.current || isScrolledUpRef.current) return;

    const container = scrollContainerRef.current;
    const key = lastMessageKeyRef.current;
    const lastEl = key ? messageRefs.current.get(key) : null;
    if (!container || !lastEl) return;

    recalcSpacerHeight();

    const msgHeight = lastEl.getBoundingClientRect().height;
    const chatInputReserved = isMobileRef.current ? 120 : CHAT_INPUT_RESERVED;
    const visibleHeight = container.clientHeight - chatInputReserved;

    isAutoScrollingRef.current = true;
    let nextTop: number;
    if (msgHeight <= visibleHeight) {
      const containerRect = container.getBoundingClientRect();
      const elementRect = lastEl.getBoundingClientRect();
      const topBuffer = parseFloat(window.getComputedStyle(container).paddingTop) || 0;
      nextTop = Math.max(0, elementRect.top - containerRect.top + container.scrollTop - topBuffer);
    } else {
      nextTop = container.scrollHeight - container.clientHeight;
    }
    container.scrollTop = nextTop;
    lastTailSyncScrollTopRef.current = container.scrollTop;
    queueMicrotask(() => {
      isAutoScrollingRef.current = false;
    });
  }, [recalcSpacerHeight]);

  syncStreamingMessageTailRef.current = syncStreamingMessageTail;

  // ── User scroll detection ─────────────────────────────────────────
  // Streaming: opt out when `scrollTop` drops meaningfully below the last
  // tail-sync position; resume only when flush with the doc bottom (tight
  // `dist` or at max scroll). Resume MUST run before the opt-out check so a
  // stale anchor cannot block “scroll back down to follow.” On resume, refresh
  // the anchor and run tail-sync on rAF so the next chunk is not the first time
  // we snap.
  const STREAMING_RESUME_DIST_FLUSH_PX = 4;
  const STREAMING_RESUME_AT_MAX_EPS_PX = 3;
  const STREAMING_OPT_OUT_ANCHOR_PX = 4;
  const IDLE_TAIL_BOTTOM_ZONE_PX = 80;
  const IDLE_SCROLLED_UP_ZONE_PX = 96;
  const handleScroll = useCallback(() => {
    if (!scrollContainerRef.current) return;

    const container = scrollContainerRef.current;
    const { scrollTop, scrollHeight, clientHeight } = container;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;

    if (isStreamingRef.current) {
      const maxScrollTop = Math.max(0, scrollHeight - clientHeight);
      const flushWithBottom =
        distFromBottom <= STREAMING_RESUME_DIST_FLUSH_PX ||
        scrollTop >= maxScrollTop - STREAMING_RESUME_AT_MAX_EPS_PX;

      // User can reach the bottom while programmatic tail-sync still holds the
      // guard flag; always evaluate resume so follow turns back on.
      if (isAutoScrollingRef.current && !flushWithBottom) return;

      if (flushWithBottom) {
        isScrolledUpRef.current = false;
        lastTailSyncScrollTopRef.current = scrollTop;
        requestAnimationFrame(() => {
          if (!isStreamingRef.current || isScrolledUpRef.current) return;
          syncStreamingMessageTailRef.current();
        });
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
      if (msSinceClear > 160) {
        isScrolledUpRef.current = true;
      }
    }
  }, []);

  // Stream start: clear scroll lock in layout phase (before paint), not in useEffect,
  // so the first tail sync is not skipped. On every streaming commit, pin the tail
  // before paint so the viewport never lags one frame behind the markdown.
  useLayoutEffect(() => {
    if (isStreaming && !prevIsStreamingForLockRef.current) {
      isScrolledUpRef.current = false;
      lastTailSyncScrollTopRef.current = -1;
    }
    prevIsStreamingForLockRef.current = isStreaming;

    if (!isStreaming) return;
    syncStreamingMessageTail();
  }, [
    isStreaming,
    streamingContent,
    currentStatusMessage?.id,
    currentStatusMessage?.message,
    streamingCitationMaps,
    syncStreamingMessageTail,
  ]);

  // ── rAF-coalesced resize handler for ResizeObserver ───────────────
  // Spacer + tail on size changes that are not always tied to store updates
  // (code blocks, images, fonts). While streaming, tail sync also runs from
  // useLayoutEffect above; this path catches layout-only growth the same frame.
  //
  // Streaming Tracker: short message → title at top; tall → bottom edge.
  const throttledResize = useMemo(() => {
    return () => {
      if (streamingLayoutRafRef.current !== null) return;
      streamingLayoutRafRef.current = requestAnimationFrame(() => {
        streamingLayoutRafRef.current = null;
        if (isStreamingRef.current) {
          syncStreamingMessageTail();
        } else {
          recalcSpacerHeight();
        }
      });
    };
  }, [recalcSpacerHeight, syncStreamingMessageTail]);

  // ── Cleanup auto-scroll timeout on unmount ──
  useEffect(() => {
    return () => {
      if (autoScrollTimeoutRef.current) clearTimeout(autoScrollTimeoutRef.current);
      if (streamingLayoutRafRef.current !== null) {
        cancelAnimationFrame(streamingLayoutRafRef.current);
        streamingLayoutRafRef.current = null;
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
      if (ev.deltaY < 0) {
        isScrolledUpRef.current = true;
        return;
      }
      if (ev.deltaY > 0) {
        const { scrollTop, scrollHeight, clientHeight } = el;
        const dist = scrollHeight - scrollTop - clientHeight;
        const maxST = Math.max(0, scrollHeight - clientHeight);
        const flush = dist <= 4 || scrollTop >= maxST - 3;
        if (flush) {
          isScrolledUpRef.current = false;
          lastTailSyncScrollTopRef.current = scrollTop;
          requestAnimationFrame(() => {
            if (!isStreamingRef.current || isScrolledUpRef.current) return;
            syncStreamingMessageTailRef.current();
          });
        }
      }
    };
    let touchScrollTop0 = 0;
    const onTouchStart = () => {
      touchScrollTop0 = el.scrollTop;
    };
    const onTouchEnd = () => {
      if (!isStreamingRef.current) return;
      if (el.scrollTop < touchScrollTop0 - 8) {
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
  }, []);

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
        recalcSpacerHeight();
        requestAnimationFrame(() => requestAnimationFrame(() => {
          executeScroll({
            target: 'bottom-of-container',
            behavior: 'smooth',
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
    // do initial scroll.

    recalcSpacerHeight();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      executeScroll({
        target: isCurrentlyStreaming ? 'bottom-of-container' : 'top-of-last-message',
        behavior: isCurrentlyStreaming ? 'auto' : '80/20-jump',
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
  // deps — only `messagePairs.length` is. During streaming, `useThread()` may
  // return a new `thread.messages` reference on every render (even with the
  // same logical content), which would invalidate the `messagePairs` useMemo
  // and produce a new array reference ~60fps. If the full array were a dep,
  // this effect would re-run on every render, its cleanup would cancel the
  // pending rAF each time, and the ResizeObserver would never be connected.
  // We read the latest pairs via `messagePairsRef.current` inside the effect.
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
  }, [messagePairs.length, recalcSpacerHeight, throttledResize]);

  // ── 3. ResizeObserver on scroll container (window resize) ─────────
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const observer = new ResizeObserver(() => {
      recalcSpacerHeight();
    });
    observer.observe(container);

    return () => observer.disconnect();
  }, [recalcSpacerHeight]);

  // ── 3b. Streaming→final scroll-position guard ────────────────────
  // When the assistant-ui runtime atomically swaps the streaming message for
  // the final thread message, React may briefly see 0 pairs then 1 pair again.
  //
  // During that transient 0-pairs state the message DOM disappears,
  // scrollHeight shrinks, and the browser clamps scrollTop to a lower
  // value (often 0). A plain `container.scrollTop = saved` would just
  // get re-clamped if scrollHeight is too small.
  //
  // Strategy:
  //   1. Temporarily inflate the spacer so scrollHeight stays large
  //      enough to support the target scrollTop.
  //   2. Restore scrollTop before the browser paints.
  //   3. Depend on BOTH isStreaming AND messagePairs.length so this
  //      fires for the initial 0-pairs render AND the recovery render.
  //   4. Effect #5 resets streamingScrollTopRef and recalcs the spacer
  //      once the transition has fully settled.
  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    const spacer = spacerRef.current;
    if (!container || !spacer) return;

    const targetTop = streamingScrollTopRef.current;
    if (isStreaming || targetTop <= 0) return;

    // Ensure scrollHeight is large enough so scrollTop won't be clamped
    const neededScrollHeight = targetTop + container.clientHeight;
    const deficit = neededScrollHeight - container.scrollHeight;
    if (deficit > 0) {
      const currentSpacer = parseFloat(spacer.style.minHeight) || 0;
      spacer.style.minHeight = `${currentSpacer + deficit + 50}px`;
    }

    // Restore exact scroll position before paint
    container.scrollTop = targetTop;
  }, [isStreaming, messagePairs.length]);

  // ── 4. New message pair added (user sends a message) ──────────────
  useEffect(() => {
    if (!hasPerformedInitialScrollRef.current) {
      // Haven't done initial scroll yet — skip to avoid double-scrolling
      prevPairCountRef.current = messagePairs.length;
      return;
    }
    if (
      messagePairs.length > prevPairCountRef.current &&
      // Guard: prevPairCount > 0 prevents a transient 0-pairs state (caused by
      // assistant-ui's streaming→final message replacement) from being mistaken
      // for a freshly sent message, which would incorrectly snap scroll to top.
      prevPairCountRef.current > 0 &&
      messagePairs.length > 0
    ) {
      recalcSpacerHeight();
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          // User actively sent a message → bypass scroll lock
          executeScroll({
            target: 'top-of-last-message',
            behavior: 'smooth',
            chatBecameActive: true,
          });
        });
      });
    }
    prevPairCountRef.current = messagePairs.length;
  }, [messagePairs.length, executeScroll, recalcSpacerHeight]);

  // ── 5. Streaming completion ───────────────────────────────────────
  // ChatGPT/Claude-style: after the last token, do **not** auto smooth-scroll
  // to the bottom. Users who were reading up-thread or viewing a citation
  // would be jumped away from that context. 3b+layout already preserve
  // scroll through the final message replace; we just refresh spacer + reset
  // the scroll-position ref used by 3b.
  useEffect(() => {
    if (wasStreamingRef.current && !isStreaming) {
      const raf = requestAnimationFrame(() => {
        recalcSpacerHeight();
        streamingScrollTopRef.current = 0;
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
      threadRuntime.append({
        role: 'user',
        content: [{ type: 'text', text: question }],
        startRun: true,
      });
    },
    [threadRuntime]
  );

  return (
    <Box
      ref={scrollContainerRef}
      onScroll={handleScroll}
      style={{
        flex: 1,
        overflowY: 'auto',
        overscrollBehavior: 'contain',
        // Instant programmatic follow while tokens arrive; smooth when idle / completed.
        scrollBehavior: isStreaming ? 'auto' : 'smooth',
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Box
        style={{
          maxWidth: '50rem',
          width: '100%',
          margin: '0 auto',
          paddingTop: 'var(--space-4)',
          paddingBottom: isMobile ? 'var(--space-7)' : '100px', /* was: 40px (mobile), delta: 0px */
          paddingLeft: isMobile ? 'var(--space-4)' : undefined,
          paddingRight: isMobile ? 'var(--space-4)' : undefined,
        }}
      >
        <Flex direction="column" gap="6">
          {isLoadingConversation && (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
              <LottieLoader variant="loader" size={48} showLabel />
            </Flex>
          )}

          {messagePairs.map((pair, index) => {
            const isLast = index === messagePairs.length - 1;
            return (
              <div
                key={pair.key}
                ref={(el) => setMessageRef(pair.key, el)}
              >
                <ChatResponse
                  question={pair.question}
                  answer={pair.answer}
                  citationMaps={pair.citationMaps}
                  citationCallbacks={citationCallbacks}
                  confidence={pair.confidence}
                  isStreaming={pair.isStreaming}
                  modelInfo={pair.modelInfo}
                  collections={pair.collections}
                  appliedFilters={pair.appliedFilters}
                  messageId={pair.messageId}
                  isLastMessage={isLast}
                  citationMessageRowKey={pair.key}
                  createdAt={pair.createdAt}
                  streamingContent={pair.isStreaming ? streamingContent : undefined}
                  currentStatusMessage={pair.isStreaming ? currentStatusMessage : undefined}
                  streamingCitationMaps={pair.isStreaming ? streamingCitationMaps : undefined}
                  streamingArtifacts={pair.isStreaming ? streamingArtifacts : undefined}
                />

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

      {/* Single inline-citation popover host for the whole message list. It's
          driven by the zustand store so only one popover is ever rendered,
          even when the answer contains many citation badges. */}
      <InlineCitationPopoverHost />
    </Box>
  );
}

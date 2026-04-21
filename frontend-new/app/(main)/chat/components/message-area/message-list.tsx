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
import type { ChatArtifact } from '../../types';
import type { ConfidenceLevel, ModelInfo } from '../../types';
import type { CitationMaps } from './response-tabs/citations';
import { emptyCitationMaps, useCitationActions } from './response-tabs/citations';
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

interface FeedbackInfo {
  value?: 'like' | 'dislike';
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
  feedbackInfo?: FeedbackInfo;
  /** Collections attached to this message (from user message metadata) */
  collections?: Array<{ id: string; name: string }>;
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
  // Render-time sync (not useEffect) so the value is current when
  // synchronous ResizeObserver callbacks fire in the same commit phase.
  const isStreamingRef = useRef(isStreaming);
  isStreamingRef.current = isStreaming;
  // Timestamp (ms) of the last time we cleared the scroll lock by reaching
  // the bottom. Used to implement a grace period during which the lock cannot
  // be immediately re-engaged — prevents the race where streaming content
  // grows scrollHeight an instant after the user reaches the bottom, then
  // inertia scroll events see distFromBottom > 150 and wrongly re-lock.
  const lockClearedAtMsRef = useRef<number>(0);

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

    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      if (msg.role === 'assistant') {
        const content = extractTextContent(msg.content as { type: string; text?: string }[]);

        const metadata = (msg as { metadata?: { custom?: {
          messageId?: string;
          citationMaps?: CitationMaps;
          confidence?: ConfidenceLevel;
          modelInfo?: ModelInfo;
          feedbackInfo?: FeedbackInfo;
        } } }).metadata?.custom as {
          messageId?: string;
          citationMaps?: CitationMaps;
          confidence?: ConfidenceLevel;
          modelInfo?: ModelInfo;
          feedbackInfo?: FeedbackInfo;
        } | undefined;

        // Find preceding user message
        const prevMsg = i > 0 ? messages[i - 1] : null;
        const question = prevMsg?.role === 'user'
          ? extractTextContent(prevMsg.content as { type: string; text?: string }[])
          : 'Question';

        // Check if this message is being regenerated
        const isBeingRegenerated = !!regenerateMessageId && metadata?.messageId === regenerateMessageId;

        // Check if this is the message currently being streamed
        const isCurrentlyStreaming = isStreaming && (
          question === streamingQuestion || !content
        );

        // Extract collections from the preceding user message metadata
        const userMessageCollections = prevMsg
          ? ((prevMsg as { metadata?: { custom?: { collections?: Array<{ id: string; name: string }> } } }).metadata?.custom?.collections as Array<{ id: string; name: string }> | undefined)
          : undefined;

        pairs.push({
          key: msg.id,
          messageId: metadata?.messageId,
          question,
          // Clear old answer immediately when regeneration starts so the
          // stale content doesn't linger until the first streaming chunk.
          answer: isBeingRegenerated ? '' : content,
          citationMaps: (isCurrentlyStreaming || isBeingRegenerated)
            ? EMPTY_CITATION_MAPS  // streaming citations passed as a separate prop
            : (metadata?.citationMaps || EMPTY_CITATION_MAPS),
          confidence: metadata?.confidence,
          isStreaming: isCurrentlyStreaming || isBeingRegenerated,
          modelInfo: metadata?.modelInfo,
          feedbackInfo: metadata?.feedbackInfo,
          // Use streaming collections for the temp message; user metadata for the final message
          collections: isCurrentlyStreaming
            ? (pendingCollections.length > 0 ? pendingCollections : userMessageCollections)
            : userMessageCollections,
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
      if (spacerRef.current) spacerRef.current.style.minHeight = '0px';
      debugLog.spacer('no messages or no container → height=0');
      return;
    }

    const element = messageRefs.current.get(lastMessageKeyRef.current);
    const container = scrollContainerRef.current;

    if (!element) {
      if (spacerRef.current) spacerRef.current.style.minHeight = '0px';
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

  // ── User scroll detection ─────────────────────────────────────────
  // Tracks whether the user has manually scrolled up. When true,
  // `executeScroll` will bail out (unless `chatBecameActive` overrides).
  //
  // Thresholds are intentionally generous to avoid false positives:
  //   • "at bottom" zone: ≤ 80px  — clears the lock (was 30px)
  //   • "scrolled up" zone: > 150px — sets the lock (was 50px)
  // The dead-band between 80px and 150px prevents rapid lock oscillation
  // when the container is growing (programmatic scroll may land a few
  // pixels short of the true bottom before the next ResizeObserver tick).
  const handleScroll = useCallback(() => {
    if (!scrollContainerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;

    if (distFromBottom <= 80) {
      isScrolledUpRef.current = false;
      // Record the timestamp so we can apply a grace period below.
      lockClearedAtMsRef.current = Date.now();
    } else if (distFromBottom > 150 && !isAutoScrollingRef.current) {
      // Only set scrolled-up if we're NOT in a programmatic animation AND
      // we're outside the grace period.
      //
      // Grace period (500 ms): after the user reaches the bottom the
      // streaming content can grow scrollHeight immediately, making
      // distFromBottom jump past 150 before throttledResize has had a
      // chance to call executeScroll (which would set isAutoScrollingRef).
      // Trackpad inertia then fires additional scroll events that would
      // re-engage the lock — defeating the user's intent to resume tailing.
      const msSinceClear = Date.now() - lockClearedAtMsRef.current;
      if (msSinceClear > 500) {
        isScrolledUpRef.current = true;
      }
    }
  }, []);

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

    // Guard ALL programmatic scrolls from false-triggering the user scroll-lock
    // detector. For smooth / 80-20-jump animations we need a long window (500ms)
    // because the browser reports intermediate scroll events throughout the
    // animation. For instant 'auto' scrolls we only need a short window (~100ms)
    // to cover the single async scroll event that fires after the synchronous
    // DOM update — but it is CRITICAL to guard these too. Without it, the
    // Streaming Tracker's `executeScroll({ behavior: 'auto' })` call would
    // scroll to 'top-of-last-message' (not the bottom), handleScroll would
    // fire with isAutoScrollingRef=false and distFromBottom > threshold, and
    // incorrectly set isScrolledUpRef=true — killing all further auto-scroll.
    const guardMs = (options.behavior === 'smooth' || options.behavior === '80/20-jump') ? 500 : 100;
    if (autoScrollTimeoutRef.current) clearTimeout(autoScrollTimeoutRef.current);
    isAutoScrollingRef.current = true;
    autoScrollTimeoutRef.current = setTimeout(() => {
      isAutoScrollingRef.current = false;
      autoScrollTimeoutRef.current = null;
    }, guardMs);

    // Execute the scroll
    if (options.behavior === '80/20-jump') {
      // Instant jump to 80%, then smooth glide to 100%
      container.scrollTo({ top: targetScrollTop * 0.8, behavior: 'auto' });
      requestAnimationFrame(() => {
        container.scrollTo({ top: targetScrollTop, behavior: 'smooth' });
      });
    } else {
      container.scrollTo({ top: targetScrollTop, behavior: options.behavior });
    }
  }, []);

  // ── Throttled resize handler for ResizeObserver ──
  // Handles spacer recalc + scroll tracking on every DOM size change.
  //
  // During streaming (isStreamingRef.current === true): implements the
  // Streaming Tracker from the spec (points 3 & 4):
  //   - message height ≤ viewport → snap title to top
  //   - message height > viewport → track the bottom edge
  //
  // After streaming ends: only recalculates the spacer. The one-time
  // scroll to reveal Ask More is handled by effect #5.
  const THROTTLE_MS = 100;
  const throttledResize = useMemo(() => {
    let lastCall = 0;
    let pending: ReturnType<typeof setTimeout> | null = null;
    return () => {
      const now = Date.now();
      const run = () => {
        lastCall = Date.now();
        recalcSpacerHeight();

        // Only drive scroll during active streaming.
        if (!isStreamingRef.current) {
          return;
        }
        if (isScrolledUpRef.current) {
          return;
        }

        // Streaming Tracker 3 & 4: choose target based on message size.
        // Choose scroll target based on whether the message fits in the viewport.
        const container = scrollContainerRef.current;
        const lastEl = lastMessageKeyRef.current
          ? messageRefs.current.get(lastMessageKeyRef.current)
          : null;
        if (!container || !lastEl) {
          return;
        }

        const msgHeight = lastEl.getBoundingClientRect().height;
        const chatInputReserved = isMobileRef.current ? 120 : CHAT_INPUT_RESERVED;
        const visibleHeight = container.clientHeight - chatInputReserved;
        if (msgHeight <= visibleHeight) {
          // Short message: keep title pinned to the top of the viewport
          executeScroll({ target: 'top-of-last-message', behavior: 'auto' });
        } else {
          // Tall message: track the growing bottom edge
          executeScroll({ target: 'bottom-of-container', behavior: 'auto' });
        }
      };
      if (now - lastCall >= THROTTLE_MS) {
        run();
      } else if (!pending) {
        pending = setTimeout(() => {
          pending = null;
          run();
        }, THROTTLE_MS - (now - lastCall));
      }
    };
  }, [recalcSpacerHeight, executeScroll]);

  // ── Cleanup auto-scroll timeout on unmount ──
  useEffect(() => {
    return () => {
      if (autoScrollTimeoutRef.current) clearTimeout(autoScrollTimeoutRef.current);
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
      hasPerformedInitialScrollRef.current = false;
      isScrolledUpRef.current = false;
      lockClearedAtMsRef.current = 0;
      // Align wasStreamingRef with the incoming slot's current streaming state
      // so Effect #5 (streaming-completion handler) doesn't fire spuriously.
      // Without this, wasStreamingRef retains the outgoing slot's value and
      // can trigger a completion scroll on the wrong chat — or miss the real
      // completion when we navigate back to a chat that finished while away.
      wasStreamingRef.current = isStreamingRef.current;
    }

    prevActiveSlotIdRef.current = currentSlotId;
  }, [activeSlotId]);

  // ── 0b. Reset scroll lock when a new stream starts ─────────────────
  // When the user hits Send, `isStreaming` transitions to true. Any
  // manually-set scroll lock from the previous interaction should be
  // cleared so the Streaming Tracker can take over immediately.
  useEffect(() => {
    if (isStreaming) {
      isScrolledUpRef.current = false;
    }
  }, [isStreaming]);

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
  // Per spec (“StreamingTracker 4c”): the atomic message replacement must
  // preserve scroll position — don’t snap on completion.
  //
  // However, Ask More & Action Bar appear AFTER completion and extend
  // the scrollHeight. If the user was tracking the bottom, they’ll be
  // slightly above the new bottom edge. We do ONE smooth scroll to bring
  // Ask More into view, but only if it’s actually below the fold.
  useEffect(() => {
    if (wasStreamingRef.current && !isStreaming) {
      // Reset scroll lock immediately. The streaming→final message swap can
      // cause scrollHeight changes that trick handleScroll into setting
      // isScrolledUpRef=true (distFromBottom > 150 while isAutoScrolling=false).
      // The user was tracking the stream, so we must clear this false lock.
      isScrolledUpRef.current = false;

      const scrollAfterAskMore = (delay: number) => setTimeout(() => {
        streamingScrollTopRef.current = 0;

        const container = scrollContainerRef.current;
        if (!container) {
          recalcSpacerHeight();
          return;
        }

        recalcSpacerHeight();

        // Use chatBecameActive to force-clear isScrolledUpRef inside executeScroll.
        // handleScroll keeps re-setting the lock between our initial clear and the
        // timer firing, so we must bypass it at scroll-execution time too.
        executeScroll({ target: 'bottom-of-container', behavior: 'smooth', chatBecameActive: true });
      }, delay);

      const t1 = scrollAfterAskMore(500);
      const t2 = scrollAfterAskMore(800);
      wasStreamingRef.current = isStreaming;
      return () => { clearTimeout(t1); clearTimeout(t2); };
    }
    wasStreamingRef.current = isStreaming;
  }, [isStreaming, recalcSpacerHeight, executeScroll]);

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
          paddingTop: '16px',
          paddingBottom: isMobile ? '40px' : '100px',
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
                  feedbackInfo={pair.feedbackInfo}
                  collections={pair.collections}
                  messageId={pair.messageId}
                  isLastMessage={isLast}
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
    </Box>
  );
}

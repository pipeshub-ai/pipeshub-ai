'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

/** Within this many pixels of the end counts as "at the bottom" (sub-pixel scroll positions). */
const BOTTOM_SLACK_PX = 24;

export interface FollowTail<T extends HTMLElement> {
  ref: React.RefObject<T | null>;
  following: boolean;
  /** New content arrived while the reader was scrolled up. */
  hasNew: boolean;
  onScroll: () => void;
  jumpToLatest: () => void;
}

/**
 * Keeps a scroll container at its end while new content arrives, unless the
 * reader scrolled up to read (FE-3); `jumpToLatest` resumes following.
 * `contentKey` changes whenever the content grows.
 */
export function useFollowTail<T extends HTMLElement>(
  contentKey: unknown,
  options: { stickOnResize?: boolean } = {},
): FollowTail<T> {
  const ref = useRef<T>(null);
  const followingRef = useRef(true);
  const [following, setFollowing] = useState(true);
  const [hasNew, setHasNew] = useState(false);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
    followingRef.current = atBottom;
    setFollowing(atBottom);
    if (atBottom) setHasNew(false);
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (followingRef.current) el.scrollTop = el.scrollHeight;
    else setHasNew(true);
  }, [contentKey]);

  // A virtualized list measures its rows after they render, so the end moves after `contentKey` changed: while
  // following, the container's one content element growing keeps it at the end.
  const stickOnResize = options.stickOnResize ?? false;
  useEffect(() => {
    const el = ref.current;
    const content = el?.firstElementChild;
    if (!stickOnResize || !el || !content || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => {
      if (followingRef.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [stickOnResize]);

  const jumpToLatest = useCallback(() => {
    followingRef.current = true;
    setFollowing(true);
    setHasNew(false);
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  return { ref, following, hasNew, onScroll, jumpToLatest };
}

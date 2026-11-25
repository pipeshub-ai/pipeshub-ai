'use client';

import { useCallback, useEffect, useRef, type RefObject } from 'react';

/**
 * Keeps a list's highlighted option visible as the keyboard moves it: whenever `activeKey` changes,
 * the element matching `activeSelector` inside `listRef` is scrolled into view (`block: 'nearest'`,
 * so a row already on screen does not move).
 *
 * Call `fromPointer()` just before a pointer hover changes the highlight. That change is not
 * scrolled, because scrolling a half-visible row under a still pointer would put another row under
 * it, which would highlight and scroll in turn.
 */
export function useActiveOptionInView(
  listRef: RefObject<HTMLElement | null>,
  activeSelector: string,
  activeKey: unknown,
): { fromPointer: () => void } {
  const pointerMoved = useRef(false);

  useEffect(() => {
    if (pointerMoved.current) {
      pointerMoved.current = false;
      return;
    }
    listRef.current?.querySelector(activeSelector)?.scrollIntoView?.({ block: 'nearest' });
  }, [listRef, activeSelector, activeKey]);

  const fromPointer = useCallback(() => {
    pointerMoved.current = true;
  }, []);

  return { fromPointer };
}

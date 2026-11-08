'use client';

import { useSyncExternalStore } from 'react';

const QUERY = '(hover: hover) and (pointer: fine)';

function subscribe(onChange: () => void): () => void {
  const mq = window.matchMedia?.(QUERY);
  if (!mq) return () => {};
  mq.addEventListener('change', onChange);
  return () => mq.removeEventListener('change', onChange);
}

const getSnapshot = () => window.matchMedia?.(QUERY)?.matches ?? true;
// Prerendered HTML assumes hover; hydration starts from the same value, then switches.
const getServerSnapshot = () => true;

/** True when the primary input can hover (mouse/trackpad). Defaults to true without matchMedia. */
export function useCanHover(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

'use client';

import { RouteLoadingFallback } from './components/route-loading';

/**
 * The page-change fallback. It never covers the window: the main column shows the loader (after a short delay, so a
 * fast page change shows none) and the sidebar stays on screen. See `components/route-loading.tsx`.
 */
export default function Loading() {
  return <RouteLoadingFallback />;
}

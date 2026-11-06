'use client';

import { createContext, useContext, useEffect, useLayoutEffect, type ReactNode } from 'react';
import { Flex } from '@radix-ui/themes';
import { create } from 'zustand';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useDeferredLoading } from '@/app/components/ui/use-deferred-loading';
import { SidebarBase } from '@/app/components/sidebar';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';

/**
 * What the app shows while the next page loads (`app/(main)/loading.tsx`).
 *
 * Next renders that fallback in each slot of the app layout: the main column and, on pages with their own sidebar,
 * the sidebar slot. The sidebar is never covered: a page change shows the loader in the main column only, and the
 * sidebar slot keeps the sidebar's empty frame until the next page's sidebar is ready. The pages that share the chat
 * rail keep it on screen throughout, because the layout holds the rail, not the slot.
 *
 * The loader waits before it appears, so a fast page change shows none, and fades out over the new page as soon as
 * it is ready (the timings of `useDeferredLoading`, without its minimum: a page that is there is never held back).
 */

type RouteSlot = 'main' | 'sidebar';

const RouteSlotContext = createContext<RouteSlot>('main');

/** Marks its children as the app layout's sidebar slot, so a page-change fallback there draws the sidebar's frame. */
export function SidebarSlot({ children }: { children: ReactNode }) {
  return <RouteSlotContext.Provider value="sidebar">{children}</RouteSlotContext.Provider>;
}

interface RouteLoadingState {
  /** Page-change fallbacks mounted in the main column. */
  pending: number;
  begin: () => void;
  end: () => void;
}

export const useRouteLoadingStore = create<RouteLoadingState>()((set) => ({
  pending: 0,
  begin: () => set((s) => ({ pending: s.pending + 1 })),
  end: () => set((s) => ({ pending: Math.max(0, s.pending - 1) })),
}));

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** The page-change fallback, for the slot it renders in. */
export function RouteLoadingFallback() {
  const slot = useContext(RouteSlotContext);
  return slot === 'sidebar' ? <SidebarLoading /> : <MainLoading />;
}

/**
 * In the main column: registers the load and paints nothing itself. The column's `RouteLoadingCover` shows the loader
 * if the load outlasts the show delay.
 */
function MainLoading() {
  const begin = useRouteLoadingStore((s) => s.begin);
  const end = useRouteLoadingStore((s) => s.end);
  // Before the first paint, so the show delay counts from the frame the page went away.
  useIsoLayoutEffect(() => {
    begin();
    return end;
  }, [begin, end]);
  return <span data-route-loading="main" hidden />;
}

/**
 * In the sidebar slot: the sidebar's own empty frame, at its width, so neither column moves while the next page's
 * sidebar loads. Below desktop the sidebar is a drawer, closed while the page changes, so there is nothing to keep.
 */
function SidebarLoading() {
  const isBelowDesktop = useIsBelowDesktop();
  if (isBelowDesktop) return null;
  return (
    <div data-route-loading="sidebar" aria-hidden style={{ display: 'contents' }}>
      <SidebarBase>{null}</SidebarBase>
    </div>
  );
}

/** No minimum time on screen: the loader leaves as soon as the page is ready, fading out over it. */
const ROUTE_LOADING_TIMING = { minVisibleMs: 0 };

/**
 * The main column's page-change loader. It fills the main column only (the column is `position: relative`), never
 * the window, so the sidebar beside it stays on screen and on top.
 */
export function RouteLoadingCover() {
  const pending = useRouteLoadingStore((s) => s.pending > 0);
  const { visible, exiting: fading } = useDeferredLoading(pending, ROUTE_LOADING_TIMING);
  if (!visible) return null;
  // With no minimum there is nothing to wait for: the fade starts in the render that brings the page, not a task
  // later, when the arriving page may still be keeping the main thread busy. The hook then removes the loader.
  const exiting = fading || !pending;
  return (
    <Flex
      data-route-loading="cover"
      align="center"
      justify="center"
      style={{
        position: 'absolute',
        inset: 0,
        backgroundColor: 'var(--surface-raised)',
        // Above anything the new page draws inside the column while the loader fades out over it.
        zIndex: 9999,
        // The exit cross-fade; 0ms under reduced motion (the motion roles zero out, and the hook skips the wait).
        opacity: exiting ? 0 : 1,
        transition: 'opacity var(--motion-control) var(--motion-ease)',
        pointerEvents: exiting ? 'none' : undefined,
      }}
    >
      {/* Announces "Loading…" as a status (not while it fades out) and holds a still frame under reduced motion. */}
      <LottieLoader variant="loader" size={96} showLabel announce={!exiting} />
    </Flex>
  );
}

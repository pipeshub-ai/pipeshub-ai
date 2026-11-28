/**
 * The page-change fallback (`app/(main)/loading.tsx`): the loader shows in the main column only, after the show delay,
 * and fades out as soon as the page is ready (no minimum time on screen); in the sidebar slot the fallback keeps the
 * sidebar's empty frame and never shows a loader.
 */
import React, { type ReactNode } from 'react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import Loading from '../../loading';
import { RouteLoadingCover, SidebarSlot, useRouteLoadingStore } from '../route-loading';
import { LOADING_EXIT_MS, LOADING_SHOW_DELAY_MS } from '@/app/components/ui/use-deferred-loading';

let belowDesktop = false;
vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsMobile: () => belowDesktop,
  useIsBelowDesktop: () => belowDesktop,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
// The player itself is not under test: what the cover asks of it is.
const loaderProps = vi.fn();
vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: (props: { announce?: boolean; showLabel?: boolean; size?: number; variant?: string }) => {
    loaderProps(props);
    return (
      <div data-testid="lottie" {...(props.announce ? { role: 'status', 'aria-label': 'Loading…' } : {})}>
        Loading…
      </div>
    );
  },
}));

/** The app layout's two columns, as `root-layout.tsx` builds them. */
const shell = ({ main, side }: { main?: ReactNode; side?: ReactNode }) => (
  <Theme>
    <div data-ph-sidebar-slot="">
      <SidebarSlot>{side}</SidebarSlot>
    </div>
    <div data-main-content="" style={{ position: 'relative' }}>
      <div data-app-main-scroll="">{main}</div>
      <RouteLoadingCover />
    </div>
  </Theme>
);
const cover = () => document.querySelector<HTMLElement>('[data-route-loading="cover"]');
const advance = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });
const reduceMotion = (reduce: boolean) =>
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: reduce && query.includes('reduce'),
    media: query,
    addEventListener() {},
    removeEventListener() {},
  }));

beforeEach(() => {
  vi.useFakeTimers();
  belowDesktop = false;
  loaderProps.mockClear();
  useRouteLoadingStore.setState({ pending: 0 });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('page-change loader', () => {
  it('shows nothing for a page change faster than the show delay', () => {
    const { rerender } = render(shell({ main: <Loading /> }));
    advance(LOADING_SHOW_DELAY_MS - 1);
    expect(screen.queryByRole('status')).toBeNull();
    expect(cover()).toBeNull();

    // The page arrived: Next swaps the fallback for it.
    rerender(shell({ main: <p>Agents</p> }));
    advance(2000);
    expect(screen.queryByRole('status')).toBeNull();
    expect(cover()).toBeNull();
    expect(screen.getByText('Agents')).toBeTruthy();
  });

  it('after the show delay, shows the same loader as before, over the main column only and never over the window', () => {
    render(shell({ main: <Loading /> }));
    advance(LOADING_SHOW_DELAY_MS);
    expect(screen.getByRole('status', { name: 'Loading…' })).toBeTruthy();
    expect(loaderProps).toHaveBeenLastCalledWith(expect.objectContaining({ variant: 'loader', size: 96, showLabel: true, announce: true }));

    const el = cover() as HTMLElement;
    // Inside the main column, which bounds it (position: relative there), and not fixed to the viewport.
    expect(el.closest('[data-main-content]')).not.toBeNull();
    expect(el.closest('[data-ph-sidebar-slot]')).toBeNull();
    expect(el.style.position).toBe('absolute');
    expect(el.getAttribute('style')).not.toContain('fixed');
    expect(el.style.backgroundColor).toBe('var(--surface-raised)');
  });

  it('once shown, fades the loader out as soon as the page is ready, with no minimum time on screen', () => {
    const { rerender } = render(shell({ main: <Loading /> }));
    advance(LOADING_SHOW_DELAY_MS);
    advance(50);
    expect(cover()?.style.opacity).toBe('1');
    expect(screen.getByRole('status')).toBeTruthy();

    // The page arrived 50 ms after the loader appeared: no hold, the fade starts in the same render.
    rerender(shell({ main: <p>Agents</p> }));
    // Fading over the page: no longer announced, and clicks reach the page.
    expect(cover()?.style.opacity).toBe('0');
    expect(cover()?.style.pointerEvents).toBe('none');
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByText('Agents')).toBeTruthy();

    // Gone one fade after the page was ready.
    advance(LOADING_EXIT_MS - 1);
    expect(cover()).not.toBeNull();
    advance(1);
    expect(cover()).toBeNull();
  });

  it('a long load keeps the loader up until the page is ready, then the same one fade', () => {
    const { rerender } = render(shell({ main: <Loading /> }));
    advance(2000);
    expect(cover()?.style.opacity).toBe('1');
    rerender(shell({ main: <p>Agents</p> }));
    expect(cover()?.style.opacity).toBe('0');
    advance(LOADING_EXIT_MS);
    expect(cover()).toBeNull();
  });

  it('under reduced motion, removes the loader at once when the page is ready', () => {
    reduceMotion(true);
    const { rerender } = render(shell({ main: <Loading /> }));
    advance(LOADING_SHOW_DELAY_MS);
    expect(cover()?.style.opacity).toBe('1');
    rerender(shell({ main: <p>Agents</p> }));
    // Transparent in the same render (the motion role is 0ms), and removed on the next tasks.
    expect(cover()?.style.opacity).toBe('0');
    advance(1);
    expect(cover()).toBeNull();
  });

  it('a page change that starts while the loader fades keeps it up', () => {
    const { rerender } = render(shell({ main: <Loading /> }));
    advance(LOADING_SHOW_DELAY_MS);
    rerender(shell({ main: <p>Agents</p> }));
    advance(10);
    expect(cover()?.style.opacity).toBe('0');
    rerender(shell({ main: <Loading /> }));
    advance(10);
    expect(cover()?.style.opacity).toBe('1');
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('in the sidebar slot, keeps the sidebar frame and never shows a loader', () => {
    render(shell({ side: <Loading /> }));
    advance(2000);
    expect(screen.queryByRole('status')).toBeNull();
    expect(cover()).toBeNull();
    expect(useRouteLoadingStore.getState().pending).toBe(0);

    const frame = document.querySelector<HTMLElement>('[data-ph-sidebar-slot] [data-route-loading="sidebar"]');
    expect(frame).not.toBeNull();
    expect(frame?.getAttribute('aria-hidden')).toBe('true');
    // The sidebar's own column (SidebarBase), at the sidebar's width, with nothing in it.
    const column = frame?.querySelector<HTMLElement>('[style*="border-right"]');
    expect(column).not.toBeNull();
    expect(column?.style.width).toMatch(/^\d+px$/);
    const words = Array.from(frame?.querySelectorAll('*') ?? []).filter(
      (e) => e.tagName !== 'STYLE' && e.children.length === 0 && (e.textContent ?? '').trim() !== '',
    );
    expect(words).toHaveLength(0);
  });

  it('below desktop, the sidebar slot draws nothing while the page loads (the drawer is closed)', () => {
    belowDesktop = true;
    render(shell({ side: <Loading /> }));
    advance(2000);
    expect(document.querySelector('[data-ph-sidebar-slot]')?.innerHTML).toBe('');
    expect(cover()).toBeNull();
  });

  it('both slots loading at once show one loader, in the main column', () => {
    render(shell({ main: <Loading />, side: <Loading /> }));
    advance(LOADING_SHOW_DELAY_MS);
    expect(screen.getAllByRole('status')).toHaveLength(1);
    expect(useRouteLoadingStore.getState().pending).toBe(1);
  });
});

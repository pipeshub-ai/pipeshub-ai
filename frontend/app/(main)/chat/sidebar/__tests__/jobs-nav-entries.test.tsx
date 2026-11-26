import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

/**
 * CJ-4 (D6, §4.3, §9): the sidebar's Needs you and Jobs entries with the server's count as an amber badge, named in
 * words, hidden at 0 while the entry stays; and the phone menu button's dot.
 */

installMemoryStorage();

const nav = vi.hoisted(() => ({ pathname: '/chat/', params: new URLSearchParams() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => nav.pathname,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));

const { JobsNavEntries } = await import('../jobs-nav-entries');
const { MobileMenuButton } = await import('@/app/(main)/components/mobile-menu-button');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useMobileSidebarStore } = await import('@/lib/store/mobile-sidebar-store');
const { EMPTY_COUNTS, takeCounts, useInboxStore } = await import('@/lib/harness-jobs/inbox-store');

const waiting = (n: number) => act(() => takeCounts({ ...EMPTY_COUNTS, waiting: n, badge: n }));

beforeEach(() => {
  installBrowserShims();
  mobile.value = false;
  nav.pathname = '/chat/';
  nav.params = new URLSearchParams();
  useFeatureFlagsStore.setState({ agentHarness: { useHarness: true, harnessOn: true } } as never);
  useInboxStore.setState({ counts: EMPTY_COUNTS, countsKnown: false });
});

afterEach(() => cleanup());

describe('the Needs you and Jobs entries', () => {
  it('link to the two tabs, with no badge at 0', () => {
    renderInTheme(<JobsNavEntries />);
    expect(screen.getByRole('link', { name: /^Needs you/ }).getAttribute('href')).toBe('/jobs?view=needs-you');
    expect(screen.getByRole('link', { name: 'Jobs' }).getAttribute('href')).toBe('/jobs?view=all');
    expect(screen.queryByTestId('needs-you-badge')).toBeNull();
  });

  it('shows the server’s count as a badge named in words, live, and drops it at 0 while the entry stays', () => {
    renderInTheme(<JobsNavEntries />);
    waiting(3);
    const badge = screen.getByTestId('needs-you-badge');
    expect(badge.getAttribute('aria-label')).toBe('3 things need you');
    expect(badge.textContent).toBe('3');
    waiting(1);
    expect(screen.getByTestId('needs-you-badge').getAttribute('aria-label')).toBe('1 thing needs you');
    waiting(0);
    expect(screen.queryByTestId('needs-you-badge')).toBeNull();
    expect(screen.getByRole('link', { name: /^Needs you/ })).toBeTruthy();
  });

  it('CJ14-FU-1: Jobs shows the server’s working count in words, quiet, hidden at 0 and when the server sends none', () => {
    renderInTheme(<JobsNavEntries />);
    expect(screen.queryByTestId('working-jobs-count')).toBeNull();
    act(() => takeCounts({ ...EMPTY_COUNTS, working: 2 }));
    const count = screen.getByTestId('working-jobs-count');
    expect(count.textContent).toBe('2');
    expect(count.getAttribute('aria-label')).toBe('2 jobs working');
    expect(count.style.color).toBe('var(--neutral-11)');
    expect(screen.getByRole('link', { name: /^Jobs/ }).getAttribute('href')).toBe('/jobs?view=all');
    act(() => takeCounts({ ...EMPTY_COUNTS, working: 1 }));
    expect(screen.getByTestId('working-jobs-count').getAttribute('aria-label')).toBe('1 job working');
    act(() => takeCounts({ ...EMPTY_COUNTS, working: 0 }));
    expect(screen.queryByTestId('working-jobs-count')).toBeNull();
    act(() => takeCounts({ ...EMPTY_COUNTS, working: 3 }));
    act(() => takeCounts(EMPTY_COUNTS));
    expect(screen.queryByTestId('working-jobs-count'), 'a server without `working` shows none').toBeNull();
  });

  it('marks the tab showing as the current entry', () => {
    nav.pathname = '/jobs/';
    nav.params = new URLSearchParams('view=all');
    renderInTheme(<JobsNavEntries />);
    const jobs = screen.getByRole('link', { name: 'Jobs' });
    const needsYou = screen.getByRole('link', { name: /^Needs you/ });
    expect(jobs.style.backgroundColor).toBe('var(--olive-3)');
    expect(needsYou.style.backgroundColor).toBe('transparent');
  });

  it('is not there while the harness is off (D1: no dead links)', () => {
    useFeatureFlagsStore.setState({ agentHarness: { useHarness: false, harnessOn: false } } as never);
    renderInTheme(<JobsNavEntries />);
    expect(screen.queryByRole('link', { name: /^Needs you/ })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Jobs' })).toBeNull();
  });

  it('stays while new runs are stopped and the harness is on for the org (ADR-OPS-08)', () => {
    useFeatureFlagsStore.setState({ agentHarness: { useHarness: false, reason: 'kill_switch', harnessOn: true } } as never);
    renderInTheme(<JobsNavEntries />);
    expect(screen.getByRole('link', { name: /^Needs you/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Jobs' })).toBeTruthy();
  });
});

describe('the phone menu button', () => {
  beforeEach(() => {
    mobile.value = true;
    useMobileSidebarStore.setState({ registeredCount: 1 });
  });

  it('gets an amber dot and says how many things need you while something waits', () => {
    renderInTheme(<MobileMenuButton />);
    expect(screen.queryByTestId('menu-needs-you-dot')).toBeNull();
    expect(screen.getByRole('button', { name: 'Open sidebar' })).toBeTruthy();
    waiting(2);
    expect(screen.getByTestId('menu-needs-you-dot')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open sidebar, 2 things need you' })).toBeTruthy();
    waiting(0);
    expect(screen.queryByTestId('menu-needs-you-dot')).toBeNull();
  });
});

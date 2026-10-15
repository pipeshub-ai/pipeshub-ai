import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/chat',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../api', async (orig) => ({
  ...(await orig<typeof import('../api')>()),
  NotificationsApi: {
    list: vi.fn().mockResolvedValue({ notifications: [], pagination: {}, stats: {} }),
    stats: vi.fn().mockResolvedValue({}),
  },
}));

import { NotificationsPanel } from '../panel';
import { useNotificationStore } from '../store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} });
  useNotificationStore.setState({ isPanelOpen: true } as never);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useNotificationStore.setState({ isPanelOpen: false } as never);
});

const panel = () => document.querySelector('[data-ph-notifications-panel]') as HTMLElement;

describe('NotificationsPanel width', () => {
  it('never exceeds the viewport on a phone', () => {
    mobile.value = true;
    render(<NotificationsPanel />);
    expect(panel().style.maxWidth).toBe('calc(100vw - 0px)');
  });

  it('leaves room for the sidebar on desktop', () => {
    mobile.value = false;
    useSidebarWidthStore.setState({ sidebarWidth: 232, isNavCollapsed: false } as never);
    render(<NotificationsPanel />);
    expect(panel().style.maxWidth).toBe('calc(100vw - 232px)');
  });
});

describe('NotificationsPanel header', () => {
  it('draws the active filter label at full strength (opacity 0.5 on gray-11 was 2.12:1, axe color-contrast)', () => {
    mobile.value = false;
    render(<NotificationsPanel />);
    const label = Array.from(document.querySelectorAll<HTMLElement>('[data-ph-notifications-panel] span')).find((el) => el.textContent === 'All');
    expect(label).toBeTruthy();
    expect(label!.style.opacity).toBe('');
    expect(label!.style.color).toBe('var(--slate-11)');
  });
});

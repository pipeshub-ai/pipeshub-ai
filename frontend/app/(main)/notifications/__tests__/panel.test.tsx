import React from 'react';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { en } from '@/lib/__tests__/test-i18n';

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/chat',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value, useIsBelowDesktop: () => mobile.value }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../api', async (orig) => ({
  ...(await orig<typeof import('../api')>()),
  NotificationsApi: {
    list: vi.fn().mockResolvedValue({ notifications: [], pagination: {}, stats: {} }),
    getStats: vi.fn().mockResolvedValue({ unread: 0 }),
  },
}));

import { NotificationsPanel } from '../panel';
import { useNotificationStore } from '../store';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  act(() => useNotificationStore.setState({ isPanelOpen: false } as never));
  document.querySelectorAll('[data-test-trigger]').forEach((el) => el.remove());
});

/** An Inbox button outside the panel, as the sidebar header renders it. */
function addTrigger(): HTMLButtonElement {
  const trigger = document.createElement('button');
  trigger.setAttribute('data-ph-notifications-trigger', '');
  trigger.setAttribute('data-test-trigger', '');
  // jsdom has no layout: report a box so the trigger counts as on screen.
  trigger.getClientRects = () => [{}] as unknown as DOMRectList;
  document.body.appendChild(trigger);
  return trigger;
}

describe('NotificationsPanel header', () => {
  it('is one line: the title only, the active filter is not repeated as a subtitle', async () => {
    mobile.value = false;
    act(() => useNotificationStore.setState({ isPanelOpen: true } as never));
    render(<NotificationsPanel />);
    const panel = await screen.findByRole('complementary', { name: en.nav.inbox });
    expect(panel.textContent).toContain(en.nav.inbox);
    const subtitle = Array.from(panel.querySelectorAll('span')).find(
      (el) => el.textContent === en.notifications.filterAll,
    );
    expect(subtitle).toBeUndefined();
  });
});

describe('NotificationsPanel focus', () => {
  it('moves focus into the panel on open and back to the Inbox button on close', async () => {
    mobile.value = false;
    const trigger = addTrigger();
    trigger.focus();
    act(() => useNotificationStore.setState({ isPanelOpen: true } as never));
    render(<NotificationsPanel />);
    const panel = await screen.findByRole('complementary', { name: en.nav.inbox });
    expect(panel.contains(document.activeElement)).toBe(true);

    act(() => useNotificationStore.getState().closePanel());
    expect(document.activeElement).toBe(trigger);
  });

  it('on a phone makes the page behind inert while open, and restores it on close', async () => {
    mobile.value = true;
    const page = document.createElement('div');
    page.setAttribute('data-test-trigger', '');
    document.body.appendChild(page);
    act(() => useNotificationStore.setState({ isPanelOpen: true } as never));
    render(<NotificationsPanel />);
    await screen.findByRole('complementary', { name: en.nav.inbox });
    expect(page.hasAttribute('inert')).toBe(true);

    act(() => useNotificationStore.getState().closePanel());
    expect(page.hasAttribute('inert')).toBe(false);
  });
});

describe('NotificationsPanel Escape with the filter menu open', () => {
  it('first Escape closes only the menu (focus on Filter); the second closes the panel (focus on Inbox)', async () => {
    mobile.value = false;
    const trigger = addTrigger();
    act(() => useNotificationStore.setState({ isPanelOpen: true } as never));
    render(
      <Theme>
        <NotificationsPanel />
      </Theme>,
    );
    await screen.findByRole('complementary', { name: en.nav.inbox });
    const filter = screen.getByRole('button', { name: en.notifications.filter });
    filter.focus();
    fireEvent.keyDown(filter, { key: 'Enter' });
    const menu = await screen.findByRole('menu');

    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    expect(useNotificationStore.getState().isPanelOpen).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(filter));

    fireEvent.keyDown(filter, { key: 'Escape' });
    expect(useNotificationStore.getState().isPanelOpen).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});

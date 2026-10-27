import React from 'react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({ usePathname: () => '/chat', useSearchParams: () => new URLSearchParams() }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  NotificationsApi: {
    list: vi.fn().mockResolvedValue({ notifications: [], hasMore: false }),
    getStats: vi.fn().mockResolvedValue({ unread: 0 }),
  },
}));

import { NotificationsPanel } from '../panel';
import { useNotificationStore } from '../store';

beforeEach(() => {
  class RO {
    observe() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', RO);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  act(() => useNotificationStore.getState().closePanel());
});

describe('NotificationsPanel on mobile', () => {
  it('fills the width and has a close button that closes it', async () => {
    act(() => useNotificationStore.getState().openPanel());
    render(
      <Theme>
        <NotificationsPanel />
      </Theme>,
    );

    const panel = await screen.findByRole('complementary');
    expect(panel.style.width).toBe('100%');
    fireEvent.click(screen.getByRole('button', { name: en.notifications.close }));
    expect(useNotificationStore.getState().isPanelOpen).toBe(false);
  });
});

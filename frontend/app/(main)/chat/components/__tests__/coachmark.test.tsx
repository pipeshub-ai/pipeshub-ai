/** MN-19 (FE): a coachmark shows until the server-stored `tipsSeen` has it, reads that list once per session, and marks optimistically. */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({ getPreferences: vi.fn(), markTipSeen: vi.fn() }));
vi.mock('@/app/(main)/notifications/api', () => ({ NotificationsApi: api, TIP_IDS: [] }));

import { Coachmark } from '../coachmark';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { resetTipsStoreForTests } from '@/lib/store/tips-store';
import { TOAST_SAFE_BOTTOM_VAR } from '@/lib/toast-safe-area';

const renderMark = (tipId: 'mentions.firstNote' | 'mentions.firstAgentMention' = 'mentions.firstNote', active = true) =>
  render(
    <Theme>
      <Coachmark tipId={tipId} active={active} message="A tip">
        <textarea aria-label="composer" />
      </Coachmark>
    </Theme>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  resetTipsStoreForTests();
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: true } });
  api.getPreferences.mockResolvedValue({ tipsSeen: [] });
  api.markTipSeen.mockResolvedValue({ tipsSeen: [] });
});
afterEach(cleanup);

describe('Coachmark', () => {
  it('draws "Got it" in the high-contrast variant (the default jade soft button is 4.26:1 on the tip, axe color-contrast)', async () => {
    renderMark();
    await screen.findByTestId('coachmark-mentions.firstNote');
    expect(screen.getByRole('button', { name: 'Got it' }).className).toContain('rt-high-contrast');
  });

  it('keeps toasts clear of itself while open (it is portaled outside the composer\'s measured box)', async () => {
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    const orig = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function (this: Element) {
      return { top: this.getAttribute('data-testid') === 'coachmark-mentions.firstNote' ? 600 : 0 } as DOMRect;
    };
    try {
      renderMark();
      await screen.findByTestId('coachmark-mentions.firstNote');
      await waitFor(() =>
        expect(document.documentElement.style.getPropertyValue(TOAST_SAFE_BOTTOM_VAR)).toBe('308px'),
      );
      fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
      await waitFor(() => expect(document.documentElement.style.getPropertyValue(TOAST_SAFE_BOTTOM_VAR)).toBe(''));
    } finally {
      Element.prototype.getBoundingClientRect = orig;
    }
  });

  it('is not rendered when tipsSeen contains the tip', async () => {
    api.getPreferences.mockResolvedValue({ tipsSeen: ['mentions.firstNote'] });
    renderMark();
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull();
  });

  it('shows once, marks seen, and is gone after a remount without a second read', async () => {
    const first = renderMark();
    expect((await screen.findByTestId('coachmark-mentions.firstNote')).textContent).toContain('A tip');
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(api.markTipSeen).toHaveBeenCalledExactlyOnceWith('mentions.firstNote');
    await waitFor(() => expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull());
    first.unmount();

    renderMark();
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull();
    expect(api.getPreferences).toHaveBeenCalledTimes(1);
    expect(api.markTipSeen).toHaveBeenCalledTimes(1);
  });

  it('keeps it hidden when the write fails', async () => {
    api.markTipSeen.mockRejectedValue(new Error('offline'));
    renderMark();
    await screen.findByTestId('coachmark-mentions.firstNote');
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    await waitFor(() => expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull());
  });

  it('waits for its moment: not shown while inactive', async () => {
    renderMark('mentions.firstNote', false);
    await waitFor(() => expect(api.getPreferences).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull();
  });

  it('flag off: renders only the child and never reads or writes', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: false } });
    renderMark();
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.getByLabelText('composer')).toBeTruthy();
    expect(screen.queryByTestId('coachmark-mentions.firstNote')).toBeNull();
    expect(api.getPreferences).not.toHaveBeenCalled();
    expect(api.markTipSeen).not.toHaveBeenCalled();
  });
});

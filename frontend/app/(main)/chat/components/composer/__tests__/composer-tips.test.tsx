import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({ getPreferences: vi.fn(), markTipSeen: vi.fn() }));
vi.mock('@/app/(main)/notifications/api', () => ({ NotificationsApi: api, TIP_IDS: [] }));

import { ComposerTips } from '../composer-tips';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { resetTipsStoreForTests, useTipsStore } from '@/lib/store/tips-store';

const mount = () => render(<Theme><ComposerTips /></Theme>);
const tip = () => screen.getByTestId('composer-tip').getAttribute('data-tip');

beforeEach(async () => {
  vi.clearAllMocks();
  resetTipsStoreForTests();
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: true } });
  api.getPreferences.mockResolvedValue({ tipsSeen: [] });
  api.markTipSeen.mockResolvedValue({ tipsSeen: [] });
  await useTipsStore.getState().ensureLoaded();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('ComposerTips', () => {
  it('explains the keys the first time, marks popoverIntro seen, then rotates through the usage tips on later opens', () => {
    const first = mount();
    expect(tip()).toBe('intro');
    expect(screen.getByTestId('composer-tip').textContent).toContain('Enter to pick');
    expect(api.markTipSeen).toHaveBeenCalledExactlyOnceWith('mentions.popoverIntro');
    first.unmount();

    const seen: (string | null)[] = [];
    for (let i = 0; i < 3; i += 1) {
      const m = mount();
      seen.push(tip());
      m.unmount();
    }
    expect(new Set(seen)).toEqual(new Set(['noteOnly', 'assistantAnywhere', 'agentAccess']));
    expect(api.markTipSeen).toHaveBeenCalledTimes(1);
  });

  it('skips the intro when the server says it was seen', async () => {
    resetTipsStoreForTests();
    api.getPreferences.mockResolvedValue({ tipsSeen: ['mentions.popoverIntro'] });
    await useTipsStore.getState().ensureLoaded();
    mount();
    expect(tip()).not.toBe('intro');
    expect(api.markTipSeen).not.toHaveBeenCalled();
  });

  it('advances to the next tip while the popover stays open', async () => {
    vi.useFakeTimers();
    resetTipsStoreForTests();
    api.getPreferences.mockResolvedValue({ tipsSeen: ['mentions.popoverIntro'] });
    await act(async () => {
      await useTipsStore.getState().ensureLoaded();
    });
    mount();
    const before = tip();
    act(() => {
      vi.advanceTimersByTime(8000);
    });
    expect(tip()).not.toBe(before);
  });

  it('the usage tips come from the translated strings', async () => {
    resetTipsStoreForTests();
    api.getPreferences.mockResolvedValue({ tipsSeen: ['mentions.popoverIntro'] });
    await useTipsStore.getState().ensureLoaded();
    mount();
    await waitFor(() =>
      expect([
        'Mention only a teammate to leave a note without asking the AI',
        '@assistant works anywhere',
        'Agents answer with your access',
      ]).toContain(screen.getByTestId('composer-tip').textContent),
    );
  });
});

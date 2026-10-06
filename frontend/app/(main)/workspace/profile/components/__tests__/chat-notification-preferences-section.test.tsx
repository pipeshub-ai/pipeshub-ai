import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';

const api = vi.hoisted(() => ({ getPreferences: vi.fn(), updatePreferences: vi.fn() }));
vi.mock('@/app/(main)/notifications/api', () => ({ NotificationsApi: api }));

import { ChatNotificationPreferencesSection } from '../chat-notification-preferences-section';

type FlagsState = Partial<ReturnType<typeof useFeatureFlagsStore.getState>>;
const setFlag = (on: boolean, mentions = false) =>
  useFeatureFlagsStore.setState({
    flags: { ENABLE_COLLABORATIVE_CHATS: on, ENABLE_CHAT_MENTIONS: mentions },
  } as FlagsState);

const prefs = (over: Record<string, unknown> = {}) => ({
  email: { chatShared: true, ownershipTransferred: true },
  inApp: { chatActivity: true },
  mutedSessions: [],
  ...over,
});

const stubMatchMedia = (matches: boolean) => {
  window.matchMedia = ((query: string) => ({
    matches,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
};

const renderSection = () => render(<Theme><ChatNotificationPreferencesSection /></Theme>);

beforeEach(() => {
  vi.clearAllMocks();
  stubMatchMedia(false);
  setFlag(true);
  api.getPreferences.mockResolvedValue(prefs());
});
afterEach(cleanup);

describe('ChatNotificationPreferencesSection', () => {
  it('shows the three switches with the loaded values', async () => {
    api.getPreferences.mockResolvedValue(prefs({ inApp: { chatActivity: false } }));
    renderSection();
    const email = await screen.findByRole('switch', { name: 'Email me when a chat is shared with me' });
    await waitFor(() => expect(email.getAttribute('aria-checked')).toBe('true'));
    expect(screen.getByRole('switch', { name: 'Email me when I become the owner of a chat' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Notify me about activity in shared chats' }).getAttribute('aria-checked')).toBe('false');
  });

  it.each([
    ['Email me when a chat is shared with me', { email: { chatShared: false } }],
    ['Email me when I become the owner of a chat', { email: { ownershipTransferred: false } }],
    ['Notify me about activity in shared chats', { inApp: { chatActivity: false } }],
  ])('PATCHes only %s', async (label, patch) => {
    api.updatePreferences.mockResolvedValue(prefs());
    renderSection();
    const sw = await screen.findByRole('switch', { name: label });
    await waitFor(() => expect(sw.hasAttribute('disabled')).toBe(false));
    fireEvent.click(sw);
    await waitFor(() => expect(api.updatePreferences).toHaveBeenCalledWith(patch));
  });

  it('shows an error and keeps the old value when the update fails', async () => {
    api.updatePreferences.mockRejectedValue(new Error('boom'));
    renderSection();
    const sw = await screen.findByRole('switch', { name: 'Notify me about activity in shared chats' });
    await waitFor(() => expect(sw.hasAttribute('disabled')).toBe(false));
    fireEvent.click(sw);
    expect((await screen.findByRole('alert')).textContent).toBe('Could not save your notification settings.');
    expect(sw.getAttribute('aria-checked')).toBe('true');
  });

  it('renders nothing and calls nothing with the flag off', () => {
    setFlag(false);
    const { container } = renderSection();
    expect(container.querySelector('.radix-themes')?.childElementCount).toBe(0);
    expect(api.getPreferences).not.toHaveBeenCalled();
  });
});

describe('ChatNotificationPreferencesSection mention switches', () => {
  const MENTION_LABELS = [
    'Notify me when someone mentions me in a chat',
    'Email me when someone mentions me in a chat',
  ];

  it('flag off: only the three original switches', async () => {
    renderSection();
    await screen.findByRole('switch', { name: 'Email me when a chat is shared with me' });
    expect(screen.getAllByRole('switch')).toHaveLength(3);
    for (const label of MENTION_LABELS) expect(screen.queryByRole('switch', { name: label })).toBeNull();
  });

  it('flag on: adds the in-app switch (default on) and the email switch (default off)', async () => {
    setFlag(true, true);
    api.getPreferences.mockResolvedValue(
      prefs({ email: { chatShared: true, ownershipTransferred: true }, inApp: { chatActivity: true } }),
    );
    renderSection();
    const inApp = await screen.findByRole('switch', { name: MENTION_LABELS[0]! });
    const email = screen.getByRole('switch', { name: MENTION_LABELS[1]! });
    await waitFor(() => expect(inApp.getAttribute('aria-checked')).toBe('true'));
    expect(email.getAttribute('aria-checked')).toBe('false');
    expect(screen.getAllByRole('switch')).toHaveLength(5);
  });

  it.each([
    [MENTION_LABELS[0]!, { inApp: { chatMentioned: false } }],
    [MENTION_LABELS[1]!, { email: { chatMentioned: true } }],
  ])('PATCHes only %s', async (label, patch) => {
    setFlag(true, true);
    api.updatePreferences.mockResolvedValue(prefs());
    renderSection();
    const sw = await screen.findByRole('switch', { name: label });
    await waitFor(() => expect(sw.hasAttribute('disabled')).toBe(false));
    fireEvent.click(sw);
    await waitFor(() => expect(api.updatePreferences).toHaveBeenCalledWith(patch));
  });
});

describe('ChatNotificationPreferencesSection narrow layout', () => {
  const rowOf = (name: string) => screen.getByRole('switch', { name }).parentElement as HTMLElement;
  const LABEL = 'Email me when a chat is shared with me';

  it('stacks label above a left-aligned switch, like SettingsRow, and lets the label wrap at ≤768px', async () => {
    stubMatchMedia(true);
    renderSection();
    await screen.findByRole('switch', { name: LABEL });
    const row = rowOf(LABEL);
    await waitFor(() => expect(row.className).toContain('rt-r-fd-column'));
    expect(screen.getByRole('switch', { name: LABEL }).style.alignSelf).toBe('flex-start');
    const label = screen.getAllByText(LABEL).find((el) => el !== screen.getByRole('switch', { name: LABEL })) as HTMLElement;
    expect(label.style.overflowWrap).toBe('anywhere');
    expect(row.style.minWidth).toBe('0');
  });

  it('keeps the single-line row on desktop', async () => {
    stubMatchMedia(false);
    renderSection();
    await screen.findByRole('switch', { name: LABEL });
    expect(rowOf(LABEL).className).not.toContain('rt-r-fd-column');
    expect(screen.getByRole('switch', { name: LABEL }).style.alignSelf).toBe('');
  });
});

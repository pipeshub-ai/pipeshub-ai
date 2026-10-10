import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useNotificationStore } from '@/app/(main)/notifications/store';
import type { Conversation } from '@/chat/types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <i data-icon={name} />,
}));

import { ChatRowBadges } from '../chat-row-badges';

type FlagsState = Partial<ReturnType<typeof useFeatureFlagsStore.getState>>;
const setFlag = (on: boolean) =>
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as FlagsState);
const conv = (over: Record<string, unknown> = {}) =>
  ({ id: 'c1', title: 'x', ...over }) as unknown as Conversation;
const access = (role: 'read' | 'write') =>
  ({ role, isOwner: false, accessLevel: role, canSend: role === 'write', canManage: false, canInvite: false, isCollaborative: true });
const show = (c: Conversation) => render(<Theme><ChatRowBadges conversation={c} /></Theme>);

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} });
  setFlag(true);
  useNotificationStore.setState({ mutedSessionIds: [] });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('ChatRowBadges icons', () => {
  it('shows an edit icon with an accessible name for "can continue"', () => {
    const { container } = show(conv({ isOwner: false, access: access('write') }));
    expect(screen.getByRole('img', { name: 'Can continue' })).toBeTruthy();
    expect(container.querySelector('[data-icon="edit"]')).toBeTruthy();
    expect(container.textContent).toBe('');
  });

  it('shows a visibility icon for "can view"', () => {
    const { container } = show(conv({ isOwner: false, access: access('read') }));
    expect(screen.getByRole('img', { name: 'Can view' })).toBeTruthy();
    expect(container.querySelector('[data-icon="visibility"]')).toBeTruthy();
  });

  it('shows group + count for shared-by-me', () => {
    const { container } = show(conv({ isOwner: true, collaboratorCount: 3 }));
    expect(screen.getByRole('img', { name: 'Shared with 3' })).toBeTruthy();
    expect(container.querySelector('[data-icon="group"]')).toBeTruthy();
    expect(container.textContent).toBe('3');
  });

  it('shows the muted marker only for muted sessions', () => {
    useNotificationStore.setState({ mutedSessionIds: ['c1'] });
    const { container } = show(conv({ isOwner: true }));
    expect(screen.getByRole('img', { name: 'Notifications muted' })).toBeTruthy();
    expect(container.querySelector('[data-icon="notifications_off"]')).toBeTruthy();
    cleanup();
    useNotificationStore.setState({ mutedSessionIds: ['other'] });
    expect(show(conv({ isOwner: true })).container.querySelector('[role="img"]')).toBeNull();
  });

  it('renders nothing with the flag off, even when muted', () => {
    setFlag(false);
    useNotificationStore.setState({ mutedSessionIds: ['c1'] });
    expect(show(conv({ isOwner: false, access: access('write'), unreadCount: 2 })).container.querySelector('[role="img"]')).toBeNull();
  });
});

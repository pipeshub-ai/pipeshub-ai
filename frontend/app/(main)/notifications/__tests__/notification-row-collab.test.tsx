import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import type { NotificationListItem } from '../api';

vi.mock('@/lib/navigation', () => ({
  Link: ({ href, children, onClick, ...rest }: React.ComponentProps<'a'>) => (
    <a
      href={href as string}
      {...rest}
      onClick={(e) => {
        onClick?.(e);
        e.preventDefault();
      }}
    >
      {children}
    </a>
  ),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { NotificationRow } from '../notification-row';

type FlagsState = Partial<ReturnType<typeof useFeatureFlagsStore.getState>>;
const setFlag = (on: boolean) =>
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as FlagsState);

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} });
  setFlag(true);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function notification(over: Partial<NotificationListItem> = {}): NotificationListItem {
  return {
    _id: 'n1',
    type: 'chat.shared',
    title: 'Conversation shared with you',
    message: 'A conversation was shared with you.',
    redirectLink: '/chat?conversationId=c1',
    severity: 'info',
    status: 'unread',
    payload: { sessionId: 's1', kind: 'chat', actorUserId: 'u1', accessLevel: 'write' },
    ...over,
  };
}

function renderRow(n: NotificationListItem, extra: Partial<React.ComponentProps<typeof NotificationRow>> = {}) {
  const noop = vi.fn();
  return render(
    <Theme>
      <NotificationRow
        notification={n}
        onMarkRead={noop}
        onMarkUnread={noop}
        onArchive={noop}
        onUnarchive={noop}
        onDismiss={noop}
        markReadLabel="Mark read"
        markUnreadLabel="Mark unread"
        archiveLabel="Archive"
        unarchiveLabel="Unarchive"
        dismissLabel="Dismiss"
        {...extra}
      />
    </Theme>,
  );
}

describe('NotificationRow collaboration types', () => {
  it('renders chat.shared (chat) with localized text and the redirectLink', () => {
    renderRow(notification());
    const link = screen.getByRole('link', { name: 'Chat shared with you' });
    expect(link.getAttribute('href')).toBe('/chat?conversationId=c1');
    expect(screen.getAllByText('A chat was shared with you. You can continue it.').length).toBeGreaterThan(0);
  });

  it('keeps agentId in the deep link of an agent chat', () => {
    renderRow(
      notification({
        redirectLink: '/chat?conversationId=c1&agentId=a9',
        payload: { sessionId: 's1', kind: 'agent', accessLevel: 'read' },
      }),
    );
    const link = screen.getByRole('link', { name: 'Chat shared with you' });
    expect(link.getAttribute('href')).toContain('agentId=a9');
    expect(screen.getAllByText('A chat was shared with you. You can view it.').length).toBeGreaterThan(0);
  });

  it('shows the handover note truncated to 140 characters, as plain text', () => {
    const note = `<b>hi</b>${'x'.repeat(300)}`;
    const { container } = renderRow(notification({ payload: { sessionId: 's1', accessLevel: 'write', note } }));
    const shown = screen.getAllByText(/^A chat was shared with you\. You can continue it\. Note:/)[0].textContent ?? '';
    const noteText = shown.slice(shown.indexOf('Note: ') + 6);
    expect(Array.from(noteText)).toHaveLength(140);
    expect(noteText.endsWith('…')).toBe(true);
    expect(container.querySelector('b')).toBeNull();
    expect(noteText.startsWith('<b>hi</b>')).toBe(true);
  });

  it.each([
    ['chat.accessChanged', { accessLevel: 'write' }, 'Chat access changed', 'You can now continue a shared chat.'],
    ['chat.accessChanged', { accessLevel: 'read' }, 'Chat access changed', 'You can now view a shared chat.'],
    ['chat.ownershipTransferred', { accessLevel: 'write' }, 'Chat ownership transferred', 'You are now the owner of a chat.'],
    ['chat.activity', { count: 4 }, 'New activity in a shared chat', 'New activity in a shared chat: 4'],
  ])('renders %s %j', (type, payload, title, message) => {
    renderRow(notification({ type, payload: { sessionId: 's1', ...payload } }));
    expect(screen.getByRole('link', { name: title })).toBeTruthy();
    expect(screen.getAllByText(message).length).toBeGreaterThan(0);
  });

  it('renders chat.mentioned with localized text, no chat title, and the redirectLink', () => {
    renderRow(
      notification({
        type: 'chat.mentioned',
        title: 'Q3 layoffs plan',
        message: 'Q3 layoffs plan: @carol see this',
        payload: { sessionId: 's1', kind: 'chat', actorUserId: 'u1', messageId: 'm1' },
      }),
    );
    const link = screen.getByRole('link', { name: 'You were mentioned' });
    expect(link.getAttribute('href')).toBe('/chat?conversationId=c1');
    expect(screen.getAllByText('Someone mentioned you in a chat.').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Q3 layoffs/)).toBeNull();
  });

  it('renders chat.deleted without a link', () => {
    renderRow(notification({ type: 'chat.deleted', payload: { sessionId: 's1' } }));
    expect(screen.getByText('Chat deleted')).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('never shows the stored title when it carries a chat title', () => {
    renderRow(notification({ title: 'Secret roadmap', message: 'Secret roadmap was shared' }));
    expect(screen.queryByText(/Secret roadmap/)).toBeNull();
  });

  it('falls back to the stored text for an unknown type', () => {
    renderRow(notification({ type: 'chat.somethingNew', title: 'Stored title', message: 'Stored message' }));
    expect(screen.getByRole('link', { name: 'Stored title' })).toBeTruthy();
    expect(screen.getAllByText('Stored message').length).toBeGreaterThan(0);
  });

  it('marks the row read when the deep link is followed', () => {
    const onMarkRead = vi.fn();
    renderRow(notification(), { onMarkRead });
    fireEvent.click(screen.getByRole('link', { name: 'Chat shared with you' }));
    expect(onMarkRead).toHaveBeenCalledTimes(1);
  });
});

describe('NotificationRow mute', () => {
  it('offers Mute on a collaboration row and calls onToggleMute', () => {
    const onToggleMute = vi.fn();
    renderRow(notification({ type: 'chat.activity' }), { onToggleMute, muteLabel: 'Mute this chat', unmuteLabel: 'Unmute this chat' });
    fireEvent.click(screen.getByRole('button', { name: 'Mute this chat' }));
    expect(onToggleMute).toHaveBeenCalledTimes(1);
  });

  it('offers Unmute when the chat is muted', () => {
    renderRow(notification({ type: 'chat.activity' }), {
      onToggleMute: vi.fn(),
      muted: true,
      muteLabel: 'Mute this chat',
      unmuteLabel: 'Unmute this chat',
    });
    expect(screen.getByRole('button', { name: 'Unmute this chat' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Mute this chat' })).toBeNull();
  });

  it('has no mute button on a non-collaboration row', () => {
    renderRow(notification({ type: 'connector.sync', payload: { sessionId: 's1' } }), {
      onToggleMute: vi.fn(),
      muteLabel: 'Mute this chat',
      unmuteLabel: 'Unmute this chat',
    });
    expect(screen.queryByRole('button', { name: 'Mute this chat' })).toBeNull();
  });
});

describe('NotificationRow flag-off parity', () => {
  it('renders the stored text of a collaboration type and offers no mute', () => {
    setFlag(false);
    renderRow(notification({ type: 'chat.activity' }), {
      onToggleMute: vi.fn(),
      muteLabel: 'Mute this chat',
      unmuteLabel: 'Unmute this chat',
    });
    expect(screen.getByRole('link', { name: 'Conversation shared with you' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Mute this chat' })).toBeNull();
  });

  it('renders an existing type exactly as before with the flag on or off', () => {
    const n = notification({ type: 'connector.sync', title: 'Sync done', message: 'All good', payload: undefined });
    const off = (setFlag(false), renderRow(n).container.innerHTML);
    cleanup();
    const on = (setFlag(true), renderRow(n).container.innerHTML);
    expect(on).toBe(off);
  });
});

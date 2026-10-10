import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k, i18n: { language: 'en' } }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/navigation', () => ({
  Link: ({ href, children, onClick, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      href={href}
      {...rest}
      onClick={(e) => {
        e.preventDefault();
        onClick?.(e);
      }}
    >
      {children}
    </a>
  ),
}));

import { NotificationRow, chatConversationIdFromHref, isExternalNotificationHref } from '../notification-row';
import type { NotificationListItem } from '../api';

function renderRow(redirectLink: string, onOpenLink = vi.fn(), onMarkRead = vi.fn()) {
  const notification: NotificationListItem = {
    _id: 'n1',
    type: 'conversation_shared',
    title: 'Alice shared a conversation',
    redirectLink,
    status: 'unread',
  };
  render(
    <Theme>
      <NotificationRow
        notification={notification}
        onOpenLink={onOpenLink}
        onMarkRead={onMarkRead}
        onMarkUnread={vi.fn()}
        onArchive={vi.fn()}
        onUnarchive={vi.fn()}
        onDismiss={vi.fn()}
        markReadLabel="read"
        markUnreadLabel="unread"
        archiveLabel="archive"
        unarchiveLabel="unarchive"
        dismissLabel="dismiss"
      />
    </Theme>,
  );
  fireEvent.click(screen.getByText('Alice shared a conversation'));
  return { onOpenLink, onMarkRead };
}

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => vi.stubGlobal('ResizeObserver', NoopResizeObserver));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('NotificationRow link', () => {
  it('reports an in-app link it opens, so the page can refresh even when it is the current one', () => {
    const { onOpenLink, onMarkRead } = renderRow('chat/?conversationId=abc');
    expect(onOpenLink).toHaveBeenCalledWith('/chat/?conversationId=abc');
    expect(onMarkRead).toHaveBeenCalledTimes(1);
  });

  it('does not report an external link, which opens in a new tab', () => {
    const { onOpenLink } = renderRow('https://example.com/report');
    expect(onOpenLink).not.toHaveBeenCalled();
  });

  it.each(['//other.example/chat/?conversationId=abc', '/\\other.example/chat/?conversationId=abc'])(
    'treats the protocol-relative link %s as external: new tab, not reported',
    (redirectLink) => {
      const { onOpenLink } = renderRow(redirectLink);
      expect(onOpenLink).not.toHaveBeenCalled();
      const link = screen.getByText('Alice shared a conversation').closest('a');
      expect(link?.getAttribute('target')).toBe('_blank');
      expect(link?.getAttribute('rel')).toBe('noopener noreferrer');
    },
  );
});

describe('isExternalNotificationHref', () => {
  it.each([
    ['https://example.com/x', true],
    ['HTTP://example.com/x', true],
    ['//example.com/x', true],
    ['/\\example.com/x', true],
    ['/chat/?conversationId=abc', false],
    ['chat/?conversationId=abc', false],
  ])('%s is external: %s', (href, expected) => {
    expect(isExternalNotificationHref(href)).toBe(expected);
  });
});

describe('chatConversationIdFromHref', () => {
  it.each([
    ['/chat/?conversationId=abc', 'abc'],
    ['/chat?conversationId=abc', 'abc'],
    ['/chat/?agentId=a1&conversationId=abc', 'abc'],
  ])('reads the conversation from %s', (href, expected) => {
    expect(chatConversationIdFromHref(href)).toBe(expected);
  });

  it.each([
    '/chat/',
    '/knowledge-base?conversationId=abc',
    '/chatroom?conversationId=abc',
    'https://demo.example.com/chat/?conversationId=abc',
    '//demo.example.com/chat/?conversationId=abc',
    '/\\demo.example.com/chat/?conversationId=abc',
  ])('finds no conversation in %s', (href) => {
    expect(chatConversationIdFromHref(href)).toBeNull();
  });
});

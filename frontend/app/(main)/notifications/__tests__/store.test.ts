import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useNotificationStore } from '../store';
import type { NotificationListItem, NotificationListResponse } from '../api';

function makeNotification(
  overrides: Partial<NotificationListItem> & Pick<NotificationListItem, '_id' | 'status'>,
): NotificationListItem {
  return {
    type: 'CONNECTOR_ERROR',
    severity: 'error',
    title: 'Title',
    message: 'Message',
    payload: {
      connectorId: 'conn-1',
      connectorName: 'S3',
    },
    ...overrides,
  };
}

function makePage(
  notifications: NotificationListItem[],
  overrides: Partial<NotificationListResponse> = {},
): NotificationListResponse {
  return {
    notifications,
    cursor: null,
    hasMore: false,
    unreadCount: notifications.filter((n) => n.status === 'unread').length,
    ...overrides,
  };
}

describe('useNotificationStore', () => {
  beforeEach(() => {
    useNotificationStore.setState({
      notifications: [],
      unreadCount: 0,
      cursor: null,
      hasMore: false,
      isLoadingMore: false,
      listFilter: 'all',
    });
  });

  it('setInitialPage replaces list and pagination metadata', () => {
    const items: NotificationListItem[] = [
      makeNotification({
        _id: '1',
        status: 'unread',
        title: 'A',
        message: 'Message A',
        payload: { connectorId: 'c1', connectorName: 'S3' },
      }),
      makeNotification({
        _id: '2',
        status: 'read',
        severity: 'warning',
        title: 'B',
        message: 'Message B',
        payload: { connectorId: 'c2', connectorName: 'Slack' },
      }),
    ];
    useNotificationStore.getState().setInitialPage(
      makePage(items, { unreadCount: 5, hasMore: true, cursor: 'cursor-1' }),
    );
    const s = useNotificationStore.getState();
    expect(s.notifications).toHaveLength(2);
    expect(s.unreadCount).toBe(5);
    expect(s.hasMore).toBe(true);
    expect(s.cursor).toBe('cursor-1');
  });

  it('appendPage merges without duplicates', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'unread' })], {
        hasMore: true,
        cursor: 'c1',
      }),
    );
    useNotificationStore.getState().appendPage(
      makePage(
        [
          makeNotification({ _id: '1', status: 'unread' }),
          makeNotification({ _id: '2', status: 'unread', severity: 'warning' }),
        ],
        { hasMore: false, cursor: null },
      ),
    );
    expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['1', '2']);
    expect(useNotificationStore.getState().hasMore).toBe(false);
  });

  it('addNotification prepends and increments unreadCount for unread items', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'unread' })], { unreadCount: 1 }),
    );
    useNotificationStore.getState().addNotification(
      makeNotification({
        _id: '1',
        status: 'unread',
        title: 'New',
        message: 'New message',
        payload: { connectorId: 'c1', connectorName: 'S3' },
      }),
    );
    expect(useNotificationStore.getState().notifications[0].title).toBe('New');
    expect(useNotificationStore.getState().notifications).toHaveLength(1);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });

  it('markRead updates status and decrements unreadCount', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'unread' })], { unreadCount: 1 }),
    );
    useNotificationStore.getState().markRead('1');
    expect(useNotificationStore.getState().notifications[0].status).toBe('read');
    expect(useNotificationStore.getState().unreadCount).toBe(0);
  });

  it('markAllRead sets all items read and clears unreadCount', () => {
    useNotificationStore.getState().setInitialPage(
      makePage(
        [
          makeNotification({ _id: '1', status: 'unread' }),
          makeNotification({ _id: '2', status: 'unread', severity: 'warning' }),
        ],
        { unreadCount: 2, hasMore: true, cursor: 'cursor-1' },
      ),
    );
    useNotificationStore.getState().markAllRead();
    const s = useNotificationStore.getState();
    expect(s.notifications.every((n) => n.status === 'read')).toBe(true);
    expect(s.unreadCount).toBe(0);
  });

  it('markAllRead on unread filter clears pagination', () => {
    useNotificationStore.setState({ listFilter: 'unread' });
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'unread' })], {
        unreadCount: 1,
        hasMore: true,
        cursor: 'c1',
      }),
    );
    useNotificationStore.getState().markAllRead();
    const s = useNotificationStore.getState();
    expect(s.hasMore).toBe(false);
    expect(s.cursor).toBe(null);
  });

  it('remove drops notification and decrements unreadCount', () => {
    useNotificationStore.getState().setInitialPage(
      makePage(
        [
          makeNotification({ _id: '1', status: 'unread' }),
          makeNotification({ _id: '2', status: 'unread', severity: 'warning' }),
        ],
        { unreadCount: 2 },
      ),
    );
    useNotificationStore.getState().remove('1');
    expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['2']);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });
});

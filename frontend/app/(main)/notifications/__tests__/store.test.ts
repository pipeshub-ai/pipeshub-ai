import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useNotificationStore } from '../store';
import type { NotificationListItem, NotificationListResponse } from '../api';

function makeNotification(
  overrides: Partial<NotificationListItem> & Pick<NotificationListItem, '_id' | 'status'>,
): NotificationListItem {
  return {
    type: 'CONNECTOR_ERROR',
    severity: 'error',
    payload: {
      title: 'Title',
      message: 'Message',
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
    unreadCount: notifications.filter((n) => n.status === 'Unread').length,
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
    });
  });

  it('setInitialPage replaces list and pagination metadata', () => {
    const items: NotificationListItem[] = [
      makeNotification({
        _id: '1',
        status: 'Unread',
        payload: {
          title: 'A',
          message: 'Message A',
          connectorId: 'c1',
          connectorName: 'S3',
        },
      }),
      makeNotification({
        _id: '2',
        status: 'Read',
        severity: 'warning',
        payload: {
          title: 'B',
          message: 'Message B',
          connectorId: 'c2',
          connectorName: 'Slack',
        },
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
      makePage([makeNotification({ _id: '1', status: 'Unread' })], {
        hasMore: true,
        cursor: 'c1',
      }),
    );
    useNotificationStore.getState().appendPage(
      makePage(
        [
          makeNotification({ _id: '1', status: 'Unread' }),
          makeNotification({ _id: '2', status: 'Unread', severity: 'warning' }),
        ],
        { hasMore: false, cursor: null },
      ),
    );
    expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['1', '2']);
    expect(useNotificationStore.getState().hasMore).toBe(false);
  });

  it('addNotification prepends and increments unreadCount for unread items', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'Unread' })], { unreadCount: 1 }),
    );
    useNotificationStore.getState().addNotification(
      makeNotification({
        _id: '1',
        status: 'Unread',
        payload: {
          title: 'New',
          message: 'New message',
          connectorId: 'c1',
          connectorName: 'S3',
        },
      }),
    );
    expect(useNotificationStore.getState().notifications[0].payload?.title).toBe('New');
    expect(useNotificationStore.getState().notifications).toHaveLength(1);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });

  it('markRead updates status and decrements unreadCount', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'Unread' })], { unreadCount: 1 }),
    );
    useNotificationStore.getState().markRead('1');
    expect(useNotificationStore.getState().notifications[0].status).toBe('Read');
    expect(useNotificationStore.getState().unreadCount).toBe(0);
  });

  it('remove drops notification and decrements unreadCount', () => {
    useNotificationStore.getState().setInitialPage(
      makePage(
        [
          makeNotification({ _id: '1', status: 'Unread' }),
          makeNotification({ _id: '2', status: 'Unread', severity: 'warning' }),
        ],
        { unreadCount: 2 },
      ),
    );
    useNotificationStore.getState().remove('1');
    expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['2']);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });
});

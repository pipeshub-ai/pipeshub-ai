import { describe, it, expect, beforeEach } from 'vitest';
import { useNotificationStore } from '../store';
import type { NotificationListItem } from '../api';

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

describe('useNotificationStore', () => {
  beforeEach(() => {
    useNotificationStore.setState({
      notifications: [],
      unreadCount: 0,
    });
  });

  it('setAll replaces list and counts unread', () => {
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
    useNotificationStore.getState().setAll(items);
    const s = useNotificationStore.getState();
    expect(s.notifications).toHaveLength(2);
    expect(s.unreadCount).toBe(1);
  });

  it('addNotification prepends and dedupes by _id', () => {
    useNotificationStore.getState().setAll([
      makeNotification({
        _id: '1',
        status: 'Unread',
        payload: {
          title: 'Old',
          message: 'Old message',
          connectorId: 'c1',
          connectorName: 'S3',
        },
      }),
    ]);
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
  });

  it('markRead updates status and unread count', () => {
    useNotificationStore.getState().setAll([
      makeNotification({ _id: '1', status: 'Unread' }),
    ]);
    useNotificationStore.getState().markRead('1');
    expect(useNotificationStore.getState().notifications[0].status).toBe('Read');
    expect(useNotificationStore.getState().unreadCount).toBe(0);
  });

  it('remove drops notification', () => {
    useNotificationStore.getState().setAll([
      makeNotification({ _id: '1', status: 'Unread' }),
      makeNotification({ _id: '2', status: 'Unread', severity: 'warning' }),
    ]);
    useNotificationStore.getState().remove('1');
    expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['2']);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });
});

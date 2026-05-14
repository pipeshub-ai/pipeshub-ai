import { describe, it, expect, beforeEach } from 'vitest';
import { useNotificationStore } from '../store';
import type { NotificationListItem } from '../api';

describe('useNotificationStore', () => {
  beforeEach(() => {
    useNotificationStore.setState({
      notifications: [],
      unreadCount: 0,
    });
  });

  it('setAll replaces list and counts unread', () => {
    const items: NotificationListItem[] = [
      { _id: '1', title: 'A', type: 'X', link: '/', status: 'Unread' },
      { _id: '2', title: 'B', type: 'X', link: '/', status: 'Read' },
    ];
    useNotificationStore.getState().setAll(items);
    const s = useNotificationStore.getState();
    expect(s.notifications).toHaveLength(2);
    expect(s.unreadCount).toBe(1);
  });

  it('addNotification prepends and dedupes by _id', () => {
    useNotificationStore.getState().setAll([
      { _id: '1', title: 'Old', type: 'X', link: '/', status: 'Unread' },
    ]);
    useNotificationStore.getState().addNotification({
      _id: '1',
      title: 'New',
      type: 'X',
      link: '/',
      status: 'Unread',
    });
    expect(useNotificationStore.getState().notifications[0].title).toBe('New');
    expect(useNotificationStore.getState().notifications).toHaveLength(1);
  });

  it('markRead updates status and unread count', () => {
    useNotificationStore.getState().setAll([
      { _id: '1', title: 'A', type: 'X', link: '/', status: 'Unread' },
    ]);
    useNotificationStore.getState().markRead('1');
    expect(useNotificationStore.getState().notifications[0].status).toBe('Read');
    expect(useNotificationStore.getState().unreadCount).toBe(0);
  });

  it('remove drops notification', () => {
    useNotificationStore.getState().setAll([
      { _id: '1', title: 'A', type: 'X', link: '/', status: 'Unread' },
      { _id: '2', title: 'B', type: 'X', link: '/', status: 'Unread' },
    ]);
    useNotificationStore.getState().remove('1');
    expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['2']);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });
});

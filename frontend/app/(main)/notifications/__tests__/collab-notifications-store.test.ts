import { describe, it, expect, beforeEach } from 'vitest';
import { useNotificationStore } from '../store';
import { isCollabNotificationType, truncateNote, collabSessionId } from '../collab-notifications';
import type { NotificationListItem } from '../api';

const item = (over: Partial<NotificationListItem> = {}): NotificationListItem => ({
  _id: 'n1',
  type: 'chat.activity',
  status: 'unread',
  payload: { sessionId: 's1', count: 1 },
  ...over,
});

beforeEach(() => {
  useNotificationStore.setState({ notifications: [], unreadCount: 0, mutedSessionIds: [] });
});

describe('upsertNotification (coalesced chat.activity)', () => {
  it('replaces the row in place of adding a duplicate and moves it to the top', () => {
    const s = useNotificationStore.getState();
    s.addNotification(item());
    s.addNotification(item({ _id: 'n2', type: 'chat.shared', payload: { sessionId: 's2' } }));
    s.upsertNotification(item({ payload: { sessionId: 's1', count: 3 } }));
    const { notifications, unreadCount } = useNotificationStore.getState();
    expect(notifications.map((n) => n._id)).toEqual(['n1', 'n2']);
    expect(notifications[0].payload?.count).toBe(3);
    expect(unreadCount).toBe(2);
  });

  it('counts a row that turns unread again once', () => {
    useNotificationStore.setState({ notifications: [item({ status: 'read' })], unreadCount: 0 });
    useNotificationStore.getState().upsertNotification(item({ payload: { sessionId: 's1', count: 2 } }));
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });

  it('adds a row it has not seen', () => {
    useNotificationStore.getState().upsertNotification(item());
    expect(useNotificationStore.getState().notifications).toHaveLength(1);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });
});

describe('collab helpers', () => {
  it('recognizes the five types only', () => {
    expect(['chat.shared', 'chat.accessChanged', 'chat.ownershipTransferred', 'chat.deleted', 'chat.activity'].every(isCollabNotificationType)).toBe(true);
    expect(isCollabNotificationType('chat.other')).toBe(false);
  });
  it('truncates by code point', () => {
    expect(truncateNote('a'.repeat(140))).toHaveLength(140);
    expect(truncateNote('a'.repeat(141))).toHaveLength(140);
    expect(Array.from(truncateNote('😀'.repeat(200)))).toHaveLength(140);
  });
  it('reads the session id only for collaboration types', () => {
    expect(collabSessionId(item())).toBe('s1');
    expect(collabSessionId(item({ type: 'x', payload: { sessionId: 's1' } }))).toBeUndefined();
    expect(collabSessionId(item({ payload: undefined }))).toBeUndefined();
  });
});

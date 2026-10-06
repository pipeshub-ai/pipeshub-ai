import { describe, it, expect, beforeEach } from 'vitest';
import i18next from 'i18next';
import '@/lib/__tests__/test-i18n';
import { describeCollabNotification } from '../collab-notifications';
import { useNotificationStore } from '../store';
import type { NotificationListItem } from '../api';

const t = i18next.t.bind(i18next);
const row = (type: string, context?: NotificationListItem['context'], payload: Record<string, unknown> = {}): NotificationListItem => ({
  _id: 'n1', type, status: 'unread', context, payload: { sessionId: 's1', accessLevel: 'write', ...payload },
});
const msg = (r: NotificationListItem) => describeCollabNotification(r, t as never)?.message;
const BOTH = { chatTitle: 'Q3 plan', actorName: 'Alex' };

describe('describeCollabNotification context', () => {
  it('chat.shared: both, chat only, actor only, none', () => {
    expect(msg(row('chat.shared', BOTH))).toBe('Alex shared “Q3 plan” with you. You can continue it.');
    expect(msg(row('chat.shared', { chatTitle: 'Q3 plan' }))).toBe('“Q3 plan” was shared with you. You can continue it.');
    expect(msg(row('chat.shared', { actorName: 'Alex' }))).toBe('Alex shared a chat with you. You can continue it.');
    expect(msg(row('chat.shared'))).toBe('A chat was shared with you. You can continue it.');
    expect(msg(row('chat.shared', {}))).toBe('A chat was shared with you. You can continue it.');
  });

  it('chat.shared read level and the handover note still append', () => {
    expect(msg(row('chat.shared', BOTH, { accessLevel: 'read', note: 'hi' }))).toBe('Alex shared “Q3 plan” with you. You can view it. Note: hi');
  });

  it('chat.mentioned: both, chat only, actor only, none', () => {
    expect(msg(row('chat.mentioned', BOTH))).toBe('Alex mentioned you in “Q3 plan”.');
    expect(msg(row('chat.mentioned', { chatTitle: 'Q3 plan' }))).toBe('You were mentioned in “Q3 plan”.');
    expect(msg(row('chat.mentioned', { actorName: 'Alex' }))).toBe('Alex mentioned you in a chat.');
    expect(msg(row('chat.mentioned'))).toBe('Someone mentioned you in a chat.');
  });

  it('accessChanged, ownershipTransferred and activity use the title only', () => {
    expect(msg(row('chat.accessChanged', BOTH))).toBe('You can now continue “Q3 plan”.');
    expect(msg(row('chat.accessChanged', { actorName: 'Alex' }))).toBe('You can now continue a shared chat.');
    expect(msg(row('chat.ownershipTransferred', BOTH))).toBe('You are now the owner of “Q3 plan”.');
    expect(msg(row('chat.ownershipTransferred'))).toBe('You are now the owner of a chat.');
    expect(msg(row('chat.activity', BOTH, { count: 3 }))).toBe('New activity in “Q3 plan”: 3');
    expect(msg(row('chat.activity', undefined, { count: 3 }))).toBe('New activity in a shared chat: 3');
  });

  it('chat.deleted never names the chat, and blank or non-string fields are ignored', () => {
    expect(msg(row('chat.deleted', BOTH))).toBe('A chat you took part in was deleted.');
    expect(msg(row('chat.shared', { chatTitle: '  ', actorName: 42 as never }))).toBe('A chat was shared with you. You can continue it.');
  });

  it('keeps markup in a title as literal text', () => {
    expect(msg(row('chat.mentioned', { chatTitle: '<b>x</b>', actorName: 'A&B' }))).toBe('A&B mentioned you in “<b>x</b>”.');
  });
});

describe('notification store keeps context', () => {
  beforeEach(() => useNotificationStore.setState({ notifications: [], unreadCount: 0 }));

  it('a websocket row replaced by the list fetch picks up the context', () => {
    const s = useNotificationStore.getState();
    s.addNotification(row('chat.shared'));
    useNotificationStore.getState().setInitialPage({ notifications: [row('chat.shared', BOTH)], cursor: null, hasMore: false });
    expect(useNotificationStore.getState().notifications[0].context).toEqual(BOTH);
  });

  it('a later page carrying context enriches a websocket row already present', () => {
    useNotificationStore.getState().addNotification(row('chat.shared'));
    useNotificationStore.getState().appendPage({ notifications: [row('chat.shared', BOTH)], cursor: null, hasMore: false });
    const { notifications } = useNotificationStore.getState();
    expect(notifications).toHaveLength(1);
    expect(notifications[0].context).toEqual(BOTH);
  });

  it('a websocket update without context does not drop the context already resolved', () => {
    useNotificationStore.setState({ notifications: [row('chat.activity', BOTH)] });
    useNotificationStore.getState().upsertNotification({ ...row('chat.activity'), status: 'read' });
    const n = useNotificationStore.getState().notifications[0];
    expect(n.status).toBe('read');
    expect(n.context).toEqual(BOTH);
  });
});

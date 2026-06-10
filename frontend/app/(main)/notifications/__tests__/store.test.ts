import { describe, it, expect, beforeEach, vi } from 'vitest';
import { getVisibleNotifications, useNotificationStore } from '../store';
import { NotificationsApi, type NotificationListItem, type NotificationListResponse } from '../api';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    NotificationsApi: {
      ...actual.NotificationsApi,
      list: vi.fn(),
    },
  };
});

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
    ...overrides,
  };
}

function makeUnreadBatch(count: number, startId = 1): NotificationListItem[] {
  return Array.from({ length: count }, (_, i) =>
    makeNotification({ _id: String(startId + i), status: 'unread' }),
  );
}

describe('useNotificationStore', () => {
  beforeEach(() => {
    vi.mocked(NotificationsApi.list).mockReset();
    useNotificationStore.setState({
      notifications: [],
      unreadCount: 0,
      readCount: 0,
      archivedCount: 0,
      cursor: null,
      hasMore: false,
      isLoadingMore: false,
      listFilter: 'all',
    });
  });

  it('setInitialPage replaces list and pagination metadata (does not touch counts)', () => {
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
    useNotificationStore.setState({ unreadCount: 7 });
    useNotificationStore.getState().setInitialPage(
      makePage(items, { hasMore: true, cursor: 'cursor-1' }),
    );
    const s = useNotificationStore.getState();
    expect(s.notifications).toHaveLength(2);
    expect(s.unreadCount).toBe(7); // setInitialPage must not overwrite counts
    expect(s.hasMore).toBe(true);
    expect(s.cursor).toBe('cursor-1');
  });

  it('setStats sets unreadCount, readCount, archivedCount', () => {
    useNotificationStore.getState().setStats({ unreadCount: 5, readCount: 12, archivedCount: 3 });
    const s = useNotificationStore.getState();
    expect(s.unreadCount).toBe(5);
    expect(s.readCount).toBe(12);
    expect(s.archivedCount).toBe(3);
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
      makePage([makeNotification({ _id: '1', status: 'unread' })]),
    );
    useNotificationStore.setState({ unreadCount: 1 });
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
      makePage([makeNotification({ _id: '1', status: 'unread' })]),
    );
    useNotificationStore.setState({ unreadCount: 1 });
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
        { hasMore: true, cursor: 'cursor-1' },
      ),
    );
    useNotificationStore.setState({ unreadCount: 2 });
    useNotificationStore.getState().markAllRead();
    const s = useNotificationStore.getState();
    expect(s.notifications.every((n) => n.status === 'read')).toBe(true);
    expect(s.unreadCount).toBe(0);
  });

  it('markAllRead on unread filter clears pagination', () => {
    useNotificationStore.setState({ listFilter: 'unread' });
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'unread' })], {
        hasMore: true,
        cursor: 'c1',
      }),
    );
    useNotificationStore.setState({ unreadCount: 1 });
    useNotificationStore.getState().markAllRead();
    const s = useNotificationStore.getState();
    expect(s.hasMore).toBe(false);
    expect(s.cursor).toBe(null);
  });

  it('archive sets status to archived, increments archivedCount, decrements unreadCount when was unread', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'unread' })]),
    );
    useNotificationStore.setState({ unreadCount: 1, archivedCount: 0 });
    useNotificationStore.getState().archive('1');
    const s = useNotificationStore.getState();
    expect(s.notifications[0].status).toBe('archived');
    expect(s.unreadCount).toBe(0);
    expect(s.archivedCount).toBe(1);
  });

  it('archive increments archivedCount without changing unreadCount when notification was already read', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'read' })]),
    );
    useNotificationStore.setState({ unreadCount: 2, archivedCount: 0 });
    useNotificationStore.getState().archive('1');
    const s = useNotificationStore.getState();
    expect(s.notifications[0].status).toBe('archived');
    expect(s.unreadCount).toBe(2);
    expect(s.archivedCount).toBe(1);
  });

  it('archive is a no-op when notification is already archived', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'archived' })]),
    );
    useNotificationStore.setState({ unreadCount: 0, archivedCount: 1 });
    useNotificationStore.getState().archive('1');
    const s = useNotificationStore.getState();
    expect(s.archivedCount).toBe(1);
    expect(s.unreadCount).toBe(0);
  });

  it('unarchive sets status to read, decrements archivedCount, increments readCount', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'archived' })]),
    );
    useNotificationStore.setState({ archivedCount: 1, readCount: 0 });
    useNotificationStore.getState().unarchive('1');
    const s = useNotificationStore.getState();
    expect(s.notifications[0].status).toBe('read');
    expect(s.archivedCount).toBe(0);
    expect(s.readCount).toBe(1);
  });

  it('unarchive is a no-op when notification is not archived', () => {
    useNotificationStore.getState().setInitialPage(
      makePage([makeNotification({ _id: '1', status: 'unread' })]),
    );
    useNotificationStore.setState({ archivedCount: 0, readCount: 0 });
    useNotificationStore.getState().unarchive('1');
    const s = useNotificationStore.getState();
    expect(s.notifications[0].status).toBe('unread');
    expect(s.archivedCount).toBe(0);
    expect(s.readCount).toBe(0);
  });

  it('remove drops notification and decrements unreadCount', () => {
    useNotificationStore.getState().setInitialPage(
      makePage(
        [
          makeNotification({ _id: '1', status: 'unread' }),
          makeNotification({ _id: '2', status: 'unread', severity: 'warning' }),
        ],
      ),
    );
    useNotificationStore.setState({ unreadCount: 2 });
    useNotificationStore.getState().remove('1');
    expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['2']);
    expect(useNotificationStore.getState().unreadCount).toBe(1);
  });

  describe('ensureBackfill', () => {
    it('loads pages until visible count reaches 20', async () => {
      useNotificationStore.getState().setInitialPage(
        makePage([makeNotification({ _id: '1', status: 'unread' })], {
          hasMore: true,
          cursor: 'c1',
        }),
      );
      vi.mocked(NotificationsApi.list).mockResolvedValueOnce(
        makePage(makeUnreadBatch(19, 2), { hasMore: false, cursor: null }),
      );

      await useNotificationStore.getState().ensureBackfill();

      expect(NotificationsApi.list).toHaveBeenCalledTimes(1);
      const visible = getVisibleNotifications(
        useNotificationStore.getState().notifications,
        'all',
      );
      expect(visible).toHaveLength(20);
    });

    it('does not call API when visible count is already 20', async () => {
      useNotificationStore.getState().setInitialPage(
        makePage(makeUnreadBatch(20), { hasMore: true, cursor: 'c1' }),
      );

      await useNotificationStore.getState().ensureBackfill();

      expect(NotificationsApi.list).not.toHaveBeenCalled();
    });

    it('does not call API when hasMore is false despite visible below 20', async () => {
      useNotificationStore.getState().setInitialPage(
        makePage(makeUnreadBatch(5), { hasMore: false, cursor: null }),
      );

      await useNotificationStore.getState().ensureBackfill();

      expect(NotificationsApi.list).not.toHaveBeenCalled();
    });

    it('stops after max page cap when pages add no visible items in all view', async () => {
      useNotificationStore.getState().setInitialPage(
        makePage([makeNotification({ _id: '1', status: 'unread' })], {
          hasMore: true,
          cursor: 'c1',
        }),
      );
      vi.mocked(NotificationsApi.list).mockImplementation(async () =>
        makePage([makeNotification({ _id: 'archived-only', status: 'archived' })], {
          hasMore: true,
          cursor: 'next',
        }),
      );

      await useNotificationStore.getState().ensureBackfill();

      expect(NotificationsApi.list).toHaveBeenCalledTimes(5);
      expect(
        getVisibleNotifications(useNotificationStore.getState().notifications, 'all'),
      ).toHaveLength(1);
    });

    it('refills after remove when visible count drops below 20', async () => {
      useNotificationStore.getState().setInitialPage(
        makePage([makeNotification({ _id: '1', status: 'unread' })], {
          hasMore: true,
          cursor: 'c1',
        }),
      );
      vi.mocked(NotificationsApi.list).mockResolvedValueOnce(
        makePage([makeNotification({ _id: '2', status: 'unread' })], {
          hasMore: false,
          cursor: null,
        }),
      );

      useNotificationStore.getState().remove('1');
      await useNotificationStore.getState().ensureBackfill();

      expect(NotificationsApi.list).toHaveBeenCalledTimes(1);
      expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['2']);
    });
  });
});

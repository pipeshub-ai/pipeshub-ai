import { create } from 'zustand';
import {
  DEFAULT_NOTIFICATION_PAGE_SIZE,
  NotificationsApi,
  type NotificationListFilter,
  type NotificationListItem,
  type NotificationListResponse,
  type NotificationStatsResponse,
} from './api';

const MAX_BACKFILL_PAGES = 5;
const MIN_VISIBLE = DEFAULT_NOTIFICATION_PAGE_SIZE;

function visibleForFilter(
  notifications: NotificationListItem[],
  filter: NotificationListFilter,
): NotificationListItem[] {
  if (filter === 'unread') {
    return notifications.filter((n) => n.status === 'unread');
  }
  if (filter === 'archived') {
    return notifications.filter((n) => n.status === 'archived');
  }
  return notifications.filter((n) => n.status !== 'archived');
}

export function getVisibleNotifications(
  notifications: NotificationListItem[],
  filter: NotificationListFilter,
): NotificationListItem[] {
  return visibleForFilter(notifications, filter);
}

function listParamsForFilter(
  filter: NotificationListFilter,
  extra?: { cursor?: string },
) {
  return {
    limit: DEFAULT_NOTIFICATION_PAGE_SIZE,
    ...(filter === 'unread' ? { status: 'unread' as const } : {}),
    ...(filter === 'archived' ? { status: 'archived' as const } : {}),
    ...extra,
  };
}

interface NotificationState {
  notifications: NotificationListItem[];
  unreadCount: number;
  readCount: number;
  archivedCount: number;
  cursor: string | null;
  hasMore: boolean;
  isLoadingMore: boolean;
  listFilter: NotificationListFilter;
  isPanelOpen: boolean;
  openPanel: () => void;
  closePanel: () => void;
  togglePanel: () => void;
  setListFilter: (filter: NotificationListFilter) => void;
  setInitialPage: (response: NotificationListResponse) => void;
  setStats: (stats: NotificationStatsResponse) => void;
  appendPage: (response: NotificationListResponse) => void;
  loadMore: () => Promise<void>;
  ensureBackfill: () => Promise<void>;
  addNotification: (item: NotificationListItem) => void;
  markRead: (id: string) => void;
  markUnread: (id: string) => void;
  markAllRead: () => void;
  remove: (id: string) => void;
  archive: (id: string) => void;
  unarchive: (id: string) => void;
}

function dedupeAppend(
  existing: NotificationListItem[],
  incoming: NotificationListItem[],
): NotificationListItem[] {
  const seen = new Set(existing.map((n) => n._id));
  const merged = [...existing];
  for (const item of incoming) {
    if (item._id && !seen.has(item._id)) {
      seen.add(item._id);
      merged.push(item);
    }
  }
  return merged;
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  unreadCount: 0,
  readCount: 0,
  archivedCount: 0,
  cursor: null,
  hasMore: false,
  isLoadingMore: false,
  listFilter: 'all',
  isPanelOpen: false,
  setListFilter: (filter) => set({ listFilter: filter, hasMore: false, cursor: null }),
  openPanel: () => set({ isPanelOpen: true }),
  closePanel: () => set({ isPanelOpen: false }),
  togglePanel: () => set((s) => ({ isPanelOpen: !s.isPanelOpen })),

  setInitialPage: (response) =>
    set({
      notifications: response.notifications,
      cursor: response.cursor,
      hasMore: response.hasMore,
    }),

  setStats: (stats) =>
    set({
      unreadCount: stats.unreadCount,
      readCount: stats.readCount,
      archivedCount: stats.archivedCount,
    }),

  appendPage: (response) =>
    set((state) => ({
      notifications: dedupeAppend(state.notifications, response.notifications),
      cursor: response.cursor,
      hasMore: response.hasMore,
    })),

  loadMore: async () => {
    const { isLoadingMore, hasMore, cursor } = get();
    if (isLoadingMore || !hasMore || !cursor) {
      return;
    }
    set({ isLoadingMore: true });
    try {
      const response = await NotificationsApi.list(
        listParamsForFilter(get().listFilter, { cursor }),
      );
      get().appendPage(response);
    } finally {
      set({ isLoadingMore: false });
    }
  },

  ensureBackfill: async () => {
    for (let i = 0; i < MAX_BACKFILL_PAGES; i++) {
      const { listFilter, notifications, hasMore, cursor, isLoadingMore } = get();
      if (!hasMore || !cursor || isLoadingMore) break;

      const visible = visibleForFilter(notifications, listFilter);
      if (visible.length >= MIN_VISIBLE) break;

      await get().loadMore();
    }
  },

  addNotification: (item) => {
    const prev = get().notifications;
    const isDuplicate = prev.some((n) => n._id === item._id);
    const wasUnread = item.status === 'unread';
    const next = [item, ...prev.filter((n) => n._id !== item._id)];
    set({
      notifications: next,
      unreadCount: !isDuplicate && wasUnread ? get().unreadCount + 1 : get().unreadCount,
    });
  },

  markRead: (id) => {
    const next = get().notifications.map((n) =>
      n._id === id ? { ...n, status: 'read' as const } : n,
    );
    set({
      notifications: next,
    });
  },

  markUnread: (id) => {
    const next = get().notifications.map((n) =>
      n._id === id ? { ...n, status: 'unread' as const } : n,
    );
    set({
      notifications: next,
    });
  },

  markAllRead: () => {
    const { listFilter, notifications } = get();
    set({
      notifications: notifications.map((n) => ({ ...n, status: 'read' as const })),
      ...(listFilter === 'unread' ? { hasMore: false, cursor: null } : {}),
    });
  },

  remove: (id) => {
    const next = get().notifications.filter((n) => n._id !== id);
    set({
      notifications: next,
    });
  },

  archive: (id) => {
    const target = get().notifications.find((n) => n._id === id);
    if (!target || target.status === 'archived') return;
    set({
      notifications: get().notifications.map((n) =>
        n._id === id ? { ...n, status: 'archived' as const } : n,
      ),
      archivedCount: get().archivedCount + 1,
    });
  },

  unarchive: (id) => {
    const target = get().notifications.find((n) => n._id === id);
    if (!target || target.status !== 'archived') return;
    set({
      notifications: get().notifications.map((n) =>
        n._id === id ? { ...n, status: 'read' as const } : n,
      ),
      archivedCount: Math.max(0, get().archivedCount - 1),
      readCount: get().readCount + 1,
    });
  },
}));

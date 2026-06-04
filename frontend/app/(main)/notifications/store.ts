import { create } from 'zustand';
import {
  DEFAULT_NOTIFICATION_PAGE_SIZE,
  NotificationsApi,
  type NotificationListFilter,
  type NotificationListItem,
  type NotificationListResponse,
  type NotificationStatsResponse,
} from './api';

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
  addNotification: (item: NotificationListItem) => void;
  markRead: (id: string) => void;
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
    const target = get().notifications.find((n) => n._id === id);
    const wasUnread = target?.status === 'unread';
    const next = get().notifications.map((n) =>
      n._id === id ? { ...n, status: 'read' as const } : n,
    );
    set({
      notifications: next,
      unreadCount: wasUnread ? Math.max(0, get().unreadCount - 1) : get().unreadCount,
    });
  },

  markAllRead: () => {
    const { listFilter, notifications } = get();
    set({
      notifications: notifications.map((n) => ({ ...n, status: 'read' as const })),
      unreadCount: 0,
      ...(listFilter === 'unread' ? { hasMore: false, cursor: null } : {}),
    });
  },

  remove: (id) => {
    const target = get().notifications.find((n) => n._id === id);
    const wasUnread = target?.status === 'unread';
    const next = get().notifications.filter((n) => n._id !== id);
    set({
      notifications: next,
      unreadCount: wasUnread ? Math.max(0, get().unreadCount - 1) : get().unreadCount,
    });
  },

  archive: (id) => {
    const target = get().notifications.find((n) => n._id === id);
    if (!target || target.status === 'archived') return;
    const wasUnread = target.status === 'unread';
    set({
      notifications: get().notifications.map((n) =>
        n._id === id ? { ...n, status: 'archived' as const } : n,
      ),
      unreadCount: wasUnread ? Math.max(0, get().unreadCount - 1) : get().unreadCount,
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

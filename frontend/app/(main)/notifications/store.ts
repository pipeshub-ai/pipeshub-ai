import { create } from 'zustand';
import {
  DEFAULT_NOTIFICATION_PAGE_SIZE,
  NotificationsApi,
  type NotificationListItem,
  type NotificationListResponse,
} from './api';

interface NotificationState {
  notifications: NotificationListItem[];
  unreadCount: number;
  cursor: string | null;
  hasMore: boolean;
  isLoadingMore: boolean;
  isPanelOpen: boolean;
  openPanel: () => void;
  closePanel: () => void;
  togglePanel: () => void;
  setInitialPage: (response: NotificationListResponse) => void;
  appendPage: (response: NotificationListResponse) => void;
  loadMore: () => Promise<void>;
  addNotification: (item: NotificationListItem) => void;
  markRead: (id: string) => void;
  remove: (id: string) => void;
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
  cursor: null,
  hasMore: false,
  isLoadingMore: false,
  isPanelOpen: false,
  openPanel: () => set({ isPanelOpen: true }),
  closePanel: () => set({ isPanelOpen: false }),
  togglePanel: () => set((s) => ({ isPanelOpen: !s.isPanelOpen })),

  setInitialPage: (response) =>
    set({
      notifications: response.notifications,
      unreadCount: response.unreadCount,
      cursor: response.cursor,
      hasMore: response.hasMore,
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
      const response = await NotificationsApi.list({
        limit: DEFAULT_NOTIFICATION_PAGE_SIZE,
        cursor,
      });
      get().appendPage(response);
    } finally {
      set({ isLoadingMore: false });
    }
  },

  addNotification: (item) => {
    const prev = get().notifications;
    const isDuplicate = prev.some((n) => n._id === item._id);
    const wasUnread = item.status === 'Unread';
    const next = [item, ...prev.filter((n) => n._id !== item._id)];
    set({
      notifications: next,
      unreadCount: !isDuplicate && wasUnread ? get().unreadCount + 1 : get().unreadCount,
    });
  },

  markRead: (id) => {
    const target = get().notifications.find((n) => n._id === id);
    const wasUnread = target?.status === 'Unread';
    const next = get().notifications.map((n) =>
      n._id === id ? { ...n, status: 'Read' as const } : n,
    );
    set({
      notifications: next,
      unreadCount: wasUnread ? Math.max(0, get().unreadCount - 1) : get().unreadCount,
    });
  },

  remove: (id) => {
    const target = get().notifications.find((n) => n._id === id);
    const wasUnread = target?.status === 'Unread';
    const next = get().notifications.filter((n) => n._id !== id);
    set({
      notifications: next,
      unreadCount: wasUnread ? Math.max(0, get().unreadCount - 1) : get().unreadCount,
    });
  },
}));

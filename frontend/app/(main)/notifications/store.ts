import { create } from 'zustand';
import type { NotificationListItem } from './api';

function countUnread(items: NotificationListItem[]): number {
  return items.filter((n) => n.status === 'Unread').length;
}

interface NotificationState {
  notifications: NotificationListItem[];
  unreadCount: number;
  isPanelOpen: boolean;
  openPanel: () => void;
  closePanel: () => void;
  togglePanel: () => void;
  setAll: (items: NotificationListItem[]) => void;
  addNotification: (item: NotificationListItem) => void;
  markRead: (id: string) => void;
  remove: (id: string) => void;
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  unreadCount: 0,
  isPanelOpen: false,
  openPanel: () => set({ isPanelOpen: true }),
  closePanel: () => set({ isPanelOpen: false }),
  togglePanel: () => set((s) => ({ isPanelOpen: !s.isPanelOpen })),

  setAll: (items) =>
    set({
      notifications: items,
      unreadCount: countUnread(items),
    }),

  addNotification: (item) => {
    const prev = get().notifications;
    const next = [item, ...prev.filter((n) => n._id !== item._id)];
    set({
      notifications: next,
      unreadCount: countUnread(next),
    });
  },

  markRead: (id) => {
    const next = get().notifications.map((n) =>
      n._id === id ? { ...n, status: 'Read' as const } : n,
    );
    set({
      notifications: next,
      unreadCount: countUnread(next),
    });
  },

  remove: (id) => {
    const next = get().notifications.filter((n) => n._id !== id);
    set({
      notifications: next,
      unreadCount: countUnread(next),
    });
  },
}));

import { apiClient } from '@/lib/api';

export interface NotificationListItem {
  _id: string;
  title: string;
  type: string;
  link: string;
  status: string;
  orgId?: string;
  assignedTo?: string;
  appName?: string;
  appId?: string;
  payload?: Record<string, unknown>;
  createdAt?: string;
  updatedAt?: string;
}

export const NotificationsApi = {
  async getAll(): Promise<NotificationListItem[]> {
    const { data } = await apiClient.get<{ notifications: NotificationListItem[] }>(
      '/api/v1/notifications',
    );
    return data.notifications ?? [];
  },

  async markRead(id: string): Promise<NotificationListItem> {
    const { data } = await apiClient.patch<{ notification: NotificationListItem }>(
      `/api/v1/notifications/${encodeURIComponent(id)}/read`,
    );
    return data.notification;
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/api/v1/notifications/${encodeURIComponent(id)}`);
  },
};

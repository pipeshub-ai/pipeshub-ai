import { apiClient } from '@/lib/api';

export type NotificationSeverity = 'info' | 'warning' | 'error' | 'critical';

export type NotificationStatus = 'Read' | 'Unread' | 'Archived';

export type NotificationOrigin =
  | 'Connector Service'
  | 'Indexing Service'
  | 'AI Service'
  | 'External Service'
  | 'PipesHub';

export interface NotificationPayload {
  title: string;
  message: string;
  connectorId: string;
  connectorName: string;
  errorCode?: string;
  redirectLink?: string;
}

/** Matches the Mongo/API notification document shape (.lean() / websocket). */
export interface NotificationListItem {
  _id: string;
  orgId?: string;
  type: string;
  severity?: NotificationSeverity;
  status: NotificationStatus;
  origin?: NotificationOrigin;
  assignedTo?: string;
  payload?: NotificationPayload;
  isDeleted?: boolean;
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

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

export interface NotificationListResponse {
  notifications: NotificationListItem[];
  cursor: string | null;
  hasMore: boolean;
  unreadCount: number;
}

export interface NotificationListParams {
  limit?: number;
  cursor?: string;
}

export const DEFAULT_NOTIFICATION_PAGE_SIZE = 20;

export const NotificationsApi = {
  async list(params: NotificationListParams = {}): Promise<NotificationListResponse> {
    const limit = params.limit ?? DEFAULT_NOTIFICATION_PAGE_SIZE;
    const searchParams = new URLSearchParams({ limit: String(limit) });
    if (params.cursor) {
      searchParams.set('cursor', params.cursor);
    }
    const { data } = await apiClient.get<NotificationListResponse>(
      `/api/v1/notifications?${searchParams.toString()}`,
    );
    return {
      notifications: data.notifications ?? [],
      cursor: data.cursor ?? null,
      hasMore: data.hasMore ?? false,
      unreadCount: data.unreadCount ?? 0,
    };
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

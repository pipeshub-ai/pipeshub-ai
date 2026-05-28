import { apiClient } from '@/lib/api';

export type NotificationSeverity = 'info' | 'warning' | 'error' | 'critical';

export type NotificationStatus = 'read' | 'unread' | 'archived';

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

export type NotificationListFilter = 'all' | 'unread';

export interface NotificationListParams {
  limit?: number;
  cursor?: string;
  /** When `'unread'`, only unread notifications are returned. Omit for all. */
  status?: 'unread';
}

export const DEFAULT_NOTIFICATION_PAGE_SIZE = 20;

export const NotificationsApi = {
  async list(params: NotificationListParams = {}): Promise<NotificationListResponse> {
    const limit = params.limit ?? DEFAULT_NOTIFICATION_PAGE_SIZE;
    const searchParams = new URLSearchParams({ limit: String(limit) });
    if (params.cursor) {
      searchParams.set('cursor', params.cursor);
    }
    if (params.status === 'unread') {
      searchParams.set('status', 'unread');
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

  async markAllRead(): Promise<{ success: boolean; modifiedCount: number }> {
    const { data } = await apiClient.patch<{ success: boolean; modifiedCount: number }>(
      '/api/v1/notifications/read-all',
    );
    return {
      success: data.success ?? true,
      modifiedCount: data.modifiedCount ?? 0,
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

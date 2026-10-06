import { apiClient } from '@/lib/api';

export type NotificationSeverity = 'info' | 'warning' | 'error' | 'critical' | 'success';

export type NotificationStatus = 'read' | 'unread' | 'archived';

export type NotificationOriginService =
  | 'Connector Service'
  | 'Indexing Service'
  | 'AI Service'
  | 'External Service';

/** Matches the Mongo/API notification document shape (.lean() / websocket). */
export interface NotificationListItem {
  _id: string;
  orgId?: string;
  type: string;
  title?: string;
  message?: string;
  redirectLink?: string;
  severity?: NotificationSeverity;
  status: NotificationStatus;
  originService?: NotificationOriginService;
  assignedTo?: string;
  payload?: Record<string, unknown>;
  /** Resolved by the list endpoint after a read check; absent on websocket rows and when unresolved. */
  context?: { chatTitle?: string; actorName?: string };
  isDeleted?: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface NotificationListResponse {
  notifications: NotificationListItem[];
  cursor: string | null;
  hasMore: boolean;
}

export interface NotificationStatsResponse {
  unreadCount: number;
  readCount: number;
  archivedCount: number;
}

export const TIP_IDS = [
  'mentions.firstSharedSend',
  'mentions.firstNote',
  'mentions.firstAgentMention',
  'mentions.popoverIntro',
] as const;
export type TipId = (typeof TIP_IDS)[number];

export interface NotificationPreferences {
  email: { chatShared: boolean; ownershipTransferred: boolean; chatMentioned?: boolean };
  inApp: { chatActivity: boolean; chatMentioned?: boolean };
  mutedSessions: string[];
  tipsSeen?: string[];
}

export interface NotificationPreferencesPatch {
  email?: Partial<NotificationPreferences['email']>;
  inApp?: Partial<NotificationPreferences['inApp']>;
}

export type NotificationListFilter = 'all' | 'unread' | 'archived';

export interface NotificationListParams {
  limit?: number;
  cursor?: string;
  /** When `'unread'`, only unread notifications are returned. When `'archived'`, only archived. Omit for all. */
  status?: 'unread' | 'archived';
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
    } else if (params.status === 'archived') {
      searchParams.set('status', 'archived');
    }
    const { data } = await apiClient.get<NotificationListResponse>(
      `/api/v1/notifications?${searchParams.toString()}`,
    );
    return {
      notifications: data.notifications ?? [],
      cursor: data.cursor ?? null,
      hasMore: data.hasMore ?? false,
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

  async markUnread(id: string): Promise<NotificationListItem> {
    const { data } = await apiClient.patch<{ notification: NotificationListItem }>(
      `/api/v1/notifications/${encodeURIComponent(id)}/unread`,
    );
    return data.notification;
  },

  async archive(id: string): Promise<NotificationListItem> {
    const { data } = await apiClient.patch<{ notification: NotificationListItem }>(
      `/api/v1/notifications/${encodeURIComponent(id)}/archive`,
    );
    return data.notification;
  },

  async unarchive(id: string): Promise<NotificationListItem> {
    const { data } = await apiClient.patch<{ notification: NotificationListItem }>(
      `/api/v1/notifications/${encodeURIComponent(id)}/unarchive`,
    );
    return data.notification;
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/api/v1/notifications/${encodeURIComponent(id)}`);
  },

  async getStats(): Promise<NotificationStatsResponse> {
    const { data } = await apiClient.get<NotificationStatsResponse>(
      '/api/v1/notifications/stats',
    );
    return {
      unreadCount: data.unreadCount ?? 0,
      readCount: data.readCount ?? 0,
      archivedCount: data.archivedCount ?? 0,
    };
  },

  async getPreferences(options: { quiet?: boolean } = {}): Promise<NotificationPreferences> {
    const { data } = await apiClient.get<NotificationPreferences>(
      '/api/v1/notifications/preferences',
      options.quiet ? { suppressErrorToast: true } : undefined,
    );
    return data;
  },

  async updatePreferences(patch: NotificationPreferencesPatch): Promise<NotificationPreferences> {
    const { data } = await apiClient.patch<NotificationPreferences>(
      '/api/v1/notifications/preferences',
      patch,
    );
    return data;
  },

  async markTipSeen(tipId: TipId): Promise<NotificationPreferences> {
    const { data } = await apiClient.patch<NotificationPreferences>(
      '/api/v1/notifications/preferences/tips',
      { tipId },
    );
    return data;
  },

  async muteSession(sessionId: string): Promise<NotificationPreferences> {
    const { data } = await apiClient.put<NotificationPreferences>(
      `/api/v1/notifications/preferences/muted-sessions/${encodeURIComponent(sessionId)}`,
    );
    return data;
  },

  async unmuteSession(sessionId: string): Promise<NotificationPreferences> {
    const { data } = await apiClient.delete<NotificationPreferences>(
      `/api/v1/notifications/preferences/muted-sessions/${encodeURIComponent(sessionId)}`,
    );
    return data;
  },
};

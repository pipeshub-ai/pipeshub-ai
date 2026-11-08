/**
 * Notification repository: per-user notifications and their read state.
 *
 * Rows leave as plain objects with every id as a string. A notification older
 * than the retention window, or deleted, is never returned or changed: the
 * window and the delete flag are part of every method here, not of the caller's
 * filter.
 */
export type NotificationStatus = 'read' | 'unread' | 'archived';

export interface NotificationRow {
  _id: string;
  orgId: string;
  assignedTo: string;
  type: string;
  title?: string;
  message?: string;
  redirectLink?: string;
  severity?: string;
  originService?: string;
  status: NotificationStatus;
  payload?: Record<string, unknown>;
  isDeleted: boolean;
  deletedBy?: string;
  createdAt: Date;
  updatedAt?: Date;
}

/** Whose notifications a call reads or changes. */
export interface NotificationScope {
  orgId: string;
  userId: string;
}

/** A notification to store; ids are strings. */
export type NewNotification = Record<string, unknown> & {
  orgId: string;
  assignedTo: string;
  type: string;
};

export interface NotificationCursor {
  createdAt: Date;
  id: string;
}

export interface NotificationRepository {
  insert(row: NewNotification): Promise<NotificationRow>;
  insertMany(rows: NewNotification[]): Promise<NotificationRow[]>;
  /**
   * Newest first, keyset on (createdAt, _id). `status: null` lists everything
   * but archived. Returns up to `limit + 1` rows so the caller can tell whether
   * another page exists.
   */
  list(
    scope: NotificationScope,
    query: {
      status: string | null;
      cursor?: NotificationCursor;
      limit: number;
    },
  ): Promise<NotificationRow[]>;
  /** The badge: counts by status, archived excluded from none of them. */
  counts(
    scope: NotificationScope,
  ): Promise<{ unread: number; read: number; archived: number }>;
  /** Marks every unread notification read; returns how many changed. */
  markAllRead(scope: NotificationScope): Promise<number>;
  /** Not for an archived one (see `unarchive`). Null when none matches. */
  setStatus(
    scope: NotificationScope,
    id: string,
    status: 'read' | 'unread' | 'archived',
  ): Promise<NotificationRow | null>;
  /** Archived to read. Null when none matches. */
  unarchive(
    scope: NotificationScope,
    id: string,
  ): Promise<NotificationRow | null>;
  /** Soft delete, archived ones included. Null when none matches. */
  softDelete(
    scope: NotificationScope,
    id: string,
  ): Promise<NotificationRow | null>;
}

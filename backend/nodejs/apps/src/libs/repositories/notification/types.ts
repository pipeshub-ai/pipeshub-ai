/**
 * Notification repository: per-user notifications and their read state.
 *
 * Rows leave as plain objects with every id as a string. A notification older
 * than the retention window, or deleted, is never returned or changed: the
 * window and the delete flag are part of every method here, not of the caller's
 * filter. So is `visibleAt`: a notification created with a `visibleAt` still in
 * the future is neither listed, counted, marked read nor found by id until then.
 */
export type NotificationStatus = 'read' | 'unread' | 'archived';

export type NotificationSeverity =
  'info' | 'warning' | 'error' | 'critical' | 'success';

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
  /** When set, the notification is not visible before this instant. */
  visibleAt?: Date;
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
  /**
   * One row per input, in order. A row that names its own `_id` (a 24-hex id) is stored under that id, and is IDEMPOTENT: when a notification of the org already has that id,
   * nothing is written and the existing row is returned. A row without `_id` gets a new one.
   */
  insertMany(rows: NewNotification[]): Promise<NotificationRow[]>;
  /**
   * Whether the notification with this id exists for this user in this org. Ungated: neither the retention window, the delete flag nor `visibleAt` hides it (a read-back asks
   * "was it stored", not "is it in the inbox"). An id or user that is not a valid id, or another org's or user's notification, is false.
   */
  exists(scope: NotificationScope, id: string): Promise<boolean>;
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
  /**
   * Erasure of a conversation (CLEANUP-3): removes outright every notification of the org that links to it
   * (`payload.conversationId`), whoever it was for, deleted or past retention. Returns how many went.
   */
  eraseForConversation(orgId: string, conversationId: string): Promise<number>;
  /** An org's erasure (CLEANUP-G1): removes up to `limit` of the org's notifications outright; how many went. */
  eraseForOrg(orgId: string, limit: number): Promise<number>;
}

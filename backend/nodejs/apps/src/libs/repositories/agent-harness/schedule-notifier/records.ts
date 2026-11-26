import type { NotificationSeverity } from '../../notification/types';

/** Who the actor is for a mail or a Slack message: their address and name; null when they have no address. */
export interface NoticePerson {
  email: string;
  name: string;
}

/** One inbox notification of a schedule's notice, as the notifier writes it. */
export interface NoticeRow {
  type: string;
  severity: NotificationSeverity;
  title: string;
  message: string;
  redirectLink: string;
  payload: Record<string, unknown>;
}

/**
 * Main's records a schedule notice reads and writes (TR-07, AP-08), per document store: the run's saved answer in its
 * conversation, the person's address and name, and the actor's inbox notification.
 */
export interface ScheduleNoticeRecords {
  /** The newest answer of the conversation; null when there is none. */
  answer(orgId: string, sessionId: string): Promise<string | null>;
  /** A live person's address and name; null when they have no address. */
  person(orgId: string, userId: string): Promise<NoticePerson | null>;
  /** Stores the notification unread in the actor's inbox: the stored row, as the inbox pushes it. */
  saveNotice(
    orgId: string,
    actorId: string,
    row: NoticeRow,
  ): Promise<object | null>;
}

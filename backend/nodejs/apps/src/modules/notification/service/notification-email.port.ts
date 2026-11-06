import mongoose from 'mongoose';

/**
 * Broker-only hint on a notification event: what an email about it needs. The
 * consumer hands it to the email dispatcher as is.
 */
export interface EmailIntent {
  readonly template: string;
}

export interface EmailDispatchInput {
  orgId: string | mongoose.Types.ObjectId;
  assignedTo: string | mongoose.Types.ObjectId;
  dedupeKey?: string;
  /** App-relative path, e.g. `/chat?conversationId=…`. */
  redirectLink?: string;
  emailIntent: EmailIntent;
  /** The inserted notification. */
  notification?: { id: string; type: string; sessionId?: string };
}

export interface INotificationEmailDispatcher {
  /** Never throws. Call only for a newly inserted notification (E11000 means it was already delivered). */
  dispatch(input: EmailDispatchInput): Promise<void>;
}

export const NOTIFICATION_EMAIL_DISPATCHER = Symbol.for(
  'INotificationEmailDispatcher',
);

/** Notifications send no email. */
export const noNotificationEmails: INotificationEmailDispatcher = {
  dispatch: () => Promise.resolve(),
};

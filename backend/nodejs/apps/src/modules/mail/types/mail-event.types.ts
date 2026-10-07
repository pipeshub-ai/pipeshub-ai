import { MailBody } from '../middlewares/types';

export enum MailEventType {
  SendMailEvent = 'sendMail',
}

/**
 * Job published to {@link BrokerTopic.MAIL_EVENTS}. `orgId` is optional — the
 * pre-login OTP flow has no org — and failure notifications need it, so jobs
 * without one are logged instead.
 */
export interface MailEventPayload {
  mail: MailBody;
  orgId?: string;
  /**
   * Who to mint a set-password link for, as `templateData.link`. Minted by the
   * consumer at send time so no live credential waits in the broker for the
   * topic's retention, and the token's TTL starts at delivery, not enqueue.
   */
  passwordResetLinkFor?: PasswordResetLinkTarget;
}

export interface PasswordResetLinkTarget {
  userId: string;
  orgId: string;
  email: string;
}

export interface MailEvent {
  eventType: MailEventType;
  timestamp: number;
  payload: MailEventPayload;
}

/** Outcome of one delivery attempt, so callers can decide whether to retry. */
export type MailSendResult =
  | { status: 'sent' }
  | { status: 'transient'; error: string }
  | { status: 'permanent'; error: string }
  // Deadline fired while SMTP may still be in flight — retrying risks a duplicate.
  | { status: 'indeterminate'; error: string }
  // Not attempted: the SMTP server kept failing to connect and is cooling down.
  | { status: 'unavailable'; error: string };

export const SMTP_DEADLINE_ERROR_CODE = 'ESMTPDEADLINE';

/** Upper bound on retries for one mail job, so the broker never sees a stuck handler. */
export const MAIL_MESSAGE_BUDGET_MS = 90_000;

// Headroom over the budget for the failure notification publish after it.
export const MAIL_CONSUMER_LIVENESS_MS = MAIL_MESSAGE_BUDGET_MS * 2;

// Per RFC 5321: 4xx means try again later, 5xx is an outright rejection.
const PERMANENT_SMTP_RANGE = { min: 500, max: 599 };

/** Failures that say the server is unreachable, as opposed to rejecting one message. */
export const SMTP_CONNECTION_ERROR_CODES = new Set([
  'ECONNECTION',
  'ETIMEDOUT',
  'ESOCKET',
  'EDNS',
  'ECONNREFUSED',
  'ECONNRESET',
  'EHOSTUNREACH',
  'ENETUNREACH',
  'ENOTFOUND',
  'EAI_AGAIN',
  SMTP_DEADLINE_ERROR_CODE,
]);

const PERMANENT_ERROR_CODES = new Set([
  'EAUTH',
  'EENVELOPE',
  'EMESSAGE',
]);

/**
 * An error with no usable signal counts as transient: dropping a real email is
 * worse than one redundant attempt.
 */
export function classifyMailError(error: unknown): 'transient' | 'permanent' {
  const err = error as { responseCode?: number; code?: string } | null;
  if (!err) {
    return 'transient';
  }

  const responseCode = Number(err.responseCode);
  if (Number.isFinite(responseCode)) {
    if (
      responseCode >= PERMANENT_SMTP_RANGE.min &&
      responseCode <= PERMANENT_SMTP_RANGE.max
    ) {
      return 'permanent';
    }
    return 'transient';
  }

  if (typeof err.code === 'string') {
    return PERMANENT_ERROR_CODES.has(err.code) ? 'permanent' : 'transient';
  }

  return 'transient';
}

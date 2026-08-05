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
  | { status: 'permanent'; error: string };

// Per RFC 5321: 4xx means try again later, 5xx is an outright rejection.
const PERMANENT_SMTP_RANGE = { min: 500, max: 599 };

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

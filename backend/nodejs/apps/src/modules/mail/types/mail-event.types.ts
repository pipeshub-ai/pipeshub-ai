import { MailBody } from '../middlewares/types';

export enum MailEventType {
  SendMailEvent = 'sendMail',
}

/**
 * Job published to {@link BrokerTopic.MAIL_EVENTS} and consumed by MailConsumer.
 *
 * `orgId` is optional because not every caller has an org in scope (e.g. the
 * pre-login OTP flow); admin failure notifications are only published when it
 * is present, since the notification pipeline keys delivery off the org.
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

// 4xx are "try again later" per RFC 5321; 5xx mean the server rejected the
// message outright and replaying it would fail identically.
const PERMANENT_SMTP_RANGE = { min: 500, max: 599 };

const TRANSIENT_ERROR_CODES = new Set([
  'ECONNECTION',
  'ETIMEDOUT',
  'ESOCKET',
  'ECONNRESET',
  'EDNS',
  'EAI_AGAIN',
  'ECONNREFUSED',
  'EHOSTUNREACH',
  'ENETUNREACH',
  'ETLS',
]);

/**
 * Classifies a nodemailer failure so the consumer knows whether replaying is
 * worthwhile. Network-shaped codes and SMTP 4xx are transient; SMTP 5xx and
 * nodemailer's rejection codes (EAUTH, EMESSAGE) are permanent. When the error
 * carries no usable signal at all it is treated as transient, since dropping a
 * real email is worse than one redundant attempt.
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
    return TRANSIENT_ERROR_CODES.has(err.code) ? 'transient' : 'permanent';
  }

  return 'transient';
}

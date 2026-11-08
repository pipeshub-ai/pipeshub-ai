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
  | { status: 'transient'; error: string; problem?: SmtpProblem }
  | { status: 'permanent'; error: string; problem?: SmtpProblem }
  // Deadline fired while SMTP may still be in flight — retrying risks a duplicate.
  | { status: 'indeterminate'; error: string }
  // Not attempted: the SMTP server kept failing to connect and is cooling down.
  | { status: 'unavailable'; error: string; problem: 'unreachable' };

/** An SMTP setup fault an admin can fix, as opposed to one message being refused. */
export type SmtpProblem = 'auth' | 'unreachable';

/**
 * What the sender is told when the SMTP setup is at fault. Listed in
 * libs/errors/reader-friendly, so callers of the mail route may repeat them.
 */
export const SMTP_PROBLEM_MESSAGES: Record<SmtpProblem, string> = {
  auth: "The email wasn't sent because the mail (SMTP) server rejected the username or password. An admin can update them in Workspace settings > Mail.",
  unreachable:
    "The email wasn't sent because PipesHub couldn't reach the mail (SMTP) server. An admin can check the host and port in Workspace settings > Mail.",
};

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

// Failures that prove the message never left: the server was never reached, or
// it never got as far as greeting us. A dropped socket or a timeout later on may
// come after the server accepted the message, so those get no label.
const SMTP_NEVER_CONNECTED_CODES = new Set([
  'EDNS',
  'ECONNREFUSED',
  'ENOTFOUND',
  'EHOSTUNREACH',
  'ENETUNREACH',
  'EAI_AGAIN',
]);
// Nodemailer's `timeoutType` for the stages before any mail command is sent.
const SMTP_SETUP_TIMEOUTS = new Set([
  'CONNECT_TIMEOUT',
  'GREETING_TIMEOUT',
  'UPGRADE_TIMEOUT',
]);

export function smtpProblemOf(error: unknown): SmtpProblem | undefined {
  const err = error as {
    responseCode?: number;
    code?: string;
    originalCode?: string;
    timeoutType?: string;
  } | null;
  // 535 is "authentication credentials invalid" (RFC 4954).
  if (err?.code === 'EAUTH' || Number(err?.responseCode) === 535) {
    return 'auth';
  }
  // Nodemailer wraps the system error (ECONNREFUSED) as ESOCKET and keeps it in originalCode.
  if (
    SMTP_NEVER_CONNECTED_CODES.has(err?.code ?? '') ||
    SMTP_NEVER_CONNECTED_CODES.has(err?.originalCode ?? '') ||
    SMTP_SETUP_TIMEOUTS.has(err?.timeoutType ?? '')
  ) {
    return 'unreachable';
  }
  return undefined;
}

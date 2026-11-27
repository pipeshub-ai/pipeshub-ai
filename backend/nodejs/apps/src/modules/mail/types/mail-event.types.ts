import type { ErrorClass } from '../../../libs/utils/error-class';
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
  /**
   * Send only if SMTP is configured: without it the job is dropped with an
   * info log instead of a failure notification. For mail that accompanies an
   * in-app notification, so a workspace without mail set up still gets that
   * notification and not an "Email delivery failed" one on top.
   */
  ifSmtpConfigured?: boolean;
  /**
   * The job's event id; a producer stamps a random one when unset. A job that may be published again for the same mail gives a
   * stable one, so the mail consumer's inbox sends it once.
   */
  eventId?: string;
}

/** What the mail topic carries: the job itself (unsigned), or the job under `payload` (the shape a signed envelope needs). */
export type MailEventMessage =
  MailEventPayload | { eventId?: string; payload: MailEventPayload };

/**
 * The job in a consumed mail message. The signed shape (the job under `payload`) wins whenever `payload` is an object:
 * a signature covers only `payload`, so top-level fields of a signed message are never read. A flat job (the unsigned
 * shape) is read only when the message was not signed (`signed` false); a signed message carrying one beside or
 * instead of its payload is refused (undefined).
 */
export function mailJobOf(
  value: unknown,
  signed = false,
): MailEventPayload | undefined {
  if (value === null || typeof value !== 'object') return undefined;
  const v = value as Record<string, unknown>;
  const inner = v.payload;
  if (inner !== null && typeof inner === 'object' && !Array.isArray(inner)) {
    return 'mail' in inner ? (inner as MailEventPayload) : undefined;
  }
  if (signed) return undefined;
  return 'mail' in v ? (v as unknown as MailEventPayload) : undefined;
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
/**
 * Every failure carries `errorClass` (docs/logging.md "Failures") and, when the server answered, its reply code. `error` is the
 * failure's text for the admin notification only: SMTP replies quote the recipient, so it is never logged.
 */
interface MailFailure {
  error: string;
  errorClass: ErrorClass;
  smtpCode?: number;
}

export type MailSendResult =
  | { status: 'sent' }
  | ({ status: 'transient'; problem?: SmtpProblem } & MailFailure)
  | ({ status: 'permanent'; problem?: SmtpProblem } & MailFailure)
  // Deadline fired while SMTP may still be in flight — retrying risks a duplicate.
  | ({ status: 'indeterminate' } & MailFailure)
  // Not attempted: the SMTP server kept failing to connect and is cooling down.
  | ({ status: 'unavailable'; problem: 'unreachable' } & MailFailure);

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

const PERMANENT_ERROR_CODES = new Set(['EAUTH', 'EENVELOPE', 'EMESSAGE']);

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

// A certificate the SMTP server presented that we cannot trust: our host or TLS setting is wrong.
const TLS_CERTIFICATE_CODES = new Set([
  'CERT_HAS_EXPIRED',
  'DEPTH_ZERO_SELF_SIGNED_CERT',
  'SELF_SIGNED_CERT_IN_CHAIN',
  'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
  'ERR_TLS_CERT_ALTNAME_INVALID',
]);
const SMTP_TIMEOUT_CODES = new Set(['ETIMEDOUT', SMTP_DEADLINE_ERROR_CODE]);

/** The class of a reply the server sent; the command it refused says whose fault a 5xx is. */
function replyClass(
  code: number,
  err: { code?: string; command?: string },
): ErrorClass {
  // RFC 5321: 4xx is temporary whatever the command (greylisting, a busy server, a 454 on AUTH).
  if (code < 500) return 'unavailable';
  // Our login (535 rejected, 530 required) or our sender / relay (553 sender rejected, 550 relaying denied): every mail fails.
  if (
    code === 530 ||
    code === 535 ||
    err.code === 'EAUTH' ||
    err.command === 'MAIL FROM'
  ) {
    return 'misconfigured';
  }
  // The server refused this recipient or this message.
  if (err.command === 'RCPT TO' || err.command === 'DATA') return 'bad_request';
  return 'upstream_5xx';
}

/**
 * The error class of a failed send (docs/logging.md "Failures"). The reply code decides whenever there is one, as
 * {@link classifyMailError} does; nodemailer's own code only when the server never answered. Never throws.
 */
export function mailErrorClass(error: unknown): ErrorClass {
  if (typeof error === 'string') return 'internal';
  const err = error as {
    responseCode?: number;
    code?: string;
    command?: string;
    originalCode?: string;
    timeoutType?: string;
    message?: string;
  } | null;
  if (!err || typeof err !== 'object') return 'internal';
  const responseCode = Number(err.responseCode);
  if (
    Number.isInteger(responseCode) &&
    responseCode >= 400 &&
    responseCode < 600
  ) {
    return replyClass(responseCode, err);
  }
  const code = err.code ?? '';
  // A failed TLS upgrade (nodemailer builds ETLS without the socket's code): our host or TLS setting.
  if (code === 'EAUTH' || code === 'ETLS') return 'misconfigured';
  if (code === 'EENVELOPE' && err.command === 'API') {
    // Nodemailer's own checks before sending: our fromEmail, or the caller's recipients.
    return (err.message ?? '').startsWith('Invalid sender')
      ? 'misconfigured'
      : 'bad_request';
  }
  // The message is larger than the server allows: the caller's attachments.
  if (
    code === 'EMESSAGE' &&
    (err.message ?? '').startsWith('Message size larger than allowed')
  ) {
    return 'bad_request';
  }
  if (
    TLS_CERTIFICATE_CODES.has(err.originalCode ?? '') ||
    TLS_CERTIFICATE_CODES.has(code)
  ) {
    return 'misconfigured';
  }
  if (
    SMTP_TIMEOUT_CODES.has(code) ||
    SMTP_SETUP_TIMEOUTS.has(err.timeoutType ?? '')
  )
    return 'timeout';
  if (
    SMTP_CONNECTION_ERROR_CODES.has(code) ||
    SMTP_NEVER_CONNECTED_CODES.has(err.originalCode ?? '')
  ) {
    return 'unavailable';
  }
  if (code === 'EPROTOCOL') return 'upstream_5xx';
  return 'internal';
}

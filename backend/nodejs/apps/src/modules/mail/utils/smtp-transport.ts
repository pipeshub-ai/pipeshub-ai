import nodemailer from 'nodemailer';
import type SMTPTransport from 'nodemailer/lib/smtp-transport';
import { SmtpConfig } from '../middlewares/types';
import { SmtpProblem, smtpProblemOf } from '../types/mail-event.types';

const SMTP_DNS_TIMEOUT_MS = 30_000;
const SMTP_CONNECTION_TIMEOUT_MS = 30_000;
const SMTP_GREETING_TIMEOUT_MS = 30_000;
const SMTP_SOCKET_TIMEOUT_MS = 60_000;
// An admin is waiting on the settings form; a server this slow fails sends too.
const SMTP_CHECK_TIMEOUT_MS = 10_000;

/** How PipesHub connects to an SMTP server, for sending and for checking settings alike. */
export function smtpTransportOptions(
  smtpConfig: SmtpConfig,
): SMTPTransport.Options {
  const port = smtpConfig.port || 587;
  return {
    host: smtpConfig.host,
    port,
    // 465 is TLS from the first byte; the others upgrade with STARTTLS when offered.
    secure: port === 465,
    dnsTimeout: SMTP_DNS_TIMEOUT_MS,
    connectionTimeout: SMTP_CONNECTION_TIMEOUT_MS,
    greetingTimeout: SMTP_GREETING_TIMEOUT_MS,
    socketTimeout: SMTP_SOCKET_TIMEOUT_MS,
    // Attachments come from the request body; never let one read a local file or fetch a URL.
    disableFileAccess: true,
    disableUrlAccess: true,
    ...(!smtpConfig.username
      ? {}
      : smtpConfig.password
        ? { auth: { user: smtpConfig.username, pass: smtpConfig.password } }
        : { auth: { user: smtpConfig.username } }),
  };
}

/** A settings problem an admin can fix before saving, or `rejected` for any other refusal. */
export type SmtpSettingsProblem = SmtpProblem | 'rejected';

/** Fixed text for the settings form; the server's own words go to the log only. */
export const SMTP_SETTINGS_PROBLEM_MESSAGES: Record<
  SmtpSettingsProblem,
  string
> = {
  auth: 'The mail (SMTP) server rejected this username or password. Check them, then save again.',
  unreachable:
    "PipesHub couldn't reach the mail (SMTP) server at this host and port. Check them and that PipesHub can connect to the server, then save again.",
  rejected:
    "The mail (SMTP) server didn't accept a connection with these settings. Check the host, port, username and password, then save again.",
};

/**
 * Connects and signs in once with *smtpConfig*, as sending would. Resolves to
 * null when the server accepts the settings, else to what is wrong with them.
 */
export async function smtpSettingsProblem(
  smtpConfig: SmtpConfig,
  onError?: (error: unknown) => void,
): Promise<SmtpSettingsProblem | null> {
  const transporter = nodemailer.createTransport({
    ...smtpTransportOptions(smtpConfig),
    dnsTimeout: SMTP_CHECK_TIMEOUT_MS,
    connectionTimeout: SMTP_CHECK_TIMEOUT_MS,
    greetingTimeout: SMTP_CHECK_TIMEOUT_MS,
    socketTimeout: SMTP_CHECK_TIMEOUT_MS,
  });
  try {
    await transporter.verify();
    return null;
  } catch (error) {
    onError?.(error);
    return smtpProblemOf(error) ?? 'rejected';
  } finally {
    transporter.close();
  }
}

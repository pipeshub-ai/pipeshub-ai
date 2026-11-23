import { upgradeGraceEndsAt } from './license-notice';
import { licenseStatus, type LicenseStore } from './license.service';

/** Days before the upgrade grace ends at which the install's admins are mailed, once each. */
export const GRACE_REMINDER_DAYS = [14, 7, 3, 1] as const;

/** One key per reminder, `<prefix>/<days>`, holding `{ endsAt, sentAt }`; created once, by whichever replica gets there first. */
export const GRACE_REMINDERS_KEY = '/services/licence/graceReminders';

const DAY_MS = 24 * 60 * 60 * 1000;

/** How a reminder send ended: `retry` gives the claim back, so a later check sends it. */
export type ReminderOutcome = 'sent' | 'retry' | 'failed';

/**
 * What a mail send's result means for the reminder's claim. Only a failure that may pass (`transient`,
 * `unavailable`) gives the claim back for the next check. `indeterminate` may have gone out, and `permanent`
 * (refused login, rejected address, bad template) fails the same way every time: retrying it every check could
 * lock the SMTP account. Both keep the claim.
 */
export const reminderOutcome = (status: string): ReminderOutcome =>
  status === 'sent'
    ? 'sent'
    : status === 'transient' || status === 'unavailable'
      ? 'retry'
      : 'failed';

export interface GraceReminderDeps {
  store: LicenseStore;
  /** Creates `key` with `value` only if it does not exist yet; false when it does. Atomic across replicas. */
  claim(key: string, value: unknown): Promise<boolean>;
  /** Removes a claim whose send failed in a way worth retrying. */
  release(key: string): Promise<void>;
  /** The install's organization and its admins' addresses; null when there is no organization yet. */
  admins(): Promise<{ orgId: string; emails: string[] } | null>;
  /** False when mail is not set up on this install. */
  mailConfigured(): Promise<boolean>;
  send(mail: {
    orgId: string;
    to: string[];
    daysLeft: number;
    endsAt: string;
  }): Promise<ReminderOutcome>;
  log: {
    info(message: string, meta?: Record<string, unknown>): void;
    warn(message: string, meta?: Record<string, unknown>): void;
  };
}

/** Whole days, rounded up, until the grace ends. */
export const graceDaysLeft = (endsAt: string, now: Date): number =>
  Math.ceil((Date.parse(endsAt) - now.getTime()) / DAY_MS);

/**
 * The reminder due now: the nearest threshold reached. A threshold passed while
 * the install was down is not sent late once a nearer one is due.
 */
export function dueReminder(daysLeft: number): number | null {
  const reached = GRACE_REMINDER_DAYS.filter((days) => daysLeft <= days);
  return reached.length > 0 ? Math.min(...reached) : null;
}

// Per process: says once per reminder that mail is not set up, not at every check.
const toldNoMail = new Set<string>();

/**
 * Mails the install's admins the reminder due now, while the upgrade grace runs
 * with no licence key in force. Each reminder is claimed in the store before it
 * is sent, so restarts and other replicas never send it again; a send that can
 * be retried gives the claim back. Returns the reminder sent, or null.
 */
export async function sendGraceReminder(
  deps: GraceReminderDeps,
  now: Date = new Date(),
): Promise<number | null> {
  const endsAt = upgradeGraceEndsAt(await licenseStatus(deps.store, now), now);
  if (endsAt === null) return null;
  const daysLeft = graceDaysLeft(endsAt, now);
  const days = dueReminder(daysLeft);
  if (days === null) return null;
  const key = `${GRACE_REMINDERS_KEY}/${String(days)}`;
  if ((await deps.store.read(key)) !== null) return null;

  if (!(await deps.mailConfigured())) {
    if (!toldNoMail.has(key)) {
      toldNoMail.add(key);
      deps.log.info(
        'Mail is not set up, so admins are not mailed the upgrade grace reminder',
        { days, endsAt },
      );
    }
    return null;
  }
  const admins = await deps.admins();
  if (admins === null || admins.emails.length === 0) return null;

  if (!(await deps.claim(key, { endsAt, sentAt: now.toISOString() }))) {
    return null;
  }
  let outcome: Awaited<ReturnType<GraceReminderDeps['send']>>;
  try {
    outcome = await deps.send({
      orgId: admins.orgId,
      to: admins.emails,
      daysLeft,
      endsAt,
    });
  } catch (error) {
    // Nothing was sent (e.g. the stored mail settings could not be read): give the claim back.
    await deps.release(key);
    throw error;
  }
  if (outcome === 'sent') return days;
  if (outcome === 'retry') await deps.release(key);
  deps.log.warn('Could not mail the upgrade grace reminder', {
    days,
    outcome,
  });
  return null;
}

/** Tests: forget which missing-mail notes this process has logged. */
export const resetGraceReminderLog = (): void => {
  toldNoMail.clear();
};

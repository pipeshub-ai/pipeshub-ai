// Copyright (c) 2026 PipesHub Inc.
// Licensed under the PipesHub Enterprise License. See the LICENSE file in this
// ee folder. Free for development, testing and evaluation. Production use needs
// a PipesHub licence key.

import {
  CLOCK_ROLLBACK_TOLERANCE_MS,
  EXPIRY_WARNING_MS,
  ISSUED_AT_LEEWAY_MS,
} from './license-policy';
import type {
  LicenseEvaluation,
  LicenseStatus,
  VerifiedLicense,
} from './license.types';

/** Where `now` falls in a license that has started: before the warning window, in it, in grace, or past it. */
export function windowStatus(
  endDate: Date,
  graceEndsAt: Date,
  now: Date,
): Extract<LicenseStatus, 'active' | 'expiring' | 'grace' | 'expired'> {
  const at = now.getTime();
  if (at >= graceEndsAt.getTime()) return 'expired';
  if (at >= endDate.getTime()) return 'grace';
  if (at >= endDate.getTime() - EXPIRY_WARNING_MS) return 'expiring';
  return 'active';
}

/** What the latest license check saved: the license time it ran at, and what the server clock read then. */
export interface LicenseClockMark {
  at?: Date | null;
  clock?: Date | null;
}

/**
 * The time every license decision is made at. Normally the server clock; once
 * the clock reads more than the tolerance behind the last check, it counts on
 * from that check by the time that has passed since, so setting the clock back
 * gains nothing. `runningMs` is that time as measured by the process's own
 * running clock, which setting the system clock does not move; without it a
 * clock pushed back again before each check would hold license time still.
 * Never earlier than the newest license PipesHub signed, less the clock skew an
 * upload is allowed.
 */
export function licenseTime(
  clock: Date,
  mark: LicenseClockMark | null,
  licenses: readonly VerifiedLicense[],
  runningMs: number | null = null,
): Date {
  const now = clock.getTime();
  const last = mark?.at?.getTime();
  let at = now;
  if (last !== undefined) {
    const measured = Math.max(0, runningMs ?? 0);
    // Within the tolerance (time sync, servers a little apart) the clock is trusted, but
    // never behind what ran since this process's own last check: a clock set back by less
    // than the tolerance before every check would otherwise hold license time still.
    at =
      last - now <= CLOCK_ROLLBACK_TOLERANCE_MS
        ? Math.max(now, last + measured)
        : last + Math.max(0, now - (mark?.clock?.getTime() ?? now), measured);
  }
  const signed = licenses.map(
    (license) => license.issuedAt.getTime() - ISSUED_AT_LEEWAY_MS,
  );
  return new Date(Math.max(at, ...signed));
}

// Ties on issuedAt are broken by code units, not localeCompare: the process locale must not change which
// license is in force.
const byIssueDesc = (a: VerifiedLicense, b: VerifiedLicense): number =>
  b.issuedAt.getTime() - a.issuedAt.getTime() ||
  (a.licenseId < b.licenseId ? 1 : a.licenseId > b.licenseId ? -1 : 0);

/**
 * The license in force at `now`: of those whose window (start to end of grace)
 * contains `now`, one still before its end date beats one in its grace period,
 * then the most recently issued wins. A later license therefore replaces an
 * earlier one (more or fewer seats, a downgrade) while both run, a renewal
 * takes over on its start date even from a license in grace that was issued
 * after it, and a short-lived license gives way to one that is still running
 * when it ends.
 */
export function evaluateLicenses(
  licenses: readonly VerifiedLicense[],
  now: Date,
): LicenseEvaluation {
  const at = now.getTime();
  const running = (l: VerifiedLicense): number =>
    at < l.endDate.getTime() ? 1 : 0;
  const inWindow = licenses
    .filter((l) => l.startDate.getTime() <= at && at < l.graceEndsAt.getTime())
    .sort((a, b) => {
      const byRunning = running(b) - running(a);
      return byRunning !== 0 ? byRunning : byIssueDesc(a, b);
    });
  const upcoming =
    licenses
      .filter((l) => l.startDate.getTime() > at)
      .sort(
        (a, b) =>
          a.startDate.getTime() - b.startDate.getTime() || byIssueDesc(a, b),
      )[0] ?? null;
  const effective = inWindow[0] ?? null;
  if (effective) {
    return {
      status: windowStatus(effective.endDate, effective.graceEndsAt, now),
      effective,
      upcoming,
      latest: null,
    };
  }
  const ended =
    licenses
      .filter((l) => l.graceEndsAt.getTime() <= at)
      .sort((a, b) => b.graceEndsAt.getTime() - a.graceEndsAt.getTime())[0] ??
    null;
  if (ended)
    return { status: 'expired', effective: null, upcoming, latest: ended };
  return {
    status: upcoming ? 'pending' : 'none',
    effective: null,
    upcoming,
    latest: null,
  };
}

/**
 * Whether `candidate` would never be the license in force from `now` on: newer
 * licenses win every moment of its term. The license in force changes only on
 * a start, end or grace end, so checking those instants covers the whole term.
 */
export function neverInForce(
  candidate: VerifiedLicense,
  others: readonly VerifiedLicense[],
  now: Date,
): boolean {
  const all = [...others, candidate];
  const from = Math.max(now.getTime(), candidate.startDate.getTime());
  const until = candidate.graceEndsAt.getTime();
  const instants = all
    .flatMap((l) => [l.startDate, l.endDate, l.graceEndsAt])
    .map((date) => date.getTime())
    .filter((time) => time > from && time < until);
  return ![from, ...instants].some(
    (time) =>
      evaluateLicenses(all, new Date(time)).effective?.licenseId ===
      candidate.licenseId,
  );
}

/**
 * The next instant the license in force can change: a waiting license starts,
 * or the license in force reaches its end (one still running may take over)
 * or its grace end. Null when nothing is dated ahead.
 */
export function nextLicenseChange(
  evaluation: LicenseEvaluation,
  now: Date,
): Date | null {
  const candidates = [
    evaluation.upcoming?.startDate,
    evaluation.effective?.endDate,
    evaluation.effective?.graceEndsAt,
  ].flatMap((date) => (date && date.getTime() > now.getTime() ? [date] : []));
  if (candidates.length === 0) return null;
  return new Date(Math.min(...candidates.map((date) => date.getTime())));
}

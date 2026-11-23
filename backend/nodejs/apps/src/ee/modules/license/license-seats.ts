import { ForbiddenError } from '../../../libs/errors/http.errors';
import { seatsInForce, type LicenseStore } from './license.service';
import { isUnlimitedSeats, type LicenseSeats } from './license.types';

/** When the install first went over its seats: `{ overSince: string | null }`. */
export const SEAT_LIMIT_KEY = '/services/licence/seats';

/** Admins are warned this long after the install goes over; then new users are refused. */
export const SEAT_WARNING_DAYS = 14;
const DAY_MS = 24 * 60 * 60 * 1000;
export const SEAT_WARNING_MS = SEAT_WARNING_DAYS * DAY_MS;

/**
 * Service accounts are identities for agents, not people, so they hold no
 * seat. A choice the owner can reverse: set true, and guard service-account
 * creation with `assertSeatFor` as well.
 */
export const SERVICE_ACCOUNTS_HOLD_SEATS = false;

/** The seat state of an install whose license in force caps its seats. */
export interface SeatLimit {
  seats: number;
  /** People with an account here, invited ones included, removed ones not. */
  used: number;
  /** How many `used` is above `seats`; 0 when within. */
  over: number;
  /** When it first went over; null when within. */
  overSince: string | null;
  /** When new users start being refused: `overSince` plus SEAT_WARNING_DAYS. */
  refuseFrom: string | null;
  /** Whole days, rounded up, until `refuseFrom`; 0 once refusing, null when within. */
  daysLeft: number | null;
  /** New users are refused now; those already here keep working. */
  refusing: boolean;
}

/** The seat state, or null when nothing limits seats: no license in force, or `unlimited`. */
export function seatLimitState(
  seats: LicenseSeats | null,
  used: number,
  storedOverSince: Date | null,
  now: Date,
): SeatLimit | null {
  if (seats === null || isUnlimitedSeats(seats)) return null;
  const over = Math.max(0, used - seats);
  const overSince = over > 0 ? (storedOverSince ?? now) : null;
  const refuseFrom = overSince
    ? new Date(overSince.getTime() + SEAT_WARNING_MS)
    : null;
  const left = refuseFrom ? refuseFrom.getTime() - now.getTime() : null;
  return {
    seats,
    used,
    over,
    overSince: overSince?.toISOString() ?? null,
    refuseFrom: refuseFrom?.toISOString() ?? null,
    daysLeft: left === null ? null : Math.max(0, Math.ceil(left / DAY_MS)),
    refusing: left !== null && left <= 0,
  };
}

const storedOverSince = (stored: unknown): Date | null => {
  const value = (stored as { overSince?: unknown } | null)?.overSince;
  const at = typeof value === 'string' ? new Date(value) : null;
  return at && !Number.isNaN(at.getTime()) ? at : null;
};

/**
 * Works out the seat state and stores when the install first went over,
 * clearing it once back within (or once nothing limits seats), so going over
 * again starts a new warning. People are counted only when seats are capped.
 */
export async function checkSeats(
  store: LicenseStore,
  countPeople: () => Promise<number>,
  now: Date = new Date(),
): Promise<SeatLimit | null> {
  const seats = await seatsInForce(store, now);
  const stored = await store.read(SEAT_LIMIT_KEY);
  const limited = seats !== null && !isUnlimitedSeats(seats);
  const used = limited ? await countPeople() : 0;
  const state = seatLimitState(seats, used, storedOverSince(stored), now);
  const overSince = state?.overSince ?? null;
  if (overSince !== (storedOverSince(stored)?.toISOString() ?? null)) {
    await store.write(SEAT_LIMIT_KEY, { overSince });
  }
  return state;
}

/** How many more people may be added now: any number unless new users are refused, then the seats left. */
export const seatRoom = (limit: SeatLimit | null): number =>
  limit?.refusing ? Math.max(0, limit.seats - limit.used) : Infinity;

/** What an admin adding users is told once the seats are full. */
export const seatRefusal = (seats: number): ForbiddenError =>
  new ForbiddenError(
    `Your licence covers ${String(seats)} users. Remove users or add seats to invite more.`,
  );

/** What a person signing in for the first time is told once the seats are full. */
export const SEAT_REFUSAL_AT_SIGN_IN =
  "Your organization's licence has no free seats. Ask your administrator to remove users or add seats.";

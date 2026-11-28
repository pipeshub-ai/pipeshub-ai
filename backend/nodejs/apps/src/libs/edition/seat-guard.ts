import { ForbiddenError } from '../errors/http.errors';

/** What the edition allows now: how many more people may be added, and why any beyond that are refused. */
export interface SeatCheck {
  /** Infinity when nothing limits it. */
  room: number;
  /** Why people beyond `room` are refused: told to an admin adding them, or to a person signing in. */
  refusal: { admin: string; signIn: string };
}

/** How an edition limits the people an install may add. Supplied by the edition boot (`EditionBoot.seatGuard`). */
export interface SeatGuard {
  check(): Promise<SeatCheck>;
  /** After people were added or removed. Never throws. */
  afterChange(): Promise<void>;
}

/** No limit: what this code does when no edition supplies a guard. */
export const noopSeatGuard: SeatGuard = {
  check: () =>
    Promise.resolve({ room: Infinity, refusal: { admin: '', signIn: '' } }),
  afterChange: () => Promise.resolve(),
};

let guard: SeatGuard = noopSeatGuard;

/** app.ts installs the edition's guard at start-up; no argument puts the no-op one back (tests). */
export const setSeatGuard = (replacement: SeatGuard = noopSeatGuard): void => {
  guard = replacement;
};

export const getSeatGuard = (): SeatGuard => guard;

/** Refuses to add one more person when the edition has no room for them. */
export async function assertSeatFor(
  audience: keyof SeatCheck['refusal'] = 'admin',
): Promise<void> {
  const { room, refusal } = await guard.check();
  if (room < 1) throw new ForbiddenError(refusal[audience]);
}

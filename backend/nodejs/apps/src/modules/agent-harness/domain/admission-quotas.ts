import { HarnessQuotaExceededError } from './errors';
import { RunStatus } from './execution';

/**
 * Admission quotas (P1.14). Constants, not configuration: a deployment that
 * needs other limits changes them here.
 */
export const ADMISSION_QUOTAS = Object.freeze({
  /** Runs holding or waiting for a worker, per actor and per org. */
  activeRunsPerUser: 10,
  activeRunsPerOrg: 200,
  /** Admissions per fixed window, per actor and per org. */
  admissionsPerUser: 30,
  admissionsPerOrg: 600,
  windowMs: 60_000,
  /** No run end can be predicted, so a full active-run quota asks for this. */
  activeRetryAfterS: 15,
});

export type AdmissionQuotas = {
  readonly [K in keyof typeof ADMISSION_QUOTAS]: number;
};

/**
 * C4.10 `quotas.*`: an org's caps on its active runs, as the deployment and
 * the org lowered them. Absent: the release's.
 */
export interface AdmissionQuotaCaps {
  activeRunsPerOrg?: number;
  activeRunsPerUser?: number;
}

export const QUOTA_ACTIVE_PER_ORG = 'quotas.activeRunsPerOrg';
export const QUOTA_ACTIVE_PER_USER = 'quotas.activeRunsPerUser';

const positiveCap = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value > 0;

/** `quotas` with `caps` applied: a cap only ever lowers the release's quota. */
export function cappedQuotas(
  quotas: AdmissionQuotas,
  caps: AdmissionQuotaCaps | undefined,
): AdmissionQuotas {
  if (caps === undefined) return quotas;
  return {
    ...quotas,
    activeRunsPerOrg: positiveCap(caps.activeRunsPerOrg)
      ? Math.min(quotas.activeRunsPerOrg, caps.activeRunsPerOrg)
      : quotas.activeRunsPerOrg,
    activeRunsPerUser: positiveCap(caps.activeRunsPerUser)
      ? Math.min(quotas.activeRunsPerUser, caps.activeRunsPerUser)
      : quotas.activeRunsPerUser,
  };
}

/** The caps a run's pin resolved (`effectiveConfig.dimensions`); none for dimensions it does not hold. */
export function quotaCapsOfPin(resolved: {
  effectiveConfig: {
    dimensions: Record<string, { value?: unknown } | undefined>;
  };
}): AdmissionQuotaCaps {
  const org = resolved.effectiveConfig.dimensions[QUOTA_ACTIVE_PER_ORG]?.value;
  const user =
    resolved.effectiveConfig.dimensions[QUOTA_ACTIVE_PER_USER]?.value;
  return {
    ...(positiveCap(org) ? { activeRunsPerOrg: org } : {}),
    ...(positiveCap(user) ? { activeRunsPerUser: user } : {}),
  };
}

/**
 * Runs that count against the active quota: queued ones wait for a worker,
 * running and cancelling ones hold one. Suspended runs (approval, pause,
 * reconciliation) hold nothing and do not count.
 */
export const QUOTA_ACTIVE_STATUSES: readonly RunStatus[] = Object.freeze([
  'queued',
  'running',
  'cancelling',
]);

export interface QuotaWindow {
  windowStart: number;
  count: number;
}

export const orgQuotaKey = (orgId: string): string =>
  JSON.stringify(['org', orgId]);

export const userQuotaKey = (orgId: string, actorId: string): string =>
  JSON.stringify(['user', orgId, actorId]);

/** The window after one more admission at `now`: a new one once it lapsed. */
export function advanceWindow(
  current: QuotaWindow | null,
  now: number,
  windowMs: number,
): QuotaWindow {
  return current === null || now - current.windowStart >= windowMs
    ? { windowStart: now, count: 1 }
    : { windowStart: current.windowStart, count: current.count + 1 };
}

/** Throws when this admission is one too many for its window. */
export function assertWithinRate(
  scope: 'org' | 'user',
  window: QuotaWindow,
  limit: number,
  now: number,
  windowMs: number,
): void {
  if (window.count <= limit) return;
  throw rateRefusal(scope, window, now, windowMs);
}

const rateRefusal = (
  scope: 'org' | 'user',
  window: QuotaWindow,
  now: number,
  windowMs: number,
): HarnessQuotaExceededError =>
  new HarnessQuotaExceededError(
    scope,
    'admission_rate',
    Math.max(1, Math.ceil((window.windowStart + windowMs - now) / 1000)),
  );

/** Throws when admitting one more run would exceed the active-run quota. */
export function assertActiveBelow(
  scope: 'org' | 'user',
  active: number,
  limit: number,
  quotas: Pick<AdmissionQuotas, 'activeRetryAfterS'>,
): void {
  if (active < limit) return;
  throw new HarnessQuotaExceededError(
    scope,
    'active_runs',
    quotas.activeRetryAfterS,
  );
}

export interface AdmissionQuotaState {
  /** Windows already advanced for this admission. */
  orgWindow: QuotaWindow;
  userWindow: QuotaWindow;
  /** Active runs before this admission. */
  orgActive: number;
  userActive: number;
}

/** Every quota an admission must fit, in one place for every store. */
export function assertAdmissionQuotas(
  state: AdmissionQuotaState,
  now: number,
  quotas: AdmissionQuotas,
): void {
  assertActiveBelow('user', state.userActive, quotas.activeRunsPerUser, quotas);
  assertActiveBelow('org', state.orgActive, quotas.activeRunsPerOrg, quotas);
  assertAdmissionRate(state, now, quotas);
}

/**
 * The rate windows alone: a steering message (T2) counts as an admission,
 * like any message the user sends, but adds no run to the active quota.
 */
export function assertAdmissionRate(
  state: Pick<AdmissionQuotaState, 'orgWindow' | 'userWindow'>,
  now: number,
  quotas: AdmissionQuotas,
): void {
  assertWithinRate(
    'user',
    state.userWindow,
    quotas.admissionsPerUser,
    now,
    quotas.windowMs,
  );
  assertWithinRate(
    'org',
    state.orgWindow,
    quotas.admissionsPerOrg,
    now,
    quotas.windowMs,
  );
}

/**
 * How long an admission's hold on its quota documents outlives a process
 * that died holding it. Longer than a transaction can live (MongoDB's
 * `transactionLifetimeLimitSeconds`, 60 s), so a hold never lapses under an
 * admission that can still commit.
 */
export const ADMISSION_HOLD_MS = 120_000;

/** An admission between reserving its place and knowing whether it happened. */
export interface QuotaHold {
  id: string;
  /** The run the admission creates, if it creates one. */
  runId: string;
  at: number;
  /** The window its place was counted in. */
  windowStart: number;
  /** The admission's receipt key (`admissionScopeKey`), on an actor's window: its identical twins wait for it. */
  admission?: string;
}

/** One org's or one actor's quota document. */
export interface QuotaLedgerState extends QuotaWindow {
  /** In the order they reserved. */
  holds: QuotaHold[];
}

export interface QuotaReservationRequest {
  id: string;
  runId: string;
  /** Set: an identical admission (same key) holding a place here makes this one wait for it (QA21-1). */
  admission?: string;
  now: number;
  limit: number;
  windowMs: number;
  holdMs: number;
}

export interface QuotaReservation {
  /** As the rate checks read it: one past the limit when no place was left. */
  window: QuotaWindow;
  /** Runs of admissions that reserved earlier and are not known to have ended. */
  ahead: string[];
  /**
   * An identical admission holds a place in this window: nothing was taken or recorded, and this one waits for it
   * to end rather than being counted against its own twin.
   */
  twin: boolean;
}

/** A live hold of another instance of the same admission. */
const twinOf = (
  holds: readonly QuotaHold[],
  request: Pick<QuotaReservationRequest, 'id' | 'admission'>,
): QuotaHold | undefined =>
  request.admission === undefined
    ? undefined
    : holds.find(
        (hold) =>
          hold.admission === request.admission && hold.id !== request.id,
      );

/**
 * Takes a place in the window for admission `id`, or none when the window is
 * full, and records the hold in arrival order. Reserving again (a retried
 * transaction) keeps the place and the position.
 */
export function reserveQuota(
  state: QuotaLedgerState | null,
  request: QuotaReservationRequest,
): QuotaLedgerState {
  const { id, runId, now, limit, windowMs, holdMs } = request;
  const lapsed = state === null || now - state.windowStart >= windowMs;
  const windowStart = lapsed ? now : state.windowStart;
  const count = lapsed ? 0 : state.count;
  const holds = (state?.holds ?? []).filter((hold) => hold.at > now - holdMs);
  const mine = holds.find((hold) => hold.id === id);
  const counted = mine?.windowStart === windowStart;
  if (twinOf(holds, request) !== undefined || (!counted && count >= limit)) {
    return { windowStart, count, holds: holds.filter((hold) => hold !== mine) };
  }
  const hold: QuotaHold = {
    id,
    runId,
    at: now,
    windowStart,
    ...(request.admission !== undefined
      ? { admission: request.admission }
      : {}),
  };
  return {
    windowStart,
    count: counted ? count : count + 1,
    holds:
      mine === undefined
        ? [...holds, hold]
        : holds.map((other) => (other === mine ? hold : other)),
  };
}

/** What admission `request.id` got from the state `reserveQuota` returned. */
export function reservationOf(
  state: QuotaLedgerState,
  request: Pick<QuotaReservationRequest, 'id' | 'limit' | 'admission'>,
): QuotaReservation {
  const index = state.holds.findIndex((hold) => hold.id === request.id);
  return {
    window: {
      windowStart: state.windowStart,
      count: index < 0 ? request.limit + 1 : state.count,
    },
    ahead: state.holds.slice(0, Math.max(index, 0)).map((hold) => hold.runId),
    twin: index < 0 && twinOf(state.holds, request) !== undefined,
  };
}

/**
 * The org's active runs as an admission must count them: the ones its
 * snapshot holds, and the runs of the admissions ahead of it that its
 * snapshot does not hold yet (`seenAhead` of them are in it). Whoever
 * reserved first is counted by everyone behind it, so two admissions cannot
 * both take the last place.
 */
export const activeWithAhead = (
  activeInSnapshot: number,
  ahead: readonly string[],
  seenAhead: number,
): number => activeInSnapshot + ahead.length - seenAhead;

/**
 * An admission's reservations (SEC-DOS-01, DOS-MONGO-1): the actor's place first, the org's only once the actor may
 * still be admitted, so a person's burst holds no more of the org's places than their own active quota.
 */
export interface HeldQuotas {
  user: QuotaReservation;
  /** None: the actor's own reservation already refuses the admission (`actorRefusal`), and the org was not asked. */
  org: QuotaReservation | null;
}

/**
 * Whether the org's window is asked at all. Not when the actor's window had no place, nor when as many of their
 * admissions as their active quota are already ahead of this one: every snapshot counts those, so it is refused.
 * A steering message, which adds no run, asks the org in its transaction instead (`orgNeeded`).
 */
export const asksOrg = (
  user: QuotaReservation,
  quotas: AdmissionQuotas,
): boolean =>
  user.window.count <= quotas.admissionsPerUser &&
  user.ahead.length < quotas.activeRunsPerUser;

/** A steering message counts in the org's window even when the actor's admissions ahead fill their active quota. */
export const orgNeeded = (
  user: QuotaReservation,
  quotas: AdmissionQuotas,
): boolean => user.window.count <= quotas.admissionsPerUser;

/** Why an admission the org was not asked for is refused, on the actor's own quota. */
export function actorRefusal(
  user: QuotaReservation,
  now: number,
  quotas: AdmissionQuotas,
): HarnessQuotaExceededError {
  return user.window.count > quotas.admissionsPerUser
    ? rateRefusal('user', user.window, now, quotas.windowMs)
    : new HarnessQuotaExceededError(
        'user',
        'active_runs',
        quotas.activeRetryAfterS,
      );
}

/** The runs ahead of an admission in either window: whether its snapshot holds them decides its active counts. */
export const runsAhead = (held: HeldQuotas): string[] => [
  ...new Set([...held.user.ahead, ...(held.org?.ahead ?? [])]),
];

/** The actor's and the org's active runs, as `assertHeldQuotas` reads them from the admission's snapshot. */
export interface SnapshotCounts {
  orgActive: number;
  userActive: number;
  /** Which of `runsAhead` the snapshot holds, whatever their status. */
  seen: ReadonlySet<string>;
}

const seenOf = (ahead: readonly string[], seen: ReadonlySet<string>): number =>
  ahead.filter((runId) => seen.has(runId)).length;

/**
 * Every quota an admission must fit, decided on its reservations and its snapshot, for every store that reserves
 * outside its transaction. `counts` null: the rate windows alone (a steering message).
 */
export function assertHeldQuotas(
  held: HeldQuotas,
  counts: SnapshotCounts | null,
  now: number,
  quotas: AdmissionQuotas,
): void {
  if (held.org === null) throw actorRefusal(held.user, now, quotas);
  const windows = { orgWindow: held.org.window, userWindow: held.user.window };
  if (counts === null) {
    assertAdmissionRate(windows, now, quotas);
    return;
  }
  assertAdmissionQuotas(
    {
      ...windows,
      orgActive: activeWithAhead(
        counts.orgActive,
        held.org.ahead,
        seenOf(held.org.ahead, counts.seen),
      ),
      userActive: activeWithAhead(
        counts.userActive,
        held.user.ahead,
        seenOf(held.user.ahead, counts.seen),
      ),
    },
    now,
    quotas,
  );
}

/**
 * A window with no place taken and no hold is kept as no document: a refused admission leaves nothing behind, not
 * even the empty window it opened (QA26-2: a refused replay changes nothing of the org).
 */
export const holdsNothing = (state: QuotaLedgerState): boolean =>
  state.count === 0 && state.holds.length === 0;

/** Ends admission `id`'s hold; `refund` gives its place back while its window lasts. */
export function releaseQuota(
  state: QuotaLedgerState,
  id: string,
  refund: boolean,
): QuotaLedgerState {
  const mine = state.holds.find((hold) => hold.id === id);
  return {
    windowStart: state.windowStart,
    count:
      refund && mine?.windowStart === state.windowStart
        ? state.count - 1
        : state.count,
    holds: state.holds.filter((hold) => hold !== mine),
  };
}

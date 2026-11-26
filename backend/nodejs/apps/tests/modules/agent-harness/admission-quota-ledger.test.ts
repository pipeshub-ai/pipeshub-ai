/**
 * PERF-C (PERFB-F1): the org's admission window as a ledger an admission
 * reserves in before its transaction and settles after it. PERFC-1 is the
 * rules; PERFC-2 runs any interleaving of admissions against them; DOS-MONGO-1
 * runs a person's burst against the actor's and the org's ledgers together.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import {
  activeWithAhead,
  actorRefusal,
  ADMISSION_HOLD_MS,
  ADMISSION_QUOTAS,
  AdmissionQuotas,
  asksOrg,
  assertHeldQuotas,
  holdsNothing,
  orgNeeded,
  QuotaLedgerState,
  QuotaReservation,
  QuotaReservationRequest,
  releaseQuota,
  reservationOf,
  reserveQuota,
  runsAhead,
} from '../../../src/modules/agent-harness/domain/admission-quotas';
import { HarnessQuotaExceededError } from '../../../src/modules/agent-harness/domain/errors';
import { propertyRuns } from '../../support/harness/property-profile';

const WINDOW_MS = 60_000;

const request = (id: string, overrides: Partial<QuotaReservationRequest> = {}): QuotaReservationRequest => ({
  id,
  runId: `run-${id}`,
  now: 1_000,
  limit: 3,
  windowMs: WINDOW_MS,
  holdMs: ADMISSION_HOLD_MS,
  ...overrides,
});

const reserveAll = (ids: string[], overrides: Partial<QuotaReservationRequest> = {}): QuotaLedgerState =>
  ids.reduce<QuotaLedgerState | null>((state, id) => reserveQuota(state, request(id, overrides)), null) as QuotaLedgerState;

describe('PERFC-1: the org admission window as a ledger', () => {
  it('the first reservation opens the window with one place taken and nobody ahead', () => {
    const state = reserveQuota(null, request('a'));
    expect(state).to.deep.equal({
      windowStart: 1_000,
      count: 1,
      holds: [{ id: 'a', runId: 'run-a', at: 1_000, windowStart: 1_000 }],
    });
    expect(reservationOf(state, request('a'))).to.deep.equal({ window: { windowStart: 1_000, count: 1 }, ahead: [], twin: false });
  });

  it('later reservations take the next places and see the runs of those ahead of them, in arrival order', () => {
    const state = reserveAll(['a', 'b', 'c']);
    expect(state.count).to.equal(3);
    expect(reservationOf(state, request('a')).ahead).to.deep.equal([]);
    expect(reservationOf(state, request('b')).ahead).to.deep.equal(['run-a']);
    expect(reservationOf(state, request('c'))).to.deep.equal({
      window: { windowStart: 1_000, count: 3 },
      ahead: ['run-a', 'run-b'],
      twin: false,
    });
  });

  it('a full window gives no place, counts nothing and holds nothing; it reads as one past the limit', () => {
    const full = reserveAll(['a', 'b', 'c']);
    const refused = reserveQuota(full, request('d'));
    expect(refused).to.deep.equal(full);
    expect(reservationOf(refused, request('d'))).to.deep.equal({ window: { windowStart: 1_000, count: 4 }, ahead: [], twin: false });
  });

  it('QA21-1: an identical admission holding a place makes its twin take nothing and wait; a lapsed one holds nobody back', () => {
    const turn = { admission: 'turn-1' };
    const held = reserveQuota(null, request('a', turn));
    expect(held.holds).to.deep.equal([{ id: 'a', runId: 'run-a', at: 1_000, windowStart: 1_000, admission: 'turn-1' }]);
    const twin = reserveQuota(held, request('b', turn));
    expect(twin, 'the twin takes no place and records no hold').to.deep.equal(held);
    expect(reservationOf(twin, request('b', turn))).to.deep.include({ twin: true, ahead: [] });
    expect(reservationOf(reserveQuota(held, request('a', turn)), request('a', turn)).twin, 'a retry of the same one').to.equal(false);
    expect(reservationOf(reserveQuota(held, request('c', { admission: 'turn-2' })), request('c')).ahead, 'another turn').to.deep.equal(['run-a']);
    expect(reserveQuota(held, request('b')).count, 'past the wait, the key is not asked').to.equal(2);
    const later = request('b', { ...turn, now: 1_000 + ADMISSION_HOLD_MS });
    expect(reservationOf(reserveQuota(held, later), later).twin, 'a twin whose hold lapsed').to.equal(false);
  });

  it('a lapsed window starts again at the reservation; holds of the old window stay ahead while they live', () => {
    const full = reserveAll(['a', 'b', 'c']);
    const lapsed = reserveQuota(full, request('d', { now: 1_000 + WINDOW_MS }));
    expect([lapsed.windowStart, lapsed.count]).to.deep.equal([1_000 + WINDOW_MS, 1]);
    expect(reservationOf(lapsed, request('d')).ahead).to.deep.equal(['run-a', 'run-b', 'run-c']);
    const before = reserveQuota(full, request('d', { now: 999 + WINDOW_MS }));
    expect(before.count, 'one millisecond earlier the window still stands').to.equal(3);
  });

  it('reserving again (a retried transaction) keeps the place and the position', () => {
    const state = reserveAll(['a', 'b', 'c']);
    const again = reserveQuota(state, request('b', { now: 1_500 }));
    expect(again.count).to.equal(3);
    expect(again.holds.map((hold) => [hold.id, hold.at])).to.deep.equal([
      ['a', 1_000],
      ['b', 1_500],
      ['c', 1_000],
    ]);
    expect(reservationOf(again, request('b')).ahead).to.deep.equal(['run-a']);
  });

  it('reserving again after the window lapsed takes a place in the new one, or loses the hold when it is full', () => {
    const old = reserveAll(['a']);
    const now = 1_000 + WINDOW_MS;
    const renewed = reserveQuota(old, request('a', { now }));
    expect([renewed.windowStart, renewed.count, renewed.holds[0]?.windowStart]).to.deep.equal([now, 1, now]);
    const others = reserveAll(['x', 'y', 'z'], { now });
    const crowded = { ...others, holds: [...old.holds, ...others.holds] };
    const refused = reserveQuota(crowded, request('a', { now: now + 1 }));
    expect(refused.count).to.equal(3);
    expect(refused.holds.map((hold) => hold.id)).to.deep.equal(['x', 'y', 'z']);
  });

  it('a place given back frees it for the next admission; kept, it stays taken', () => {
    const full = reserveAll(['a', 'b', 'c']);
    const refunded = releaseQuota(full, 'b', true);
    expect([refunded.count, refunded.holds.map((hold) => hold.id)]).to.deep.equal([2, ['a', 'c']]);
    expect(reservationOf(reserveQuota(refunded, request('d')), request('d')).window.count).to.equal(3);
    const kept = releaseQuota(full, 'b', false);
    expect([kept.count, kept.holds.map((hold) => hold.id)]).to.deep.equal([3, ['a', 'c']]);
    expect(reservationOf(reserveQuota(kept, request('d')), request('d')).window.count).to.equal(4);
  });

  it('QA26-2: a window whose only place was given back holds nothing, and is kept as no document', () => {
    const held = reserveQuota(null, request('a'));
    expect(holdsNothing(held)).to.equal(false);
    expect(holdsNothing(releaseQuota(held, 'a', true))).to.equal(true);
    expect(holdsNothing(releaseQuota(held, 'a', false)), 'a kept place').to.equal(false);
  });

  it('a place is given back once, and only in the window it was taken in', () => {
    const state = reserveAll(['a', 'b']);
    const once = releaseQuota(state, 'a', true);
    expect(releaseQuota(once, 'a', true), 'a second release changes nothing').to.deep.equal(once);
    expect(releaseQuota(state, 'never-reserved', true)).to.deep.equal(state);
    const next = reserveQuota(state, request('c', { now: 1_000 + WINDOW_MS }));
    const late = releaseQuota(next, 'a', true);
    expect(late.count, "the new window's count is not the old hold's to give back").to.equal(1);
    expect(late.holds.map((hold) => hold.id)).to.deep.equal(['b', 'c']);
  });

  it('a hold that outlived its process is ahead of nobody once it lapsed', () => {
    const state = reserveAll(['dead']);
    const alive = reserveQuota(state, request('b', { now: 999 + ADMISSION_HOLD_MS, windowMs: 10 * ADMISSION_HOLD_MS }));
    expect(reservationOf(alive, request('b')).ahead).to.deep.equal(['run-dead']);
    const lapsed = reserveQuota(state, request('b', { now: 1_000 + ADMISSION_HOLD_MS, windowMs: 10 * ADMISSION_HOLD_MS }));
    expect(reservationOf(lapsed, request('b')).ahead).to.deep.equal([]);
    expect(lapsed.count, 'its place stays counted until the window ends').to.equal(2);
  });

  it('active runs count the snapshot and the runs ahead that are not in it yet', () => {
    expect(activeWithAhead(4, [], 0)).to.equal(4);
    expect(activeWithAhead(4, ['run-a', 'run-b'], 0)).to.equal(6);
    expect(activeWithAhead(4, ['run-a', 'run-b'], 2)).to.equal(4);
  });
});

/** How an admission ends once it passed (or did not pass) its quotas. */
type Ending = 'run' | 'steer' | 'abort' | 'replay' | 'lost-committed' | 'lost-aborted';

interface Admission {
  id: string;
  ending: Ending;
  /** reserve, snapshot, decide and end, in this order; `stage` is the next one. */
  stage: number;
  snapshot: Set<string>;
  /** What the reservation answered: an admission decides on that, not on the ledger as it is later. */
  reserved?: QuotaReservation;
  refused: boolean;
}

/**
 * The store's use of the ledger, a step at a time: reserve, then the
 * transaction's snapshot, then the decision on what the reservation and the
 * snapshot say, then the commit (or not) and the release.
 */
class World {
  ledger: QuotaLedgerState | null = null;
  /** Run ids of committed runs, all active. */
  readonly active = new Set<string>();
  counted = 0;
  lost = 0;

  constructor(
    private readonly activeLimit: number,
    private readonly rateLimit: number,
  ) {}

  private request(admission: Admission): QuotaReservationRequest {
    return request(admission.id, { limit: this.rateLimit });
  }

  step(admission: Admission): void {
    const stage = admission.stage;
    admission.stage += 1;
    if (stage === 0) {
      this.ledger = reserveQuota(this.ledger, this.request(admission));
      admission.reserved = reservationOf(this.ledger, this.request(admission));
    } else if (stage === 1) {
      admission.snapshot = new Set(this.active);
    } else if (stage === 2) {
      const reserved = admission.reserved as QuotaReservation;
      const seen = reserved.ahead.filter((runId) => admission.snapshot.has(runId)).length;
      const active = activeWithAhead(admission.snapshot.size, reserved.ahead, seen);
      const needsRun = admission.ending !== 'steer';
      admission.refused = reserved.window.count > this.rateLimit || (needsRun && active >= this.activeLimit);
    } else if (stage === 3) {
      this.end(admission);
    }
  }

  private end(admission: Admission): void {
    const ledger = this.ledger as QuotaLedgerState;
    const commits = !admission.refused && ['run', 'steer', 'lost-committed'].includes(admission.ending);
    if (commits && admission.ending !== 'steer') this.active.add(`run-${admission.id}`);
    if (commits) this.counted += 1;
    if (!admission.refused && admission.ending.startsWith('lost')) {
      // The commit's answer never came: the hold is left to lapse.
      this.lost += 1;
      return;
    }
    this.ledger = releaseQuota(ledger, admission.id, !commits);
  }
}

const endings = fc.constantFrom<Ending>('run', 'run', 'run', 'steer', 'abort', 'replay', 'lost-committed', 'lost-aborted');

describe('PERFC-2: any interleaving of admissions keeps the org within its limits', function () {
  this.timeout(120_000);

  it('never more active runs than the limit, never more places than the window has; nothing left once all ended', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 4 }),
        fc.integer({ min: 1, max: 6 }),
        fc.array(endings, { minLength: 1, maxLength: 9 }),
        fc.infiniteStream(fc.nat()),
        (activeLimit, rateLimit, kinds, picks) => {
          const world = new World(activeLimit, rateLimit);
          const admissions: Admission[] = kinds.map((ending, index) => ({
            id: String(index),
            ending,
            stage: 0,
            snapshot: new Set(),
            refused: false,
          }));
          const pick = picks[Symbol.iterator]();
          for (;;) {
            const open = admissions.filter((admission) => admission.stage < 4);
            if (open.length === 0) break;
            world.step(open[(pick.next().value as number) % open.length] as Admission);
            expect(world.active.size, 'active runs').to.be.at.most(activeLimit);
            expect(world.ledger?.count ?? 0, 'places taken in the window').to.be.at.most(rateLimit);
          }
          const ledger = world.ledger as QuotaLedgerState;
          expect(ledger.count, 'every admission that happened is counted').to.be.at.least(world.counted);
          if (world.lost === 0) {
            expect(ledger.count, 'refused, aborted and replayed admissions took nothing').to.equal(world.counted);
            expect(ledger.holds).to.deep.equal([]);
          } else {
            expect(ledger.holds.length).to.equal(world.lost);
          }
        },
      ),
      propertyRuns(2_000),
    );
  });

  it('with room for everyone, nobody is refused whatever the order (no admission waits on another)', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 8 }), fc.infiniteStream(fc.nat()), (count, picks) => {
        const world = new World(count, count);
        const admissions: Admission[] = Array.from({ length: count }, (_, index) => ({
          id: String(index),
          ending: 'run' as Ending,
          stage: 0,
          snapshot: new Set<string>(),
          refused: false,
        }));
        const pick = picks[Symbol.iterator]();
        for (;;) {
          const open = admissions.filter((admission) => admission.stage < 4);
          if (open.length === 0) break;
          world.step(open[(pick.next().value as number) % open.length] as Admission);
        }
        expect(world.active.size).to.equal(count);
      }),
      propertyRuns(500),
    );
  });
});

/** One admission of the two-scope world: the actor's place, then the org's if asked, then snapshot, decision, end. */
interface ScopedAdmission {
  id: string;
  actor: 'flood' | 'colleague';
  aborts: boolean;
  stage: number;
  user?: QuotaReservation;
  org: QuotaReservation | null;
  snapshot: Set<string>;
  refusal?: HarnessQuotaExceededError;
}

/**
 * The store's use of both ledgers (`AdmissionQuotaHold`): the actor's window first, the org's only when `asksOrg`
 * says the actor may still be admitted, then the transaction's snapshot and `assertHeldQuotas`.
 */
class ScopedWorld {
  readonly ledgers = new Map<string, QuotaLedgerState | null>();
  /** Committed runs, all active, by actor. */
  readonly active = new Map<string, ScopedAdmission['actor']>();
  /** The most org places the flooding actor's admissions held at once while not yet committed. */
  floodOrgHolds = 0;

  constructor(readonly quotas: AdmissionQuotas) {}

  private reserve(key: string, admission: ScopedAdmission, limit: number): QuotaReservation {
    const ask = request(admission.id, { limit, windowMs: this.quotas.windowMs });
    const state = reserveQuota(this.ledgers.get(key) ?? null, ask);
    this.ledgers.set(key, state);
    return reservationOf(state, ask);
  }

  step(admission: ScopedAdmission): void {
    const stage = admission.stage;
    admission.stage += 1;
    if (stage === 0) {
      admission.user = this.reserve(admission.actor, admission, this.quotas.admissionsPerUser);
    } else if (stage === 1) {
      if (asksOrg(admission.user as QuotaReservation, this.quotas)) {
        admission.org = this.reserve('org', admission, this.quotas.admissionsPerOrg);
      }
      this.floodOrgHolds = Math.max(this.floodOrgHolds, this.floodHolds());
    } else if (stage === 2) {
      admission.snapshot = new Set(this.active.keys());
    } else if (stage === 3) {
      const held = { user: admission.user as QuotaReservation, org: admission.org };
      const mine = [...admission.snapshot].filter((runId) => this.active.get(runId) === admission.actor);
      try {
        assertHeldQuotas(
          held,
          { orgActive: admission.snapshot.size, userActive: mine.length, seen: admission.snapshot },
          1_000,
          this.quotas,
        );
      } catch (error) {
        admission.refusal = error as HarnessQuotaExceededError;
      }
    } else if (stage === 4) {
      const commits = admission.refusal === undefined && !admission.aborts;
      if (commits) this.active.set(`run-${admission.id}`, admission.actor);
      for (const key of admission.org === null ? [admission.actor] : [admission.actor, 'org']) {
        this.ledgers.set(key, releaseQuota(this.ledgers.get(key) as QuotaLedgerState, admission.id, !commits));
      }
    }
  }

  private floodHolds(): number {
    const holds = this.ledgers.get('org')?.holds ?? [];
    return holds.filter((hold) => hold.id.startsWith('flood') && !this.active.has(hold.runId)).length;
  }
}

describe("DOS-MONGO-1: a person's burst against both ledgers, in any interleaving", function () {
  this.timeout(120_000);

  it('keeps every limit, holds at most their active quota of the org in flight, and a colleague with room still starts', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 3 }),
        fc.integer({ min: 0, max: 3 }),
        fc.array(fc.boolean(), { minLength: 1, maxLength: 10 }),
        fc.nat({ max: 10 }),
        fc.infiniteStream(fc.nat()),
        (perUser, extraWindow, floodAborts, colleagueAt, picks) => {
          const quotas: AdmissionQuotas = {
            ...ADMISSION_QUOTAS,
            activeRunsPerUser: perUser,
            admissionsPerUser: perUser + extraWindow,
            // Room for the person's runs, as many of theirs in flight, and the colleague (the bound under test).
            activeRunsPerOrg: 2 * perUser + 1,
            admissionsPerOrg: 2 * perUser + 1,
            windowMs: WINDOW_MS,
          };
          const world = new ScopedWorld(quotas);
          const admissions: ScopedAdmission[] = floodAborts.map((aborts, index) => ({
            id: `flood-${String(index)}`, actor: 'flood', aborts, stage: 0, org: null, snapshot: new Set(),
          }));
          const colleague: ScopedAdmission = {
            id: 'colleague', actor: 'colleague', aborts: false, stage: 0, org: null, snapshot: new Set(),
          };
          admissions.splice(Math.min(colleagueAt, admissions.length), 0, colleague);
          const pick = picks[Symbol.iterator]();
          for (;;) {
            const open = admissions.filter((admission) => admission.stage < 5);
            if (open.length === 0) break;
            world.step(open[(pick.next().value as number) % open.length] as ScopedAdmission);
            const flood = [...world.active.values()].filter((actor) => actor === 'flood').length;
            expect(flood, "the person's active runs").to.be.at.most(perUser);
            expect(world.active.size, "the org's active runs").to.be.at.most(quotas.activeRunsPerOrg);
            expect(world.ledgers.get('flood')?.count ?? 0).to.be.at.most(quotas.admissionsPerUser);
            expect(world.ledgers.get('org')?.count ?? 0).to.be.at.most(quotas.admissionsPerOrg);
          }
          expect(world.floodOrgHolds, "the person's org places in flight").to.be.at.most(perUser);
          expect(colleague.refusal, 'the colleague').to.equal(undefined);
          for (const admission of admissions) {
            if (admission.refusal !== undefined) {
              expect(admission.actor).to.equal('flood');
              expect(admission.refusal.scope, "a refusal of the person's burst is their own").to.equal('user');
            }
          }
          const committed = [...world.active.values()];
          expect(world.ledgers.get('org')).to.deep.include({ count: committed.length, holds: [] });
          expect(world.ledgers.get('flood')).to.deep.include({
            count: committed.filter((actor) => actor === 'flood').length,
            holds: [],
          });
        },
      ),
      propertyRuns(2_000),
    );
  });

  it('refuses on the actor alone when the org was not asked: no place is the rate, a full quota ahead is the active runs', () => {
    const quotas: AdmissionQuotas = { ...ADMISSION_QUOTAS, activeRunsPerUser: 2, admissionsPerUser: 3, windowMs: WINDOW_MS };
    const user = (count: number, ahead: string[]): QuotaReservation => ({ window: { windowStart: 0, count }, ahead, twin: false });
    expect(asksOrg(user(3, ['a']), quotas)).to.equal(true);
    expect(asksOrg(user(3, ['a', 'b']), quotas)).to.equal(false);
    expect(asksOrg(user(4, []), quotas)).to.equal(false);
    expect(orgNeeded(user(3, ['a', 'b']), quotas), 'a steering message still asks the org').to.equal(true);
    const refused = (count: number, ahead: string[]) => {
      const error = actorRefusal(user(count, ahead), 30_000, quotas);
      return `${error.scope}:${error.quota}:${String(error.retryAfterS)}`;
    };
    expect(refused(4, [])).to.equal('user:admission_rate:30');
    expect(refused(3, ['a', 'b'])).to.equal(`user:active_runs:${String(quotas.activeRetryAfterS)}`);
    expect(() => assertHeldQuotas({ user: user(4, []), org: null }, null, 30_000, quotas)).to.throw(HarnessQuotaExceededError);
    expect(runsAhead({ user: user(1, ['a', 'b']), org: { window: { windowStart: 0, count: 1 }, ahead: ['b', 'c'], twin: false } })).to.deep.equal(['a', 'b', 'c']);
  });
});

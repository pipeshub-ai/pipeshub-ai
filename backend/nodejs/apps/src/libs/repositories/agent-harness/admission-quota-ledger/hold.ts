import { setTimeout as delay } from 'timers/promises';
import { newObjectId } from '../../ids';
import {
  ADMISSION_HOLD_MS,
  AdmissionQuotas,
  asksOrg,
  HeldQuotas,
  orgNeeded,
  orgQuotaKey,
  QuotaReservation,
  QuotaReservationRequest,
  userQuotaKey,
} from '../../../../modules/agent-harness/domain/admission-quotas';

/**
 * Where an org's or an actor's admission window and its admissions in flight are kept: one write of its own per call,
 * never in the admission's transaction, where admissions of the whole org, or a person's burst, would abort or queue
 * behind each other (PERFB-F1, DOS-MONGO-1).
 */
export interface AdmissionQuotaLedger {
  reserve(
    key: string,
    request: QuotaReservationRequest,
  ): Promise<QuotaReservation>;
  /** `refund`: the admission did not happen, so its place in the window is given back. */
  release(key: string, id: string, refund: boolean): Promise<void>;
}

/** Counts the places given back, by why. */
export interface AdmissionQuotaMetrics {
  returned(why: 'not_admitted' | 'replayed'): void;
}

// How an admission waits for an identical one in flight: an admission's transaction takes milliseconds; 5 s in all
// is far past it and short of the 120 s a hold whose commit went unanswered keeps.
const TWIN_FIRST_PAUSE_MS = 5;
const TWIN_MAX_PAUSE_MS = 100;
const TWIN_WAIT_MS = 5_000;

/** An identical admission holds the actor's place: this one runs again once it ended (`AdmissionQuotaHold.afterTwin`). */
export class TwinInFlight extends Error {
  constructor() {
    super('An identical admission is in flight');
    this.name = 'TwinInFlight';
  }
}

/**
 * One admission's places in its actor's and its org's windows, from before its transaction to its outcome. The
 * actor's is reserved first and the org's only when the actor may still be admitted (`asksOrg`): a person's burst
 * holds at most their own active quota of the org's places, and no transaction writes a quota document, so the
 * burst's refusals abort nobody (SEC-DOS-01, DOS-MONGO-1).
 */
export class AdmissionQuotaHold {
  private readonly id = newObjectId();
  private held: HeldQuotas | null = null;
  // A reservation whose answer was lost may still have been written: the keys asked are released.
  private readonly asked = new Set<string>();
  private counted = false;
  private commit: 'none' | 'unanswered' | 'done' = 'none';
  private twinPauses = 0;
  private twinWaited = 0;

  constructor(
    private readonly ledger: AdmissionQuotaLedger,
    private readonly quotas: AdmissionQuotas,
    private readonly scope: {
      orgId: string;
      actorId: string;
      runId: string;
      admission: string;
    },
    private readonly metrics?: AdmissionQuotaMetrics,
  ) {}

  /** Once for every attempt of the transaction: an attempt that ran before decided nothing. */
  async reserve(now: number): Promise<void> {
    this.attempt();
    this.held = null;
    const user = await this.reserveUser(now);
    this.held = {
      user,
      org: asksOrg(user, this.quotas) ? await this.reserveOrg(now) : null,
    };
  }

  /**
   * The actor's place. An identical admission (a resend of the same turn) that holds one takes nothing here
   * (`twin`): its transaction replays the receipt once that one ended, where racing it would count the one turn
   * against itself (QA21-1). Once the wait is over the twin no longer holds this one back.
   */
  private reserveUser(now: number): Promise<QuotaReservation> {
    return this.reserveIn(
      userQuotaKey(this.scope.orgId, this.scope.actorId),
      this.quotas.admissionsPerUser,
      now,
      this.twinWaited < TWIN_WAIT_MS ? this.scope.admission : undefined,
    );
  }

  /** Throws `TwinInFlight` when an identical admission holds the actor's place: the transaction waits and runs again. */
  assertNoTwin(): void {
    if (this.reserved().user.twin) throw new TwinInFlight();
  }

  /** The pause before the admission runs again behind its twin; past `TWIN_WAIT_MS` in all, it no longer waits. */
  async afterTwin(): Promise<void> {
    const pause = Math.min(
      TWIN_FIRST_PAUSE_MS * 2 ** this.twinPauses,
      TWIN_MAX_PAUSE_MS,
    );
    this.twinPauses += 1;
    this.twinWaited += pause;
    await delay(pause);
  }

  /**
   * The org's place of a steering message the actor's admissions ahead left without one: it adds no run, so only
   * the window counts, and the update that takes the place decides that by itself.
   */
  async steeringPlace(now: number): Promise<HeldQuotas> {
    const held = this.reserved();
    if (held.org === null && orgNeeded(held.user, this.quotas)) {
      this.held = { ...held, org: await this.reserveOrg(now) };
    }
    return this.reserved();
  }

  private reserveOrg(now: number): Promise<QuotaReservation> {
    return this.reserveIn(
      orgQuotaKey(this.scope.orgId),
      this.quotas.admissionsPerOrg,
      now,
    );
  }

  private reserveIn(
    key: string,
    limit: number,
    now: number,
    admission?: string,
  ): Promise<QuotaReservation> {
    this.asked.add(key);
    return this.ledger.reserve(key, {
      id: this.id,
      runId: this.scope.runId,
      now,
      limit,
      windowMs: this.quotas.windowMs,
      holdMs: ADMISSION_HOLD_MS,
      ...(admission !== undefined ? { admission } : {}),
    });
  }

  /**
   * A new attempt of the admission's transaction on the places already reserved (PostgreSQL reserves once, before
   * its unit, which re-runs only its own statements): an attempt that ran before decided nothing.
   */
  attempt(): void {
    this.counted = false;
    this.commit = 'none';
  }

  reserved(): HeldQuotas {
    if (this.held === null) throw new Error('Admission quota not reserved');
    return this.held;
  }

  /** The attempt passed its quotas: it takes its places if it commits. */
  count(): void {
    this.counted = true;
  }

  /** The attempt ran to its end: from here only its commit is unanswered. */
  committing(): void {
    this.commit = 'unanswered';
  }

  committed(): void {
    this.commit = 'done';
  }

  /**
   * The places go back unless the admission committed and counted (a replay
   * does not). A commit that was never answered keeps its holds until they
   * lapse: giving a place back could admit one too many.
   */
  async settle(): Promise<void> {
    if (this.asked.size === 0 || this.commit === 'unanswered') return;
    const admitted = this.commit === 'done';
    const refund = !(admitted && this.counted);
    const released = await Promise.allSettled(
      [...this.asked].map((key) => this.ledger.release(key, this.id, refund)),
    );
    const failed = released.find(
      (outcome): outcome is PromiseRejectedResult =>
        outcome.status === 'rejected',
    );
    if (failed !== undefined) throw failed.reason;
    if (refund) this.metrics?.returned(admitted ? 'replayed' : 'not_admitted');
  }
}

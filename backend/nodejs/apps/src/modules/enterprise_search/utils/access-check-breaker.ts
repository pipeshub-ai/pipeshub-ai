/**
 * The circuit breaker around the viewer access check (`readableRecordIds`). While the query
 * service keeps failing, reads stop asking it: each one fails closed at once (every answer that
 * names a record is hidden as unconfirmed) instead of waiting out its deadline, and the service
 * gets room to recover.
 *
 * It opens when at least half of 20 or more checks in the last 10 s failed, or after 5 timeouts
 * in a row. Once it has been open 15 s it lets one check through, and that check closes it or
 * keeps it open another 15 s.
 *
 * It must not take a busy process for a slow service: a burst of reads can keep this process's
 * event loop busy past their deadlines while the service answers in time. So a timeout counts
 * only when its request went out with time to be answered and its timer fired on time; any
 * other is `busy`, which still fails its own read closed and counts neither way.
 */

/** How a check that went out ended. `busy`: it timed out for this process's own reasons. */
export type CheckOutcome = 'ok' | 'failed' | 'timeout' | 'busy';

/** What a request needs left of its read's deadline as it goes out, for its timeout to count. */
export const MIN_LEFT_AT_SEND_MS = 250;
/** How late the deadline's timer may fire for a timeout to count: later, the event loop was the delay. */
export const MAX_TIMER_LATENESS_MS = 100;

/** A check that timed out counts against the service only when it had time and its timer fired on time. */
export const timeoutOutcome = (
  leftAtSendMs: number,
  timerLatenessMs: number,
): 'timeout' | 'busy' =>
  leftAtSendMs >= MIN_LEFT_AT_SEND_MS && timerLatenessMs < MAX_TIMER_LATENESS_MS
    ? 'timeout'
    : 'busy';

/** A check `allow` let through; its outcome goes back with `record`. */
export interface CheckTicket {
  readonly probe: boolean;
  readonly generation: number;
}

export interface BreakerOptions {
  windowMs: number;
  minChecks: number;
  failureRatio: number;
  timeoutsInARow: number;
  openMs: number;
  now: () => number;
}

const DEFAULTS: BreakerOptions = {
  windowMs: 10_000,
  minChecks: 20,
  failureRatio: 0.5,
  timeoutsInARow: 5,
  openMs: 15_000,
  now: () => Date.now(),
};

export class CircuitBreaker {
  private readonly opts: BreakerOptions;
  private outcomes: Array<{ at: number; failed: boolean }> = [];
  private timeouts = 0;
  private openUntil: number | undefined;
  private probing = false;
  // Changes each time it opens or closes, so an answer to a check sent before that decides nothing.
  private generation = 0;

  constructor(opts: Partial<BreakerOptions> = {}) {
    this.opts = { ...DEFAULTS, ...opts };
  }

  /** A ticket when a check may go out now; undefined while it is open, and while its one probe is out. */
  allow(): CheckTicket | undefined {
    if (this.openUntil === undefined) {
      return { probe: false, generation: this.generation };
    }
    if (this.probing || this.opts.now() < this.openUntil) return undefined;
    this.probing = true;
    return { probe: true, generation: this.generation };
  }

  /** The outcome of a check `allow` let through. Returns whether this opened or closed the breaker. */
  record(
    ticket: CheckTicket,
    outcome: CheckOutcome,
  ): 'opened' | 'closed' | undefined {
    if (outcome === 'busy') {
      // It says nothing of the service: nothing is counted, and an open breaker probes again.
      if (ticket.probe && ticket.generation === this.generation)
        this.probing = false;
      return undefined;
    }
    const now = this.opts.now();
    if (ticket.probe && ticket.generation === this.generation) {
      this.probing = false;
      if (outcome === 'ok') {
        this.reset();
        return 'closed';
      }
      this.openUntil = now + this.opts.openMs;
      return undefined;
    }
    if (ticket.generation !== this.generation || this.openUntil !== undefined) {
      return undefined;
    }
    const since = now - this.opts.windowMs;
    this.outcomes = this.outcomes.filter((o) => o.at > since);
    this.outcomes.push({ at: now, failed: outcome !== 'ok' });
    this.timeouts = outcome === 'timeout' ? this.timeouts + 1 : 0;
    const failed = this.outcomes.filter((o) => o.failed).length;
    const failing =
      this.outcomes.length >= this.opts.minChecks &&
      failed >= this.opts.failureRatio * this.outcomes.length;
    if (!failing && this.timeouts < this.opts.timeoutsInARow) return undefined;
    this.outcomes = [];
    this.timeouts = 0;
    this.openUntil = now + this.opts.openMs;
    this.generation += 1;
    return 'opened';
  }

  /** Closed, with nothing remembered. */
  reset(): void {
    this.outcomes = [];
    this.timeouts = 0;
    this.openUntil = undefined;
    this.probing = false;
    this.generation += 1;
  }
}

/** The breaker of this process's viewer access checks. */
export const accessCheckBreaker = new CircuitBreaker();

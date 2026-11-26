/**
 * Manually driven clock; same semantics as `harness_core/testing/clock.py`
 * but in milliseconds. Timers are keyed to the suspend-aware clock, so
 * `suspend(ms)` fires what became due while the host slept while
 * `monotonic()` stays put. Due timers fire in deadline order (ties in
 * scheduling order) and each callback observes time equal to its deadline.
 */

interface Timer {
  deadline: number;
  seq: number;
  callback: () => void;
  cancelled: boolean;
}

export interface TimerHandle {
  readonly deadline: number;
  cancel(): void;
}

const EPOCH_MS = Date.UTC(2026, 0, 1);

export class ManualClock {
  private wallMs: number;
  private monotonicMs = 0;
  private suspendAwareMs = 0;
  private seq = 0;
  private timers: Timer[] = [];

  constructor(startMs: number = EPOCH_MS) {
    this.wallMs = startMs;
  }

  now(): Date {
    return new Date(this.wallMs);
  }

  monotonic(): number {
    return this.monotonicMs;
  }

  suspendAware(): number {
    return this.suspendAwareMs;
  }

  callLater(delayMs: number, callback: () => void): TimerHandle {
    if (delayMs < 0) throw new RangeError('delay must be non-negative');
    const timer: Timer = {
      deadline: this.suspendAwareMs + delayMs,
      seq: this.seq++,
      callback,
      cancelled: false,
    };
    this.timers.push(timer);
    return {
      deadline: timer.deadline,
      cancel: () => {
        timer.cancelled = true;
      },
    };
  }

  pendingTimers(): number {
    return this.timers.filter((t) => !t.cancelled).length;
  }

  /** Advance every clock; returns how many timers fired. */
  advance(ms: number): number {
    return this.move(ms, true);
  }

  /** Host sleep: wall and suspend-aware time move, monotonic does not. */
  suspend(ms: number): number {
    return this.move(ms, false);
  }

  sleep(ms: number): Promise<void> {
    return new Promise((resolve) => {
      this.callLater(ms, resolve);
    });
  }

  private move(ms: number, running: boolean): number {
    if (ms < 0) throw new RangeError('time only moves forward');
    const target = this.suspendAwareMs + ms;
    let fired = 0;
    for (let next = this.nextDue(target); next; next = this.nextDue(target)) {
      this.timers.splice(this.timers.indexOf(next), 1);
      this.set(next.deadline, running);
      next.callback();
      fired += 1;
    }
    this.set(target, running);
    return fired;
  }

  private nextDue(target: number): Timer | undefined {
    this.timers = this.timers.filter((t) => !t.cancelled);
    let best: Timer | undefined;
    for (const t of this.timers) {
      if (t.deadline > target) continue;
      if (
        !best ||
        t.deadline < best.deadline ||
        (t.deadline === best.deadline && t.seq < best.seq)
      ) {
        best = t;
      }
    }
    return best;
  }

  private set(suspendAwareMs: number, running: boolean): void {
    const step = suspendAwareMs - this.suspendAwareMs;
    this.suspendAwareMs = suspendAwareMs;
    this.wallMs += step;
    if (running) this.monotonicMs += step;
  }
}

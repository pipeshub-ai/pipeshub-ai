import { MAX_REVALIDATE_IDS, type NodeId } from '../types';

/**
 * How sure the view is that what it shows is still allowed.
 * ok: checked recently. degraded: two checks in a row failed, say so and keep the canvas.
 * blank: no check has succeeded for too long, so stop showing it. Showing stale data
 * indefinitely is the one outcome that is not accepted.
 */
export type VerifyState = 'ok' | 'degraded' | 'blank';

export interface RevalidatorOptions {
  /** The ids on screen now. */
  getIds: () => NodeId[];
  /** The server's answer for up to MAX_REVALIDATE_IDS ids: the ones still visible. Rejects on any failure. */
  call: (ids: NodeId[], signal?: AbortSignal) => Promise<NodeId[]>;
  /** `asked` is what was sent, so nodes added while the call ran are not mistaken for revoked ones. */
  onResult: (asked: NodeId[], visible: NodeId[]) => void;
  onState: (state: VerifyState) => void;
  intervalMs?: number;
  jitterMs?: number;
  failuresBeforeDegraded?: number;
  blankAfterMs?: number;
  /** A check that has not answered by then counts as a failure. */
  timeoutMs?: number;
  now?: () => number;
  random?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export const REVALIDATE_INTERVAL_MS = 30_000;
export const REVALIDATE_JITTER_MS = 5_000;
export const BLANK_AFTER_MS = 5 * 60_000;
export const CHECK_TIMEOUT_MS = 10_000;

/** Checks on a timer while the tab is visible, on focus (`poke`), and on demand. */
export class Revalidator {
  private readonly o: Required<RevalidatorOptions>;
  private timer: unknown = null;
  private running = false;
  private visible = true;
  private inFlight = false;
  private failures = 0;
  private lastOk: number;
  private state: VerifyState = 'ok';
  /** The ids of the last real check, kept to probe with while the view is blank. */
  private lastAsked: NodeId[] = [];

  constructor(options: RevalidatorOptions) {
    this.o = {
      intervalMs: REVALIDATE_INTERVAL_MS,
      jitterMs: REVALIDATE_JITTER_MS,
      failuresBeforeDegraded: 2,
      blankAfterMs: BLANK_AFTER_MS,
      timeoutMs: CHECK_TIMEOUT_MS,
      now: () => Date.now(),
      random: () => Math.random(),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
      ...options,
    };
    this.lastOk = this.o.now();
  }

  /** Counts stop()s: a check that finds the count changed was started in an earlier run. */
  private run = 0;
  private stopped = new AbortController();

  start(): void {
    if (this.running) return;
    this.running = true;
    this.lastOk = this.o.now();
    this.schedule();
  }

  stop(): void {
    this.running = false;
    this.run += 1; // a check still in flight belongs to a run that is over: it reports nothing
    this.stopped.abort();
    this.stopped = new AbortController();
    if (this.timer !== null) this.o.clearTimer(this.timer);
    this.timer = null;
  }

  /** The tab became visible or hidden. Hidden tabs do not check; becoming visible checks now. */
  setVisible(visible: boolean): void {
    this.visible = visible;
    if (visible) void this.poke();
  }

  /** Check now (focus, before eviction, after an expand). */
  async poke(): Promise<void> {
    if (!this.running || !this.visible || this.inFlight) return;
    if (this.timer !== null) this.o.clearTimer(this.timer);
    this.timer = null;
    await this.check();
    this.schedule();
  }

  private schedule(): void {
    if (!this.running || this.timer !== null) return;
    const wait = this.o.intervalMs + Math.floor(this.o.random() * this.o.jitterMs);
    this.timer = this.o.setTimer(() => {
      this.timer = null;
      void this.tick();
    }, wait);
  }

  private async tick(): Promise<void> {
    if (this.running && this.visible && !this.inFlight) await this.check();
    this.schedule();
  }

  private async check(): Promise<void> {
    let asked = this.o.getIds();
    // Remembered from the first look, not from the first success: if the service is down from the start there
    // has been no success, and the probe must still have something to ask about.
    if (asked.length > 0) this.lastAsked = asked.slice(0, 50);
    // Blank means the picture was cleared because no check worked. With nothing on screen there is nothing
    // to ask about, but "ok" must be earned by a check that works, so the last ids are the probe.
    if (asked.length === 0 && this.state === 'blank') asked = this.lastAsked;
    if (asked.length === 0) {
      this.lastOk = this.o.now();
      this.setState('ok');
      return;
    }
    this.inFlight = true;
    const run = this.run;
    try {
      const seen = new Set<NodeId>();
      for (let i = 0; i < asked.length; i += MAX_REVALIDATE_IDS) {
        for (const id of await this.callWithTimeout(asked.slice(i, i + MAX_REVALIDATE_IDS))) seen.add(id);
      }
      if (run !== this.run) return;
      this.failures = 0;
      this.lastOk = this.o.now();
      this.setState('ok');
      this.o.onResult(asked, asked.filter((id) => seen.has(id)));
    } catch {
      if (run !== this.run) return;
      this.failures += 1;
      if (this.o.now() - this.lastOk >= this.o.blankAfterMs) this.setState('blank');
      else if (this.failures >= this.o.failuresBeforeDegraded) this.setState('degraded');
    } finally {
      this.inFlight = false;
    }
  }

  /** One request that is given up on (and aborted) after `timeoutMs`, so a hung check cannot hold the loop for ever. */
  private async callWithTimeout(ids: NodeId[]): Promise<NodeId[]> {
    const abort = new AbortController();
    const stop = this.stopped.signal;
    const onStop = () => abort.abort();
    stop.addEventListener('abort', onStop);
    let timer: unknown;
    try {
      return await Promise.race([
        this.o.call(ids, abort.signal),
        new Promise<never>((_, reject) => {
          timer = this.o.setTimer(() => {
            abort.abort();
            reject(new Error('The access check timed out'));
          }, this.o.timeoutMs);
        }),
      ]);
    } finally {
      stop.removeEventListener('abort', onStop);
      this.o.clearTimer(timer);
    }
  }

  private setState(next: VerifyState): void {
    if (next === this.state) return;
    this.state = next;
    this.o.onState(next);
  }
}

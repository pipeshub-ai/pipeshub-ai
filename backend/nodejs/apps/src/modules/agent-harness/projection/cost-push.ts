import type { RunBudgetView } from '../application/run-spend';
import { AGUI, AGUI_CUSTOM, AguiFrame } from './frames';

/** At most one ledger read per this long while the run journals: its spend moves per model step, seconds apart. */
export const COST_PUSH_MIN_MS = 2_000;

export interface CostPushOptions {
  runId: string;
  /** The ledger's reading of the run's spend (`RunSpendReader.read`). */
  read: () => Promise<RunBudgetView | null>;
  /** Runs `step` on the stream's ordered chain, so the frame lands between whole projections. */
  schedule: (step: () => Promise<void>) => void;
  send: (frame: AguiFrame) => void;
  failed?: (error: unknown) => void;
  minMs?: number;
  now?: () => number;
}

/**
 * C10.05 (WP-100): the run's spend pushed on its stream rather than polled. The stream's projector stays a fold of
 * the journal: what it says of spend is the ledger's, read here when the run journals something (a model step, a
 * tool result, an engine's progress, a child's) and at most once per `minMs`, then sent only when it changed. The
 * first read is sent whatever it says (`budget: null` for a run with no account), so the client knows the stream
 * pushes and stops asking.
 *
 * A model step's charge reaches the ledger through the Model Gateway, not with the step's journal event, so either
 * can land first (WP100-FU-5): each burst of activity is read once more an interval after its last tick, and the
 * run's end reads it once more at once (`final`), so the shown cost never lags the run by a step.
 */
export class CostPush {
  private timer: NodeJS.Timeout | null = null;
  /** Reads still owed for the last tick: one at the interval, and one an interval after it. */
  private owed = 0;
  private lastRead = Number.NEGATIVE_INFINITY;
  private sent: string | null = null;
  private closed = false;
  private readonly minMs: number;
  private readonly now: () => number;

  constructor(private readonly options: CostPushOptions) {
    this.minMs = options.minMs ?? COST_PUSH_MIN_MS;
    this.now = options.now ?? Date.now;
  }

  /** The run did something that may have spent: read the ledger once the interval allows. */
  tick(): void {
    this.owed = 2;
    this.arm();
  }

  /** The run ended: its last cost is read now, whatever the interval says, and nothing after it. */
  async final(): Promise<void> {
    if (this.closed) return;
    this.owed = 0;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    await this.push();
  }

  private arm(): void {
    if (this.closed || this.timer !== null || this.owed === 0) return;
    const wait = Math.max(0, this.lastRead + this.minMs - this.now());
    this.timer = setTimeout(() => {
      this.timer = null;
      this.lastRead = this.now();
      this.owed -= 1;
      this.options.schedule(async () => {
        await this.push();
        this.arm();
      });
    }, wait);
    this.timer.unref();
  }

  close(): void {
    this.closed = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private async push(): Promise<void> {
    if (this.closed) return;
    let budget: RunBudgetView | null;
    try {
      budget = await this.options.read();
    } catch (error) {
      // Best effort: a ledger that cannot be read leaves the last cost shown, never the stream.
      this.options.failed?.(error);
      return;
    }
    const key = JSON.stringify(budget);
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- close() can run while the read above is awaited
    if (this.closed || key === this.sent) return;
    this.sent = key;
    this.options.send({
      type: AGUI.CUSTOM,
      name: AGUI_CUSTOM.RUN_COST,
      value: { runId: this.options.runId, budget },
      runId: this.options.runId,
    });
  }
}

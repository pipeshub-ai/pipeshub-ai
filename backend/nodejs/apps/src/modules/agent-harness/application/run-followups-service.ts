import { HarnessNotFoundError } from '../domain/errors';
import {
  Followup,
  followupsOf,
  MAX_FOLLOWUPS_WAIT_MS,
} from '../domain/followups';
import { SessionExecutionScope } from '../ports/chat-session-repository';
import { LiveBus, LiveBusSubscription } from '../ports/live-bus';
import { ActivityFrame } from '../projection/activity';
import { RunReader } from './harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { SessionRunService } from './session-run-service';

const STORE_POLL_MS = 1_000;

export interface FollowupTimers {
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const systemTimers: FollowupTimers = {
  setTimeout: (callback, ms) => setTimeout(callback, ms).unref(),
  clearTimeout: (handle) => {
    clearTimeout(handle as NodeJS.Timeout);
  },
};

export interface RunFollowupsDependencies {
  runs: Pick<SessionRunService, 'streamAccess'>;
  executions: RunReader;
  /** The activity bus, whose `followups` frame wakes a waiting read; absent: the store is polled. */
  bus: LiveBus<ActivityFrame> | null;
  timers?: FollowupTimers;
  logger?: HarnessLogger;
}

/**
 * A completed run's suggested next questions for its owner (UX5). The worker
 * stores them after the run's terminal commit, so a client that saw the run
 * end may wait a little for them; anyone but the run's actor is told the run
 * is not found, as on the activity panel.
 */
export class RunFollowupsService {
  private readonly timers: FollowupTimers;
  private readonly logger: HarnessLogger;

  constructor(private readonly deps: RunFollowupsDependencies) {
    this.timers = deps.timers ?? systemTimers;
    this.logger = deps.logger ?? silentHarnessLogger;
  }

  async read(
    scope: SessionExecutionScope,
    runId: string,
    waitMs: number,
    signal?: AbortSignal,
  ): Promise<Followup[]> {
    const { run, access } = await this.deps.runs.streamAccess(scope, runId);
    if (access.audience !== 'owner') throw new HarnessNotFoundError();
    const stored = run.followups ?? [];
    const settled = run.status === 'failed' || run.status === 'cancelled';
    if (stored.length > 0 || settled || waitMs <= 0) return stored;
    return new FollowupsWait(
      this.deps,
      this.timers,
      this.logger,
      run.orgId,
      runId,
      Math.min(waitMs, MAX_FOLLOWUPS_WAIT_MS),
      signal,
    ).result;
  }
}

/** One waiting read: answered by the store, a `followups` frame, or the deadline. */
class FollowupsWait {
  readonly result: Promise<Followup[]>;
  private resolve!: (followups: Followup[]) => void;
  private done = false;
  private reading = false;
  private sub: LiveBusSubscription | null = null;
  private poll: unknown = null;
  private deadline: unknown = null;

  constructor(
    private readonly deps: RunFollowupsDependencies,
    private readonly timers: FollowupTimers,
    private readonly logger: HarnessLogger,
    private readonly orgId: string,
    private readonly runId: string,
    waitMs: number,
    private readonly signal: AbortSignal | undefined,
  ) {
    this.result = new Promise((resolve) => {
      this.resolve = resolve;
    });
    if (signal?.aborted === true) {
      this.finish([]);
      return;
    }
    signal?.addEventListener('abort', this.onAbort);
    this.deadline = timers.setTimeout(() => {
      void this.check(true);
    }, waitMs);
    void this.start();
  }

  private readonly onAbort = (): void => {
    this.finish([]);
  };

  private async start(): Promise<void> {
    const bus = this.deps.bus;
    if (bus !== null) {
      try {
        const retained = await bus.read(this.runId);
        if (this.done) return;
        const offered = this.offered(retained.deltas);
        if (offered !== null) {
          await this.wake(offered);
          return;
        }
        this.sub = bus.follow(
          this.runId,
          retained.cursor,
          (frames) => {
            const late = this.offered(frames);
            if (late !== null) void this.wake(late);
          },
          (error) => {
            this.busDown(error);
          },
        );
      } catch (error) {
        this.busDown(error);
      }
    }
    // Stored between the first read and the follow.
    await this.check(false);
    this.schedulePoll();
  }

  private offered(frames: readonly ActivityFrame[]): Followup[] | null {
    for (const frame of frames) {
      if (frame.kind !== 'followups') continue;
      const followups = followupsOf(frame.data);
      if (followups !== null) return followups;
    }
    return null;
  }

  /** The store is the authority; the frame's items answer if the worker published before storing. */
  private async wake(offered: Followup[]): Promise<void> {
    const stored = await this.stored();
    this.finish(stored.length > 0 ? stored : offered);
  }

  private schedulePoll(): void {
    if (this.done) return;
    this.poll = this.timers.setTimeout(() => {
      this.poll = null;
      void this.check(false).then(() => {
        this.schedulePoll();
      });
    }, STORE_POLL_MS);
  }

  private async check(final: boolean): Promise<void> {
    if (this.done || (this.reading && !final)) return;
    this.reading = true;
    try {
      const stored = await this.stored();
      if (stored.length > 0 || final) this.finish(stored);
    } finally {
      this.reading = false;
    }
  }

  private async stored(): Promise<Followup[]> {
    try {
      const run = await this.deps.executions.get(this.orgId, this.runId);
      return run?.followups ?? [];
    } catch (error) {
      this.logger.warn(
        'followups.read_failed',
        harnessLogMeta('followups.read_failed', {
          orgId: this.orgId,
          runId: this.runId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return [];
    }
  }

  private busDown(error: unknown): void {
    this.sub?.close();
    this.sub = null;
    this.logger.warn(
      'followups.livebus_unavailable',
      harnessLogMeta('followups.livebus_unavailable', {
        orgId: this.orgId,
        runId: this.runId,
        errorType: error instanceof Error ? error.name : typeof error,
        choice: 'poll_store',
      }),
    );
  }

  private finish(followups: Followup[]): void {
    if (this.done) return;
    this.done = true;
    this.sub?.close();
    this.sub = null;
    if (this.poll !== null) this.timers.clearTimeout(this.poll);
    if (this.deadline !== null) this.timers.clearTimeout(this.deadline);
    this.signal?.removeEventListener('abort', this.onAbort);
    this.resolve(followups);
  }
}

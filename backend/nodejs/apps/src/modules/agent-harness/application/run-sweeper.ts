import {
  ControlPlaneLiveness,
  LivenessObservation,
} from '../ports/control-plane-liveness';
import { HarnessRun } from '../domain/execution';
import { LeaderLease } from '../ports/leader-lease';
import { PendingReaps, SandboxReaper } from '../ports/sandbox-reaper';
import { erasureBackoffMs } from '../domain/session-erasure';
import { SweepReason } from '../ports/execution-store';
import type { WaitingRunNotices } from '../ports/approval-links';
import { ExecutionService } from './execution-service';
import { MemoryService } from './memory-service';
import type { OperatorStops } from '../ports/operator-stops';
import { NextRunService } from './next-run-service';
import { harnessLogMeta, HarnessLogger } from './harness-log';
import { detachedInterval } from '../../../libs/context/orgContext';

/**
 * The sandboxes a swept run may have left (E1): the expired attempt's when a
 * lease lapsed (the next attempt makes its own), every one of the run's when
 * it ended. Runs an engine executes hold sandboxes, and so do loop runs
 * whose `run_code` uses the broker (`agent_code`, P5.11).
 */
export function reapLabels(
  run: HarnessRun,
  reason: SweepReason,
): Record<string, string> | null {
  const features = run.requires?.features ?? [];
  if (
    !features.includes('executor_engine') &&
    !features.includes('agent_code')
  ) {
    return null;
  }
  const labels: Record<string, string> = {
    orgId: run.orgId,
    runId: run.runId,
  };
  if (reason === 'lease_expired' && run.attemptId !== null) {
    labels['attemptId'] = run.attemptId;
  }
  return labels;
}

export const SWEEP_INTERVAL_MS = 5_000;
/** Runs transitioned per tick; a larger backlog drains over later ticks. */
export const SWEEP_BATCH_LIMIT = 100;
/**
 * How long a leader that died without releasing keeps the others out, so the
 * takeover a crash costs. Two missed renewals (a slow store, a long tick) keep
 * the lease; a lapse under a live leader is harmless, since every sweep
 * transition is a guarded write and two sweepers expire a run once.
 */
export const SWEEPER_LEASE_MS = 3 * SWEEP_INTERVAL_MS;
export const SWEEPER_LEASE_NAME = 'agent-harness-run-sweeper';
/**
 * The longest silence a normal leader handover causes: the old leader's lease
 * lapses, then the next replica ticks. Longer means no Node swept at all (an
 * outage, a sleeping host) or the DB clock jumped.
 */
export const LIVENESS_GAP_MS = SWEEPER_LEASE_MS + 2 * SWEEP_INTERVAL_MS;

/**
 * A gap in control-plane liveness, if this observation ends one.
 *
 * It is logged, not waited out (C-13). A lease that lapsed during the gap
 * cannot be renewed (DP-6, P2), and its worker stopped acting on it before it
 * lapsed (the worker's `validUntil` precedes the stored lease); fencing keeps
 * a late commit out, as for any expired lease. Holding expiry back would only
 * delay recovery past SLO #6. DP-6 brings back a holdoff of one renew period,
 * so a live worker can renew what the gap let lapse.
 */
export function controlPlaneGap(
  observation: LivenessObservation,
): { gapMs: number } | null {
  if (observation.previousSeenAt === null) return null;
  const gapMs = observation.now - observation.previousSeenAt;
  return gapMs > LIVENESS_GAP_MS ? { gapMs } : null;
}

export interface RunSweeperOptions {
  executions: Pick<ExecutionService, 'sweep'>;
  lease: LeaderLease;
  liveness: ControlPlaneLiveness;
  logger: HarnessLogger;
  holderId: string;
  /** The sandbox broker (E1); absent: sandboxes of ended runs wait for their next attempt or the broker. */
  reaper?: SandboxReaper | null;
  /** Next runs (SS2) whose drain at their run's end was missed (a crash, a lost hint). */
  nextRuns?: Pick<NextRunService, 'drainDue'> | null;
  /** Memory proposals whose settlement at their run's end was missed (a crash). */
  memories?: Pick<MemoryService, 'settleDue'> | null;
  /** Where refused reaps wait to be asked again; absent: a refused reap is only logged. */
  pendingReaps?: PendingReaps | null;
  /** WP56-OUTBOX: notices about waiting runs a restart or a failed send left. */
  approverNotices?: Pick<WaitingRunNotices, 'sweepDue'> | null;
  /** ADR-OPS-01 D4: every operator control re-applied each tick, which bounds a stop at 60 s. */
  operatorControls?: Pick<OperatorStops, 'enforceAll'> | null;
}

/**
 * Expires runs nobody else will: lapsed worker leases, and idle runs
 * (queued, waiting for approval, paused, ...) past their absolute deadline,
 * which would otherwise hold their session's active key forever. One replica
 * sweeps at a time, elected through the lease; a gap in control-plane
 * liveness is logged, and a page of at most `SWEEP_BATCH_LIMIT` per tick
 * bounds the recovery that follows it.
 */
export class RunSweeper {
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private heldOff = false;
  private leading = false;

  private readonly executions: Pick<ExecutionService, 'sweep'>;
  private readonly lease: LeaderLease;
  private readonly liveness: ControlPlaneLiveness;
  private readonly logger: HarnessLogger;
  private readonly holderId: string;
  private readonly reaper: SandboxReaper | null;
  private readonly nextRuns: Pick<NextRunService, 'drainDue'> | null;
  private readonly memories: Pick<MemoryService, 'settleDue'> | null;
  private readonly pendingReaps: PendingReaps | null;
  private readonly approverNotices: Pick<WaitingRunNotices, 'sweepDue'> | null;
  private readonly operatorControls: Pick<OperatorStops, 'enforceAll'> | null;

  constructor(options: RunSweeperOptions) {
    this.operatorControls = options.operatorControls ?? null;
    this.approverNotices = options.approverNotices ?? null;
    this.pendingReaps = options.pendingReaps ?? null;
    this.executions = options.executions;
    this.lease = options.lease;
    this.liveness = options.liveness;
    this.logger = options.logger;
    this.holderId = options.holderId;
    this.reaper = options.reaper ?? null;
    this.nextRuns = options.nextRuns ?? null;
    this.memories = options.memories ?? null;
  }

  /** Ticks at once: a starting replica takes a free lease now, not an interval later. */
  start(): void {
    if (this.timer) return;
    this.timer = detachedInterval(() => {
      void this.tick();
    }, SWEEP_INTERVAL_MS);
    void this.tick();
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    while (this.running) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    try {
      await this.lease.release(SWEEPER_LEASE_NAME, this.holderId);
      this.leads(false, 'sweeper.leader_released');
    } catch (error) {
      this.logger.error(
        'sweeper.lease_release_failed',
        harnessLogMeta('sweeper.lease_release_failed', {
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  /** Logged when leadership changes hands, never per tick. */
  private leads(
    leading: boolean,
    event:
      | 'sweeper.leader_acquired'
      | 'sweeper.leader_lost'
      | 'sweeper.leader_released',
  ): void {
    if (this.leading === leading) return;
    this.leading = leading;
    this.logger.info(event, harnessLogMeta(event, { holder: this.holderId }));
  }

  async tick(): Promise<number> {
    if (this.running) return 0;
    this.running = true;
    try {
      const leader = await this.lease.tryAcquire(
        SWEEPER_LEASE_NAME,
        this.holderId,
        SWEEPER_LEASE_MS,
      );
      this.leads(
        leader,
        leader ? 'sweeper.leader_acquired' : 'sweeper.leader_lost',
      );
      if (!leader) return 0;
      if (await this.holdingOff()) return 0;
      // First: a stop must not wait behind a slow sweep page.
      if (this.operatorControls !== null) {
        await this.operatorControls.enforceAll();
      }
      const reaping: Array<Record<string, string>> = [];
      const swept =
        this.reaper === null
          ? await this.executions.sweep(SWEEP_BATCH_LIMIT)
          : await this.executions.sweep(SWEEP_BATCH_LIMIT, (run, reason) => {
              const labels = reapLabels(run, reason);
              if (labels !== null) reaping.push(labels);
            });
      await this.reap(reaping);
      const drained =
        this.nextRuns === null
          ? 0
          : await this.nextRuns.drainDue(SWEEP_BATCH_LIMIT);
      if (drained > 0) {
        this.logger.info(
          'sweeper.next_runs_admitted',
          harnessLogMeta('sweeper.next_runs_admitted', { admitted: drained }),
        );
      }
      const settled =
        this.memories === null
          ? 0
          : await this.memories.settleDue(SWEEP_BATCH_LIMIT);
      if (settled > 0) {
        this.logger.info(
          'sweeper.memories_settled',
          harnessLogMeta('sweeper.memories_settled', { runs: settled }),
        );
      }
      const noticed =
        this.approverNotices === null
          ? 0
          : await this.approverNotices.sweepDue(SWEEP_BATCH_LIMIT);
      if (noticed > 0) {
        this.logger.info(
          'sweeper.approver_notices_sent',
          harnessLogMeta('sweeper.approver_notices_sent', { sent: noticed }),
        );
      }
      if (swept > 0) {
        this.logger.info(
          'sweeper.swept',
          harnessLogMeta('sweeper.swept', {
            swept,
            limit: SWEEP_BATCH_LIMIT,
          }),
        );
      }
      return swept;
    } catch (error) {
      this.logger.error(
        'sweeper.tick_failed',
        harnessLogMeta('sweeper.tick_failed', {
          errorType: error instanceof Error ? error.name : typeof error,
          error: error instanceof Error ? error.message : String(error),
        }),
      );
      return 0;
    } finally {
      this.running = false;
    }
  }

  /**
   * One reap per swept engine run, then the refused ones that are due again.
   * A failure never fails the sweep; it is kept and asked for again with
   * backoff (CLEANUP-G6), since an ended run has no next attempt to reap it.
   */
  private async reap(reaping: Array<Record<string, string>>): Promise<void> {
    const reaper = this.reaper;
    if (reaper === null) return;
    const pending = this.pendingReaps;
    for (const labels of reaping) {
      const failed = await this.reapOnce(reaper, labels, 0);
      if (failed !== null && pending !== null) {
        await pending.defer(
          labels,
          (await pending.now()) + erasureBackoffMs(1),
          failed,
        );
      }
    }
    if (pending === null) return;
    const now = await pending.now();
    for (const due of await pending.due(now, SWEEP_BATCH_LIMIT)) {
      const failed = await this.reapOnce(reaper, due.labels, due.failures);
      if (failed === null) {
        await pending.done(due.id);
      } else {
        await pending.later(
          due.id,
          now + erasureBackoffMs(due.failures + 1),
          failed,
        );
      }
    }
  }

  /** null when the broker answered; otherwise the error's type. */
  private async reapOnce(
    reaper: SandboxReaper,
    labels: Readonly<Record<string, string>>,
    failures: number,
  ): Promise<string | null> {
    try {
      const reaped = await reaper.reap(labels);
      this.logger.info(
        'sweeper.reaped',
        harnessLogMeta('sweeper.reaped', {
          orgId: labels['orgId'],
          runId: labels['runId'],
          attemptId: labels['attemptId'] ?? null,
          sandboxes: reaped,
          retried: failures > 0,
        }),
      );
      return null;
    } catch (error) {
      const errorType = error instanceof Error ? error.name : typeof error;
      this.logger.warn(
        'sweeper.reap_failed',
        harnessLogMeta('sweeper.reap_failed', {
          orgId: labels['orgId'],
          runId: labels['runId'],
          reason: 'broker_unavailable',
          errorType,
          failures: failures + 1,
        }),
      );
      return errorType;
    }
  }

  /** An explicit holdoff in the shared liveness record (e.g. an older Node's during a rolling deploy) holds. */
  private async holdingOff(): Promise<boolean> {
    const observation = await this.liveness.observe();
    const gap = controlPlaneGap(observation);
    if (gap !== null) {
      this.logger.warn(
        'sweeper.gap',
        harnessLogMeta('sweeper.gap', {
          reason: 'control_plane_gap',
          gapMs: gap.gapMs,
          holdoffMs: 0,
        }),
      );
    }
    if (observation.now < observation.holdoffUntil) {
      this.heldOff = true;
      return true;
    }
    if (this.heldOff) {
      this.heldOff = false;
      this.logger.info(
        'sweeper.holdoff_ended',
        harnessLogMeta('sweeper.holdoff_ended', {
          at: new Date(observation.now).toISOString(),
        }),
      );
    }
    return false;
  }
}

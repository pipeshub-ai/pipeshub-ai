import type {
  HistoricalGroupFormat,
  IdleGroupSweep,
  IMessageAdmin,
} from '../../../libs/types/messaging.types';
import { LeaderLease } from '../ports/leader-lease';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { detachedInterval } from '../../../libs/context/orgContext';

export const LEFTOVER_SWEEP_INTERVAL_MS = 60_000;
export const LEFTOVER_SWEEP_LEASE_MS = 3 * LEFTOVER_SWEEP_INTERVAL_MS;
export const LEFTOVER_SWEEP_LEASE_NAME = 'agent-harness-leftover-groups';
/**
 * A subscriber of an earlier build asks Redis for messages every 2 s, and an
 * orchestrator restarts a process that stops answering within a minute, so a
 * group nobody has asked from for ten minutes belongs to no running process.
 * Removing one too early would cost that older process its change hints; it
 * still reloads on its own period.
 */
export const LEFTOVER_GROUP_IDLE_MS = 10 * 60_000;

/**
 * The per-process consumer groups earlier builds made for their fan-out
 * subscriptions, as those builds named them and the consumer each read with.
 * Subscribers hold no group now, so this list only shrinks. A name of the
 * same shape whose consumer is named otherwise is someone else's.
 */
export const HISTORICAL_GROUP_FORMATS = {
  // CPD-6's Node: `control-plane-events-<hostname>-<pid>`, read by a consumer of the same name.
  node: {
    name: /^control-plane-events-[A-Za-z0-9][A-Za-z0-9.-]{0,252}-[1-9][0-9]{0,6}$/,
    consumer: (group: string): string => group,
  },
  // CPD-6's worker, inline claimer and model gateway: `control_plane_events_group-<pid>-<8 hex>`.
  pythonControlPlane: {
    name: /^control_plane_events_group-[1-9][0-9]{0,6}-[0-9a-f]{8}$/,
    consumer: (): string => 'control_plane_events_client',
  },
  // The query service's AI-config subscription: `aiconfig_consumer_group-<pid>-<8 hex>`.
  pythonAiConfig: {
    name: /^aiconfig_consumer_group-[1-9][0-9]{0,6}-[0-9a-f]{8}$/,
    consumer: (): string => 'aiconfig_consumer_client',
  },
} as const satisfies Record<string, HistoricalGroupFormat>;

export const LEFTOVER_GROUPS: ReadonlyArray<
  Pick<IdleGroupSweep, 'topic' | 'formats'>
> = [
  {
    topic: 'control-plane-events',
    formats: [
      HISTORICAL_GROUP_FORMATS.node,
      HISTORICAL_GROUP_FORMATS.pythonControlPlane,
    ],
  },
  {
    topic: 'ai-config-events',
    formats: [HISTORICAL_GROUP_FORMATS.pythonAiConfig],
  },
];

export interface LeftoverGroupSweeperOptions {
  admin: Pick<IMessageAdmin, 'removeIdleGroups'>;
  lease: LeaderLease;
  holderId: string;
  logger?: HarnessLogger;
  groups?: ReadonlyArray<Pick<IdleGroupSweep, 'topic' | 'formats'>>;
  idleMs?: number;
}

/** Removes those groups once their process is gone; one replica at a time, elected through the lease. */
export class LeftoverGroupSweeper {
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  private readonly admin: Pick<IMessageAdmin, 'removeIdleGroups'>;
  private readonly lease: LeaderLease;
  private readonly holderId: string;
  private readonly logger: HarnessLogger;
  private readonly groups: ReadonlyArray<
    Pick<IdleGroupSweep, 'topic' | 'formats'>
  >;
  /** Groups already named as left alone, so each is logged once per process. */
  private readonly reported = new Set<string>();
  private readonly idleMs: number;

  constructor(options: LeftoverGroupSweeperOptions) {
    this.admin = options.admin;
    this.lease = options.lease;
    this.holderId = options.holderId;
    this.logger = options.logger ?? silentHarnessLogger;
    this.groups = options.groups ?? LEFTOVER_GROUPS;
    this.idleMs = options.idleMs ?? LEFTOVER_GROUP_IDLE_MS;
  }

  start(intervalMs = LEFTOVER_SWEEP_INTERVAL_MS): void {
    if (this.timer) return;
    this.timer = detachedInterval(() => {
      void this.tick();
    }, intervalMs);
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    while (this.running) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    try {
      await this.lease.release(LEFTOVER_SWEEP_LEASE_NAME, this.holderId);
    } catch (error) {
      this.logger.warn(
        'broker.leftover_groups_lease_release_failed',
        harnessLogMeta('broker.leftover_groups_lease_release_failed', {
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  private reportLeft(topic: string, left: string[]): void {
    const fresh = left.filter(
      (group) => !this.reported.has(`${topic}\n${group}`),
    );
    if (fresh.length === 0) return;
    for (const group of fresh) this.reported.add(`${topic}\n${group}`);
    this.logger.info(
      'broker.leftover_groups_left',
      harnessLogMeta('broker.leftover_groups_left', {
        topic,
        groups: fresh,
        reason: 'not_an_earlier_build_group',
      }),
    );
  }

  /** One pass over the declared groups; returns the names removed. */
  async tick(): Promise<string[]> {
    if (this.running) return [];
    this.running = true;
    const removed: string[] = [];
    try {
      const leader = await this.lease.tryAcquire(
        LEFTOVER_SWEEP_LEASE_NAME,
        this.holderId,
        LEFTOVER_SWEEP_LEASE_MS,
      );
      if (!leader) return removed;
      for (const { topic, formats } of this.groups) {
        const { removed: gone, left } = await this.admin.removeIdleGroups({
          topic,
          formats,
          idleMs: this.idleMs,
        });
        this.reportLeft(topic, left);
        if (gone.length === 0) continue;
        removed.push(...gone);
        this.logger.info(
          'broker.leftover_groups_removed',
          harnessLogMeta('broker.leftover_groups_removed', {
            topic,
            groups: gone,
          }),
        );
      }
    } catch (error) {
      this.logger.warn(
        'broker.leftover_groups_sweep_failed',
        harnessLogMeta('broker.leftover_groups_sweep_failed', {
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    } finally {
      this.running = false;
    }
    return removed;
  }
}

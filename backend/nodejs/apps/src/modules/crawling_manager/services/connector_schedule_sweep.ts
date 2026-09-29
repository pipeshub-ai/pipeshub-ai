import { Queue, Worker } from 'bullmq';
import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { RedisConfig } from '../../../libs/types/redis.types';
import {
  runWithRequestContext,
  newSystemRoot,
} from '../../../libs/context/request-context';
import { AppConfig } from '../../tokens_manager/config/config';
import { CrawlingSchedulerService } from './crawling_service';
import { crawlingQueuePrefix, crawlingRedisProvider } from './crawling_queue';
import { reconcileConnectorSchedule } from './connector_schedule_orchestrator';
import * as scheduledConnectorsClient from '../utils/scheduled_connectors_client';

// Kept off 'crawling-scheduler': that worker treats every job as a crawl.
export const SWEEP_QUEUE_NAME = 'connector-schedule-sweep';
const SWEEP_JOB_NAME = 'sweep';
const SWEEP_STARTUP_JOB_ID = 'sweep-startup';
const DEFAULT_SWEEP_INTERVAL_MINUTES = 15;

export interface SweepSummary {
  checked: number;
  repaired: number;
  driftFixed: number;
  orphansRemoved: number;
  errors: number;
  aborted: boolean;
  durationMs: number;
}

export const sweepIntervalMinutes = (): number => {
  const parsed = parseInt(
    process.env.CONNECTOR_SCHEDULE_SWEEP_INTERVAL_MINUTES ??
      String(DEFAULT_SWEEP_INTERVAL_MINUTES),
    10,
  );
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
};

/**
 * Repairs connector crawl schedules that the in-request reconcile failed to
 * create, drifted, or left behind. Runs as a repeatable BullMQ job on its own
 * queue so only one Node replica sweeps per tick.
 */
@injectable()
export class ConnectorScheduleSweepService {
  private readonly logger = Logger.getInstance({
    service: 'ConnectorScheduleSweepService',
  });
  private queue: Queue | null = null;
  private worker: Worker | null = null;

  constructor(
    @inject(CrawlingSchedulerService)
    private readonly scheduler: CrawlingSchedulerService,
    @inject('AppConfig') private readonly appConfig: AppConfig,
    @inject('RedisConfig') private readonly redisConfig: RedisConfig,
  ) {}

  async start(): Promise<void> {
    const intervalMinutes = sweepIntervalMinutes();
    if (intervalMinutes === 0) {
      this.logger.info('Connector schedule sweep disabled');
      return;
    }

    try {
      const provider = crawlingRedisProvider(this.redisConfig);
      const prefix = crawlingQueuePrefix(provider);
      this.queue = new Queue(SWEEP_QUEUE_NAME, {
        connection: provider.createClient({ blocking: true }),
        prefix,
        defaultJobOptions: { removeOnComplete: 10, removeOnFail: 10 },
      });
      this.worker = new Worker(
        SWEEP_QUEUE_NAME,
        async () => {
          await runWithRequestContext({ rootId: newSystemRoot() }, () =>
            this.sweep(),
          );
        },
        {
          connection: provider.createClient({ blocking: true }),
          prefix,
          concurrency: 1,
        },
      );

      // Idempotent across replicas and restarts; a changed interval replaces the old one.
      await this.queue.upsertJobScheduler(
        SWEEP_JOB_NAME,
        { every: intervalMinutes * 60_000 },
        { name: SWEEP_JOB_NAME },
      );
      // Repairs state after a Redis flush or deploy without waiting a full tick.
      await this.queue.add(
        SWEEP_JOB_NAME,
        {},
        { jobId: SWEEP_STARTUP_JOB_ID, removeOnComplete: true, removeOnFail: true },
      );
      this.logger.info('Connector schedule sweep started', { intervalMinutes });
    } catch (error) {
      // Defensive job only; never block app startup on it.
      this.logger.error('Failed to start connector schedule sweep', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async sweep(): Promise<SweepSummary> {
    const started = Date.now();
    const summary: SweepSummary = {
      checked: 0,
      repaired: 0,
      driftFixed: 0,
      orphansRemoved: 0,
      errors: 0,
      aborted: false,
      durationMs: 0,
    };
    const finish = (): SweepSummary => {
      summary.durationMs = Date.now() - started;
      this.logger.info('Connector schedule sweep finished', { ...summary });
      return summary;
    };

    let desired: scheduledConnectorsClient.ScheduledConnectorRecord[];
    let snapshot: Awaited<ReturnType<CrawlingSchedulerService['listRepeatableSchedules']>>;
    try {
      // Snapshot before fetching: a schedule a user creates mid-sweep is not in
      // it, so it can never be mistaken for an orphan.
      snapshot = await this.scheduler.listRepeatableSchedules();
      desired = await scheduledConnectorsClient.fetchAllScheduledConnectors(
        this.appConfig,
      );
    } catch (error) {
      this.logger.error('Connector schedule sweep aborted; nothing changed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      summary.aborted = true;
      return finish();
    }

    const desiredNames = new Set<string>();
    for (const item of desired) {
      summary.checked++;
      const ctx = {
        connectorId: item.connectorId,
        type: item.type,
        orgId: item.orgId,
      };
      try {
        const name = this.scheduler.jobNameFor(item.type, item.connectorId);
        desiredNames.add(name);

        const intervalMinutes = Number(item.sync.scheduledConfig?.intervalMinutes);
        if (!Number.isFinite(intervalMinutes) || intervalMinutes < 1) {
          this.logger.warn('Sweep skipped connector with an invalid interval', ctx);
          continue;
        }
        const every = Math.floor(intervalMinutes) * 60_000;
        // `every` only: BullMQ ignores tz for interval jobs, and comparing it
        // would reschedule on every sweep whenever the stored tz is normalised.
        const current = snapshot.filter((r) => r.name === name);
        const matching = current.filter((r) => Number(r.every) === every);
        const stale = current.filter((r) => Number(r.every) !== every);

        for (const r of stale) {
          await this.scheduler.removeRepeatableByKey(r.key);
        }
        if (matching.length > 0) {
          continue;
        }

        const outcome = await reconcileConnectorSchedule(this.scheduler, this.logger, {
          connector: item.type,
          connectorId: item.connectorId,
          orgId: item.orgId,
          userId:
            (typeof item.ownerUserId === 'string' && item.ownerUserId) || 'system',
          isActive: true,
          sync: item.sync,
        });
        if (outcome !== 'scheduled') {
          continue;
        }
        if (stale.length > 0) {
          summary.driftFixed++;
          this.logger.warn('Sweep rescheduled connector with a stale interval', ctx);
        } else {
          summary.repaired++;
          this.logger.warn('Sweep recreated missing connector schedule', ctx);
        }
      } catch (error) {
        summary.errors++;
        this.logger.error('Sweep failed for connector', {
          ...ctx,
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }

    if (desired.length === 0 && snapshot.length > 0) {
      // An empty list with live schedules is more likely a read failure than
      // every connector being disabled; disabling removes its job anyway.
      this.logger.warn(
        'Sweep got no scheduled connectors but schedules exist; skipping orphan removal',
        { schedules: snapshot.length },
      );
      return finish();
    }

    for (const r of snapshot) {
      if (desiredNames.has(r.name)) {
        continue;
      }
      try {
        await this.scheduler.removeRepeatableByKey(r.key);
        summary.orphansRemoved++;
        this.logger.warn('Sweep removed orphaned connector schedule', {
          name: r.name,
          key: r.key,
        });
      } catch (error) {
        summary.errors++;
        this.logger.error('Sweep failed to remove orphaned schedule', {
          name: r.name,
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }

    return finish();
  }

  async close(): Promise<void> {
    await this.worker?.close();
    await this.queue?.close();
    this.worker = null;
    this.queue = null;
  }
}

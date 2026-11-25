import { Worker, Job, WorkerOptions, JobProgress } from 'bullmq';
import { PgJobWorker } from '../../../libs/services/pg-job-queue.service';
import { usesPgScheduler } from './crawling_pg';
import { Logger } from '../../../libs/services/logger.service';
import { inject, injectable } from 'inversify';
import { RedisConfig } from '../../../libs/types/redis.types';
import { CrawlingJobData } from '../schema/interface';
import { ConnectorsCrawlingService } from './connectors/connectors';
import {
  CrawlingResult,
  ICrawlingTaskService,
} from './task/crawling_task_service';
import { crawlingQueuePrefix, crawlingRedisProvider } from './crawling_queue';
import {
  bindLogContext,
  runWithRequestContext,
  newSystemRoot,
  setContextIdentity,
} from '../../../libs/context/request-context';
import { classifyError, levelFor } from '../../../libs/utils/error-class';
import { getOrgRepository } from '../../../libs/repositories/org';
import {
  ambientOrg,
  forOrg,
  spawnDetached,
} from '../../../libs/context/orgContext';

@injectable()
export class CrawlingWorkerService {
  private worker: Worker;
  private readonly logger: Logger;

  constructor(
    @inject('RedisConfig') redisConfig: RedisConfig,
    @inject(ConnectorsCrawlingService)
    private taskService: ICrawlingTaskService,
  ) {
    this.logger = Logger.getInstance({ service: 'CrawlingWorkerService' });

    // A lazily resolved service may be constructed inside a request; its
    // sockets and timers must not keep that request's org context.
    this.worker = spawnDetached(() => {
      if (usesPgScheduler()) {
        // no Redis: the same worker contract on PostgreSQL (claimed with SKIP LOCKED, woken by NOTIFY)
        return new PgJobWorker(
          'crawling-scheduler',
          this.processJob.bind(this) as never,
          { concurrency: 5, maxStalledCount: 3, stalledInterval: 30000 },
        ) as unknown as Worker;
      }
      const provider = crawlingRedisProvider(redisConfig);

      const workerOptions: WorkerOptions = {
        // Dedicated blocking-capable connection; BullMQ workers block on it
        // for job availability and must not share the provider's pooled client.
        connection: provider.createClient({ blocking: true }),
        prefix: crawlingQueuePrefix(provider),
        concurrency: 5, // Process up to 5 jobs concurrently
        maxStalledCount: 3,
        stalledInterval: 30000, // 30 seconds
      };

      return new Worker(
        'crawling-scheduler', // Same queue name as in scheduler service
        this.processJob.bind(this),
        workerOptions,
      );
    });

    this.setupWorkerListeners();
    this.logger.info('CrawlingWorkerService initialized');
  }

  private async processJob(job: Job<CrawlingJobData>): Promise<void> {
    // Scheduled crawl tick — seed a fresh trace root. Every line of the run, the skips included, names the connector.
    return runWithRequestContext({ rootId: newSystemRoot() }, async () => {
      bindLogContext({
        connector_id: job.data.connectorId,
        connector_type: job.data.connector,
      });
      const started = Date.now();
      // BullMQ counts the attempts already made; each retry is its own run with its own line.
      const attempt = job.attemptsMade + 1;
      try {
        const result = await this.processJobForOrg(job);
        // The run's one line (docs/logging.md "Crawling jobs (Node)"). A job dropped before it ran (its org is gone or
        // not an id) has its own WARNING instead.
        if (result) {
          this.logger.info('crawling_job.end', {
            outcome: result.skipped ? 'skipped' : 'success',
            ...(result.skipped && { reason: result.skipped }),
            duration_ms: Date.now() - started,
            attempt,
            job_id: job.id,
            event_name: 'crawling_job.end',
          });
        }
      } catch (error) {
        // Every failure of the run, the org lookup's included, is this one line.
        const errorClass = classifyError(error);
        this.logger[levelFor(errorClass)]('crawling_job.end', {
          outcome: 'failure',
          error_class: errorClass,
          duration_ms: Date.now() - started,
          attempt,
          job_id: job.id,
          error,
          event_name: 'crawling_job.end',
        });
        throw error; // This will mark the job as failed and trigger retries
      }
    });
  }

  /** Bind the job's org only if it still exists and is active; a gone org's job is dropped (undefined). */
  private async processJobForOrg(
    job: Job<CrawlingJobData>,
  ): Promise<CrawlingResult | undefined> {
    const { orgId } = job.data;
    if (!orgId) return this.processJobInContext(job);
    if (!/^[0-9a-f]{24}$/i.test(orgId)) {
      // Not an ObjectId (for example the "system" bootstrap id): drop, a retry cannot fix it.
      this.logger.warn('Skipping crawling job: org id is not valid', {
        job_id: job.id,
        org_id: orgId,
      });
      return undefined;
    }
    setContextIdentity({ orgId });
    // inside the org's context: the PostgreSQL store reads an org only as that org
    return forOrg(orgId, 'crawl', async () => {
      if (!(await getOrgRepository().isActive(orgId))) {
        this.logger.warn(
          'Skipping crawling job: org does not exist or is not active',
          { job_id: job.id },
        );
        return undefined;
      }
      return this.processJobInContext(job);
    });
  }

  private async processJobInContext(
    job: Job<CrawlingJobData>,
  ): Promise<CrawlingResult> {
    const { orgId, userId, scheduleConfig, connector, connectorId } = job.data;

    // Update job progress
    await job.updateProgress(10);

    await job.updateProgress(20);

    // Execute the crawling task with connector information
    const result = await this.taskService.crawl(
      ambientOrg(orgId, 'CrawlingWorker.crawl') ?? orgId,
      userId,
      scheduleConfig,
      connector,
      connectorId,
    );

    await job.updateProgress(100);
    return result;
  }

  private setupWorkerListeners(): void {
    // A run's outcome is its crawling_job.end line; BullMQ's own 'failed' event repeats it (or follows a stall, which the
    // 'stalled' line reports), so it is DEBUG.
    this.worker.on('failed', (job: Job | undefined) => {
      this.logger.debug('Job failed', {
        job_id: job?.id,
        connector_id: job?.data.connectorId,
      });
    });

    this.worker.on('stalled', (jobId: string) => {
      this.logger.warn('Job stalled', { job_id: jobId });
    });

    this.worker.on('progress', (job: Job, progress: JobProgress) => {
      this.logger.debug('Job progress updated', {
        job_id: job.id,
        progress,
      });
    });

    this.worker.on('error', (error: Error) => {
      this.logger.error('Worker error', { error });
    });
  }

  async close(): Promise<void> {
    await this.worker.close();
  }
}

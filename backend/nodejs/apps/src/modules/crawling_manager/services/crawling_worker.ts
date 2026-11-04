import { Worker, Job, WorkerOptions, JobProgress } from 'bullmq';
import { PgJobWorker } from '../../../libs/services/pg-job-queue.service';
import { usesPgScheduler } from './crawling_pg';
import { Logger } from '../../../libs/services/logger.service';
import { inject, injectable } from 'inversify';
import { RedisConfig } from '../../../libs/types/redis.types';
import { CrawlingJobData } from '../schema/interface';
import { ConnectorsCrawlingService } from './connectors/connectors';
import { ICrawlingTaskService } from './task/crawling_task_service';
import { crawlingQueuePrefix, crawlingRedisProvider } from './crawling_queue';
import {
  runWithRequestContext,
  newSystemRoot,
} from '../../../libs/context/request-context';
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
    // Scheduled crawl tick — seed a fresh trace root.
    return runWithRequestContext({ rootId: newSystemRoot() }, () =>
      this.processJobForOrg(job),
    );
  }

  /** Bind the job's org only if it still exists and is active; a gone org's job is dropped. */
  private async processJobForOrg(job: Job<CrawlingJobData>): Promise<void> {
    const { orgId } = job.data;
    if (!orgId) return this.processJobInContext(job);
    if (!/^[0-9a-f]{24}$/i.test(orgId)) {
      // Not an ObjectId (for example the "system" bootstrap id): drop, a retry cannot fix it.
      this.logger.warn('Skipping crawling job: org id is not valid', {
        jobId: job.id,
        orgId,
      });
      return;
    }
    if (!(await getOrgRepository().isActive(orgId))) {
      this.logger.warn(
        'Skipping crawling job: org does not exist or is not active',
        {
          jobId: job.id,
          orgId,
        },
      );
      return;
    }
    return forOrg(orgId, 'crawl', () => this.processJobInContext(job));
  }

  private async processJobInContext(job: Job<CrawlingJobData>): Promise<void> {
    const { orgId, userId, scheduleConfig, connector, connectorId } = job.data;

    this.logger.info('Processing crawling job', {
      jobId: job.id,
      connector,
      connectorId,
      orgId,
      userId,
    });

    try {
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

      this.logger.debug('Crawling job completed successfully', {
        jobId: job.id,
        connector,
        connectorId,
        orgId,
        result,
      });
    } catch (error) {
      this.logger.error('Crawling job failed', {
        jobId: job.id,
        connector,
        connectorId,
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error; // This will mark the job as failed and trigger retries
    }
  }

  private setupWorkerListeners(): void {
    this.worker.on('completed', (job: Job) => {
      this.logger.info('Job completed', {
        jobId: job.id,
        connector: job.data.connector,
        connectorId: job.data.connectorId,
      });
    });

    this.worker.on('failed', (job: Job | undefined, err: Error) => {
      this.logger.error('Job failed', {
        jobId: job?.id,
        connector: job?.data.connector,
        connectorId: job?.data.connectorId,
        error: err.message,
      });
    });

    this.worker.on('stalled', (jobId: string) => {
      this.logger.warn('Job stalled', { jobId });
    });

    this.worker.on('progress', (job: Job, progress: JobProgress) => {
      this.logger.debug('Job progress updated', {
        jobId: job.id,
        progress,
      });
    });

    this.worker.on('error', (error: Error) => {
      this.logger.error('Worker error', { error: error.message });
    });
  }

  async close(): Promise<void> {
    await this.worker.close();
  }
}

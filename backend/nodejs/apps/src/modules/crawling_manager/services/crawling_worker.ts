import { Worker, Job, WorkerOptions, JobProgress } from 'bullmq';
import { Logger } from '../../../libs/services/logger.service';
import { inject, injectable } from 'inversify';
import { RedisConfig } from '../../../libs/types/redis.types';
import { CrawlingJobData } from '../schema/interface';
import { ConnectorsCrawlingService } from './connectors/connectors';
import { ICrawlingTaskService } from './task/crawling_task_service';

@injectable()
export class CrawlingWorkerService {
  private worker: Worker;
  private readonly logger: Logger;

  constructor(
    @inject('RedisConfig') redisConfig: RedisConfig,
    @inject(ConnectorsCrawlingService) private taskService: ICrawlingTaskService,
  ) { 
    this.logger = Logger.getInstance({ service: 'CrawlingWorkerService' });

    const workerOptions: WorkerOptions = {
      connection: {
        host: redisConfig.host,
        port: redisConfig.port,
        password: redisConfig.password,
      },
      concurrency: 5, // Process up to 5 jobs concurrently
      maxStalledCount: 3,
      stalledInterval: 30000, // 30 seconds
    };

    this.worker = new Worker(
      'crawling-scheduler', // Same queue name as in scheduler service
      this.processJob.bind(this),
      workerOptions,
    );

    this.setupWorkerListeners();
    this.logger.info('CrawlingWorkerService initialized');
  }

  private async processJob(job: Job<CrawlingJobData>): Promise<void> {
    const {  orgId, userId, scheduleConfig, connector } = job.data;

    this.logger.info('Processing crawling job', {
      jobId: job.id,
      connector,
      orgId,
      userId,
    });

    try {
      // Update job progress
      await job.updateProgress(10);

      await job.updateProgress(20);

      // Execute the crawling task with connector information
      const result = await this.taskService.crawl(
        orgId, 
        userId, 
        scheduleConfig, 
        connector
      );

      await job.updateProgress(100);

      this.logger.info('Crawling job completed successfully', {
        jobId: job.id,
        connector,
        orgId,
        result,
      });
    } catch (error) {
      this.logger.error('Crawling job failed', {
        jobId: job.id,
        connector,
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
      });
    });

    this.worker.on('failed', (job: Job | undefined, err: Error) => {
      this.logger.error('Job failed', {
        jobId: job?.id,
        connector: job?.data.connector,
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

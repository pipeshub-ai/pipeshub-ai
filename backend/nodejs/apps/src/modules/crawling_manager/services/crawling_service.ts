import { Queue, QueueOptions, Job, JobsOptions, RepeatOptions } from 'bullmq';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { CrawlingScheduleType } from '../schema/enums';
import { ConnectorType } from '../schema/enums';
import { inject, injectable } from 'inversify';
import { RedisConfig } from '../../../libs/types/redis.types';

interface CrawlingJobData {
  connectorType: ConnectorType;
  scheduleConfig: any; // Using any since BaseScheduleConfigSchema is a Mongoose schema, not Zod
  orgId: string;
  userId: string;
  timestamp: Date;
}

@injectable()
export class CrawlingSchedulerService {
  private queue: Queue;
  private readonly logger: Logger;

  constructor(@inject('RedisConfig') redisConfig: RedisConfig) {
    this.logger = Logger.getInstance({ service: 'CrawlingSchedulerService' });

    const queueOptions: QueueOptions = {
      connection: {
        host: redisConfig.host,
        port: redisConfig.port,
        password: redisConfig.password,
      },
      defaultJobOptions: {
        removeOnComplete: 50,
        removeOnFail: 100,
        attempts: 3,
        backoff: {
          type: 'exponential',
          delay: 5000,
        },
      },
    };

    this.queue = new Queue('crawling-scheduler', queueOptions);
    this.setupQueueListeners();
  }

  private setupQueueListeners(): void {
    // Queue event listeners removed - these events are handled by Worker, not Queue
    // Job status can be checked via getJobStatus method
  }

  private buildJobId(connectorType: ConnectorType, orgId: string): string {
    return `${connectorType.toLowerCase().replace(/\s+/g, '-')}-${orgId}`;
  }

  private transformScheduleConfig(
    scheduleConfig: any,
  ): RepeatOptions | undefined {
    const { scheduleType, timezone = 'UTC' } = scheduleConfig;

    switch (scheduleType) {
      case CrawlingScheduleType.HOURLY:
        const hourlyConfig = scheduleConfig as any;
        return {
          pattern: `${hourlyConfig.minute} */${hourlyConfig.interval} * * *`,
          tz: timezone,
        };

      case CrawlingScheduleType.DAILY:
        const dailyConfig = scheduleConfig as any;
        return {
          pattern: `${dailyConfig.minute} ${dailyConfig.hour} * * *`,
          tz: timezone,
        };

      case CrawlingScheduleType.WEEKLY:
        const weeklyConfig = scheduleConfig as any;
        const daysOfWeek = weeklyConfig.daysOfWeek.join(',');
        return {
          pattern: `${weeklyConfig.minute} ${weeklyConfig.hour} * * ${daysOfWeek}`,
          tz: timezone,
        };

      case CrawlingScheduleType.MONTHLY:
        const monthlyConfig = scheduleConfig as any;
        return {
          pattern: `${monthlyConfig.minute} ${monthlyConfig.hour} ${monthlyConfig.dayOfMonth} * *`,
          tz: timezone,
        };

      case CrawlingScheduleType.CUSTOM:
        const customConfig = scheduleConfig as any;
        return {
          pattern: customConfig.cronExpression,
          tz: timezone,
        };

      case CrawlingScheduleType.ONCE:
        // For "once" schedule type, we return undefined and handle it separately
        return undefined;

      default:
        throw new BadRequestError('Invalid schedule type');
    }
  }

  async scheduleJob(
    connectorType: ConnectorType,
    scheduleConfig: any,
    orgId: string,
    userId: string,
    options: {
      priority?: number;
      maxRetries?: number;
      timeout?: number;
    } = {},
  ): Promise<Job<CrawlingJobData>> {
    const jobId = this.buildJobId(connectorType, orgId);

    // Remove existing job if it exists
    await this.removeJob(connectorType, orgId);

    const jobData: CrawlingJobData = {
      connectorType,
      scheduleConfig,
      orgId,
      userId,
      timestamp: new Date(),
    };

    const jobOptions: JobsOptions = {
      jobId,
      priority: options.priority || 5,
      attempts: options.maxRetries || 3,
      removeOnComplete: 50,
      removeOnFail: 100,
    };

    // Handle different schedule types
    if (scheduleConfig.scheduleType === CrawlingScheduleType.ONCE) {
      const onceConfig = scheduleConfig as any;
      const scheduledTime = new Date(onceConfig.scheduledTime);
      const delay = scheduledTime.getTime() - Date.now();

      if (delay <= 0) {
        throw new BadRequestError('Scheduled time must be in the future');
      }

      jobOptions.delay = delay;
    } else {
      const repeatOptions = this.transformScheduleConfig(scheduleConfig);
      if (repeatOptions && scheduleConfig.isEnabled) {
        jobOptions.repeat = repeatOptions;
      }
    }

    this.logger.info('Scheduling crawling job', {
      jobId,
      connectorType,
      scheduleType: scheduleConfig.scheduleType,
      orgId,
    });

    return await this.queue.add('crawl', jobData, jobOptions);
  }

  async removeJob(connectorType: ConnectorType, orgId: string): Promise<void> {
    const jobId = this.buildJobId(connectorType, orgId);

    try {
      // Remove scheduled job
      const job = await this.queue.getJob(jobId);
      if (job) {
        await job.remove();
      }

      // Remove any repeatable jobs
      const repeatableJobs = await this.queue.getRepeatableJobs();
      for (const repeatableJob of repeatableJobs) {
        if (repeatableJob.id === jobId) {
          await this.queue.removeRepeatable(
            repeatableJob.name,
            {
              pattern: repeatableJob.pattern ?? undefined,
              tz: repeatableJob.tz ?? undefined,
              endDate:
                repeatableJob.endDate !== null
                  ? repeatableJob.endDate
                  : undefined,
            },
          );
        }
      }

      this.logger.info('Removed crawling job', { jobId, connectorType, orgId });
    } catch (error) {
      this.logger.error('Failed to remove job', {
        jobId,
        connectorType,
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async getJobStatus(
    connectorType: ConnectorType,
    orgId: string,
  ): Promise<any> {
    const jobId = this.buildJobId(connectorType, orgId);
    const job = await this.queue.getJob(jobId);

    if (!job) {
      return null;
    }

    return {
      id: job.id,
      name: job.name,
      data: job.data,
      opts: job.opts,
      progress: job.progress,
      delay: job.delay,
      timestamp: job.timestamp,
      attemptsMade: job.attemptsMade,
      finishedOn: job.finishedOn,
      processedOn: job.processedOn,
      failedReason: job.failedReason,
    };
  }

  async close(): Promise<void> {
    await this.queue.close();
  }
}

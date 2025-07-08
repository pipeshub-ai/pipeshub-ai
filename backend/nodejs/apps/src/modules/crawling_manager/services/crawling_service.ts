import { Queue, QueueOptions, Job, JobsOptions, RepeatOptions } from 'bullmq';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { CrawlingScheduleType } from '../schema/enums';
import { ConnectorType } from '../schema/enums';
import { inject, injectable } from 'inversify';
import { RedisConfig } from '../../../libs/types/redis.types';
import {
  CrawlingJobData,
  ScheduleJobOptions,
  JobStatus,
  ICrawlingSchedule,
} from '../schema/interface';

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
        db: redisConfig.db || 0,
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
    this.logger.info('CrawlingSchedulerService initialized');
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
        return {
          pattern: `${scheduleConfig.minute} */${scheduleConfig.interval} * * *`,
          tz: timezone,
        };

      case CrawlingScheduleType.DAILY:
        return {
          pattern: `${scheduleConfig.minute} ${scheduleConfig.hour} * * *`,
          tz: timezone,
        };

      case CrawlingScheduleType.WEEKLY:
        const daysOfWeek = scheduleConfig.daysOfWeek.join(',');
        return {
          pattern: `${scheduleConfig.minute} ${scheduleConfig.hour} * * ${daysOfWeek}`,
          tz: timezone,
        };

      case CrawlingScheduleType.MONTHLY:
        return {
          pattern: `${scheduleConfig.minute} ${scheduleConfig.hour} ${scheduleConfig.dayOfMonth} * *`,
          tz: timezone,
        };

      case CrawlingScheduleType.CUSTOM:
        return {
          pattern: scheduleConfig.cronExpression,
          tz: timezone,
        };

      case CrawlingScheduleType.ONCE:
        return undefined;

      default:
        throw new BadRequestError('Invalid schedule type');
    }
  }

  async scheduleJob(
    connectorType: ConnectorType,
    scheduleConfig: ICrawlingSchedule,
    orgId: string,
    userId: string,
    options: ScheduleJobOptions = {},
  ): Promise<Job<CrawlingJobData>> {
    this.logger.info('scheduleJob crawling job', {
      connectorType,
      orgId,
      userId,
      scheduleType: scheduleConfig.scheduleType,
    });
    const jobId = this.buildJobId(connectorType, orgId);


    
    this.logger.info('Scheduling crawling job', {
      jobId,
      connectorType,
      scheduleType: scheduleConfig.scheduleType,
      orgId,
      userId,
    });

    // Remove existing job if it exists
    await this.removeJob(connectorType, orgId);

    const jobData: CrawlingJobData = {
      connectorType,
      scheduleConfig,
      orgId,
      userId,
      timestamp: new Date(),
      metadata: options.metadata,
    };

    const jobOptions: JobsOptions = {
      jobId,
      priority: options.priority || 5,
      attempts: options.maxRetries || 3,
      removeOnComplete: 50,
      removeOnFail: 100,
    };

    // Add timeout if specified
    if (options.timeout) {
      jobOptions.delay = 0; // No delay, but we can add timeout handling in worker
    }

    // Handle different schedule types
    if (scheduleConfig.scheduleType === CrawlingScheduleType.ONCE) {
      const scheduledTime = new Date(scheduleConfig.scheduleConfig.scheduledTime);
      const delay = scheduledTime.getTime() - Date.now();

      if (delay <= 0) {
        throw new BadRequestError('Scheduled time must be in the future');
      }

      jobOptions.delay = delay;

      this.logger.info('Scheduling one-time job', {
        jobId,
        scheduledTime: scheduleConfig.scheduleConfig.scheduledTime,
        delay,
      });
    } else {
      const repeatOptions = this.transformScheduleConfig(scheduleConfig);
      if (repeatOptions && scheduleConfig.isEnabled) {
        jobOptions.repeat = repeatOptions;

        this.logger.info('Scheduling repeating job', {
          jobId,
          pattern: repeatOptions.pattern,
          timezone: repeatOptions.tz,
        });
      } else if (!scheduleConfig.isEnabled) {
        this.logger.info('Job scheduled but disabled', { jobId });
      }
    }

    const job = await this.queue.add('crawl', jobData, jobOptions);

    this.logger.info('Crawling job scheduled successfully', {
      jobId: job.id,
      connectorType,
      orgId,
    });

    return job;
  }

  async removeJob(connectorType: ConnectorType, orgId: string): Promise<void> {
    const jobId = this.buildJobId(connectorType, orgId);

    this.logger.info('Removing crawling job', { jobId, connectorType, orgId });

    try {
      // Remove scheduled job
      const job = await this.queue.getJob(jobId);
      if (job) {
        await job.remove();
        this.logger.debug('Removed scheduled job', { jobId });
      }

      // Remove any repeatable jobs
      const repeatableJobs = await this.queue.getRepeatableJobs();
      for (const repeatableJob of repeatableJobs) {
        if (repeatableJob.id === jobId) {
          await this.queue.removeRepeatable(repeatableJob.name, {
            pattern: repeatableJob.pattern ?? undefined,
            tz: repeatableJob.tz ?? undefined,
            endDate:
              repeatableJob.endDate !== null
                ? repeatableJob.endDate
                : undefined,
          });
          this.logger.debug('Removed repeatable job', {
            jobId,
            pattern: repeatableJob.pattern,
          });
        }
      }

      this.logger.info('Successfully removed crawling job', {
        jobId,
        connectorType,
        orgId,
      });
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
  ): Promise<JobStatus | null> {
    const jobId = this.buildJobId(connectorType, orgId);

    this.logger.debug('Getting job status', { jobId, connectorType, orgId });

    try {
      const job = await this.queue.getJob(jobId);

      if (!job) {
        this.logger.debug('Job not found', { jobId });
        return null;
      }

      const jobState = await job.getState();

      const jobStatus: JobStatus = {
        id: job.id,
        name: job.name,
        data: job.data,
        progress: job.progress,
        delay: job.delay,
        timestamp: job.timestamp,
        attemptsMade: job.attemptsMade,
        finishedOn: job.finishedOn,
        processedOn: job.processedOn,
        failedReason: job.failedReason,
        state: jobState,
      };

      this.logger.debug('Job status retrieved', {
        jobId,
        state: jobState,
        progress: job.progress,
      });

      return jobStatus;
    } catch (error) {
      this.logger.error('Failed to get job status', {
        jobId,
        connectorType,
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async getAllJobs(orgId: string): Promise<JobStatus[]> {
    this.logger.debug('Getting all jobs for organization', { orgId });

    try {
      const jobs = await this.queue.getJobs([
        'waiting',
        'active',
        'completed',
        'failed',
        'delayed',
      ]);

      // Filter jobs by orgId
      const orgJobs = jobs.filter((job) => job.data.orgId === orgId);

      const jobStatuses: JobStatus[] = [];

      for (const job of orgJobs) {
        const jobState = await job.getState();

        jobStatuses.push({
          id: job.id,
          name: job.name,
          data: job.data,
          progress: job.progress,
          delay: job.delay,
          timestamp: job.timestamp,
          attemptsMade: job.attemptsMade,
          finishedOn: job.finishedOn,
          processedOn: job.processedOn,
          failedReason: job.failedReason,
          state: jobState,
        });
      }

      this.logger.debug('Retrieved all jobs for organization', {
        orgId,
        jobCount: jobStatuses.length,
      });

      return jobStatuses;
    } catch (error) {
      this.logger.error('Failed to get all jobs', {
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async getRepeatableJobs(orgId?: string): Promise<any[]> {
    this.logger.debug('Getting repeatable jobs', { orgId });

    try {
      const repeatableJobs = await this.queue.getRepeatableJobs();

      if (orgId) {
        // Filter by orgId if provided
        return repeatableJobs.filter((job) => job.id?.includes(orgId));
      }

      return repeatableJobs;
    } catch (error) {
      this.logger.error('Failed to get repeatable jobs', {
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async pauseJob(connectorType: ConnectorType, orgId: string): Promise<void> {
    const jobId = this.buildJobId(connectorType, orgId);

    this.logger.info('Pausing job', { jobId, connectorType, orgId });

    try {
      const job = await this.queue.getJob(jobId);

      if (!job) {
        throw new BadRequestError('Job not found');
      }

      // For repeatable jobs, we need to remove and recreate with disabled state
      if (job.opts.repeat) {
        const updatedScheduleConfig = {
          ...job.data.scheduleConfig,
          isEnabled: false,
        };

        await this.scheduleJob(
          connectorType,
          updatedScheduleConfig,
          orgId,
          job.data.userId,
          {
            priority: job.opts.priority,
            maxRetries: job.opts.attempts,
          },
        );
      }

      this.logger.info('Job paused successfully', { jobId });
    } catch (error) {
      this.logger.error('Failed to pause job', {
        jobId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async resumeJob(connectorType: ConnectorType, orgId: string): Promise<void> {
    const jobId = this.buildJobId(connectorType, orgId);

    this.logger.info('Resuming job', { jobId, connectorType, orgId });

    try {
      const job = await this.queue.getJob(jobId);

      if (!job) {
        throw new BadRequestError('Job not found');
      }

      const updatedScheduleConfig = {
        ...job.data.scheduleConfig,
        isEnabled: true,
      };

      await this.scheduleJob(
        connectorType,
        updatedScheduleConfig,
        orgId,
        job.data.userId,
        {
          priority: job.opts.priority,
          maxRetries: job.opts.attempts,
        },
      );

      this.logger.info('Job resumed successfully', { jobId });
    } catch (error) {
      this.logger.error('Failed to resume job', {
        jobId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async getQueueStats(): Promise<any> {
    this.logger.debug('Getting queue statistics');

    try {
      const waiting = await this.queue.getWaiting();
      const active = await this.queue.getActive();
      const completed = await this.queue.getCompleted();
      const failed = await this.queue.getFailed();
      const delayed = await this.queue.getDelayed();
      const repeatableJobs = await this.queue.getRepeatableJobs();

      const stats = {
        waiting: waiting.length,
        active: active.length,
        completed: completed.length,
        failed: failed.length,
        delayed: delayed.length,
        repeatable: repeatableJobs.length,
        total:
          waiting.length +
          active.length +
          completed.length +
          failed.length +
          delayed.length,
      };

      this.logger.debug('Queue statistics retrieved', stats);
      return stats;
    } catch (error) {
      this.logger.error('Failed to get queue stats', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async close(): Promise<void> {
    this.logger.info('Closing CrawlingSchedulerService');

    try {
      await this.queue.close();
      this.logger.info('CrawlingSchedulerService closed successfully');
    } catch (error) {
      this.logger.error('Error closing CrawlingSchedulerService', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }
}

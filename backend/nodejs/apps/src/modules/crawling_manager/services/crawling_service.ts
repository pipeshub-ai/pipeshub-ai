import {
  Queue,
  QueueOptions,
  Job,
  JobsOptions,
  RepeatOptions,
  JobType,
} from 'bullmq';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { CrawlingScheduleType } from '../schema/enums';
import { inject, injectable } from 'inversify';
import { RedisConfig } from '../../../libs/types/redis.types';
import {
  CrawlingJobData,
  ScheduleJobOptions,
  JobStatus,
  ICrawlingSchedule,
} from '../schema/interface';

// Interface for storing paused job information
interface PausedJobInfo {
  connector: string;
  scheduleConfig: ICrawlingSchedule;
  orgId: string;
  userId: string;
  options: ScheduleJobOptions;
  pausedAt: Date;
}

@injectable()
export class CrawlingSchedulerService {
  private queue: Queue;
  private readonly logger: Logger;
  private repeatableJobMap: Map<string, string> = new Map(); // customJobId -> repeatableJobKey
  private pausedJobs: Map<string, PausedJobInfo> = new Map(); // jobId -> PausedJobInfo

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
        removeOnComplete: 10, // Keep only last 10 completed jobs per connector type
        removeOnFail: 10, // Keep only last 10 failed jobs per connector type
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

  /**
   * Creates a consistent job ID based on connector type and org ID
   */
  private buildJobId(connector: string, orgId: string): string {
    return `crawl-${connector.toLowerCase().replace(/\s+/g, '-')}-${orgId}`;
  }

  /**
   * Transforms schedule configuration to BullMQ repeat options
   */
  private transformScheduleConfig(
    scheduleConfig: any,
  ): RepeatOptions | undefined {
    const { scheduleType, timezone = 'UTC' } = scheduleConfig;

    switch (scheduleType) {
      case CrawlingScheduleType.HOURLY:
        return {
          pattern: `${scheduleConfig.minute} */${scheduleConfig.interval || 1} * * *`,
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
        return undefined; // One-time jobs don't use repeat

      default:
        throw new BadRequestError('Invalid schedule type');
    }
  }

  /**
   * Schedule a crawling job for a specific connector type
   */
  async scheduleJob(
    connector: string,
    scheduleConfig: ICrawlingSchedule,
    orgId: string,
    userId: string,
    options: ScheduleJobOptions = {},
  ): Promise<Job<CrawlingJobData>> {
    const jobId = this.buildJobId(connector, orgId);

    this.logger.info('Scheduling crawling job', {
      jobId,
      connector,
      orgId,
      userId,
      scheduleType: scheduleConfig.scheduleType,
      isEnabled: scheduleConfig.isEnabled,
    });

    // Remove any existing job for this connector type and org
    await this.removeJobInternal(connector, orgId);

    // Remove from paused jobs if it exists
    this.pausedJobs.delete(jobId);

    // Don't create a new job if the schedule is disabled
    if (!scheduleConfig.isEnabled) {
      this.logger.info('Schedule is disabled, not creating job', { jobId });
      throw new BadRequestError('Cannot schedule a disabled job');
    }

    const jobData: CrawlingJobData = {
      connector,
      scheduleConfig,
      orgId,
      userId,
      timestamp: new Date(),
      metadata: options.metadata,
    };

    const jobOptions: JobsOptions = {
      priority: options.priority || 5,
      attempts: options.maxRetries || 3,
      removeOnComplete: 10, // Keep only last 10 completed jobs
      removeOnFail: 10, // Keep only last 10 failed jobs
    };

    // Handle different schedule types
    if (scheduleConfig.scheduleType === CrawlingScheduleType.ONCE) {
      const scheduledTime = new Date(
        scheduleConfig.scheduleConfig.scheduledTime,
      );
      const delay = scheduledTime.getTime() - Date.now();

      if (delay <= 0) {
        throw new BadRequestError('Scheduled time must be in the future');
      }

      jobOptions.delay = delay;
      jobOptions.jobId = jobId; // Use custom job ID for one-time jobs

      this.logger.info('Scheduling one-time job', {
        jobId,
        scheduledTime: scheduleConfig.scheduleConfig.scheduledTime,
        delay,
      });
    } else {
      // For repeating jobs
      const repeatOptions = this.transformScheduleConfig(scheduleConfig);
      if (repeatOptions) {
        jobOptions.repeat = repeatOptions;

        this.logger.info('Scheduling repeating job', {
          jobId,
          pattern: repeatOptions.pattern,
          timezone: repeatOptions.tz,
        });
      }
    }

    const jobName = this.buildJobName(connector);
    const job = await this.queue.add(jobName, jobData, jobOptions);

    // For repeatable jobs, store the mapping to the repeatable job key
    if (scheduleConfig.scheduleType !== CrawlingScheduleType.ONCE) {
      // Wait a moment for the repeatable job to be registered
      await new Promise((resolve) => setTimeout(resolve, 100));

      const repeatableJobs = await this.queue.getRepeatableJobs();
      const matchingRepeatableJob = repeatableJobs.find((rJob) => {
        // Match by pattern and check if it's our job
        const repeatOptions = this.transformScheduleConfig(scheduleConfig);
        return (
          repeatOptions &&
          rJob.pattern === repeatOptions.pattern &&
          rJob.tz === repeatOptions.tz
        );
      });

      if (matchingRepeatableJob) {
        this.repeatableJobMap.set(jobId, matchingRepeatableJob.key);
        this.logger.debug('Repeatable job mapping stored', {
          customJobId: jobId,
          repeatableJobKey: matchingRepeatableJob.key,
          pattern: matchingRepeatableJob.pattern,
        });
      }
    }

    this.logger.info('Crawling job scheduled successfully', {
      jobId,
      actualJobId: job.id,
      connector,
      orgId,
      scheduleType: scheduleConfig.scheduleType,
    });

    return job;
  }

  /**
   * Remove a specific job (internal method with proper repeatable job handling)
   */
  private async removeJobInternal(
    connector: string,
    orgId: string,
  ): Promise<void> {
    const jobId = this.buildJobId(connector, orgId);

    try {
      // First, remove any repeatable jobs for this connector/org
      const repeatableJobs = await this.queue.getRepeatableJobs();

      // Get all jobs to find which repeatable job belongs to our connector/org
      const allJobs = await this.queue.getJobs([
        'waiting',
        'active',
        'delayed',
      ] as JobType[]);
      const matchingJobs = allJobs.filter(
        (job) =>
          job.data.connector === connector && job.data.orgId === orgId,
      );

      // Remove repeatable jobs that match our connector/org
      for (const repeatableJob of repeatableJobs) {
        // Check if any of our matching jobs uses this repeatable pattern
        const jobUsesThisPattern = matchingJobs.some(
          (job) =>
            job.opts?.repeat?.pattern === repeatableJob.pattern &&
            job.opts?.repeat?.tz === repeatableJob.tz,
        );

        if (jobUsesThisPattern) {
          try {
            await this.queue.removeRepeatable(
              this.buildJobName(connector),
              {
                pattern: repeatableJob.pattern || undefined,
                tz: repeatableJob.tz || undefined,
                endDate:
                  repeatableJob.endDate !== null
                    ? repeatableJob.endDate
                    : undefined,
              },
            );

            this.logger.debug('Removed repeatable job', {
              jobId,
              pattern: repeatableJob.pattern,
              key: repeatableJob.key,
            });
          } catch (error) {
            this.logger.debug('Error removing repeatable job', {
              pattern: repeatableJob.pattern,
              error: error instanceof Error ? error.message : 'Unknown error',
            });
          }
        }
      }

      // Remove individual job instances that match our criteria
      const allJobStates: JobType[] = [
        'waiting',
        'active',
        'delayed',
        'completed',
        'failed',
      ];
      const allJobInstances = await this.queue.getJobs(allJobStates);

      const matchingJobInstances = allJobInstances.filter(
        (job) =>
          job.data.connector === connector && job.data.orgId === orgId,
      );

      // Keep only the last 10 jobs, remove the rest
      const sortedJobs = matchingJobInstances.sort(
        (a, b) => (b.timestamp || 0) - (a.timestamp || 0),
      );
      const jobsToRemove = sortedJobs.slice(10); // Remove all but the last 10

      for (const job of jobsToRemove) {
        try {
          await job.remove();
          this.logger.debug('Removed old job instance', {
            jobId: job.id,
            connector,
            orgId,
          });
        } catch (error) {
          this.logger.debug('Failed to remove job instance', {
            jobId: job.id,
            error: error instanceof Error ? error.message : 'Unknown error',
          });
        }
      }

      // Clean up the mapping
      this.repeatableJobMap.delete(jobId);
    } catch (error) {
      this.logger.warn('Error during job removal', {
        jobId,
        connector,
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Remove a job (public method)
   */
  async removeJob(connector: string, orgId: string): Promise<void> {
    const jobId = this.buildJobId(connector, orgId);

    this.logger.info('Removing crawling job', {
      jobId,
      connector,
      orgId,
    });

    await this.removeJobInternal(connector, orgId);

    // Also remove from paused jobs if it exists
    this.pausedJobs.delete(jobId);

    this.logger.info('Successfully removed crawling job', {
      jobId,
      connector,
      orgId,
    });
  }

  /**
   * Get job status for a specific connector type
   */
  async getJobStatus(
    connector: string,
    orgId: string,
  ): Promise<JobStatus | null> {
    const jobId = this.buildJobId(connector, orgId);

    this.logger.debug('Getting job status', {
      jobId,
      connector,
      orgId,
    });

    try {
      // First check if the job is paused
      const pausedJob = this.pausedJobs.get(jobId);
      if (pausedJob) {
        this.logger.debug('Job is paused', { jobId, connector, orgId });
        return {
          id: jobId,
          name: this.buildJobName(connector),
          data: {
            connector,
            scheduleConfig: pausedJob.scheduleConfig,
            orgId,
            userId: pausedJob.userId,
            timestamp: pausedJob.pausedAt,
            metadata: pausedJob.options.metadata,
          },
          progress: 0,
          delay: undefined,
          timestamp: pausedJob.pausedAt.getTime(),
          attemptsMade: 0,
          finishedOn: undefined,
          processedOn: undefined,
          failedReason: undefined,
          state: 'paused',
        };
      }

      // Get all jobs for this connector/org combination
      const allJobs = await this.queue.getJobs([
        'waiting',
        'active',
        'delayed',
        'failed',
        'completed',
      ] as JobType[]);

      const matchingJobs = allJobs.filter(
        (job) =>
          job.data.connector === connector && job.data.orgId === orgId,
      );

      if (matchingJobs.length === 0) {
        this.logger.debug('No jobs found', { jobId, connector, orgId });
        return null;
      }

      // Get the most recent job
      const mostRecentJob = matchingJobs.sort(
        (a, b) => (b.timestamp || 0) - (a.timestamp || 0),
      )[0];

      const jobState = await mostRecentJob.getState();

      const jobStatus: JobStatus = {
        id: mostRecentJob.id,
        name: mostRecentJob.name,
        data: mostRecentJob.data,
        progress: mostRecentJob.progress,
        delay: mostRecentJob.delay,
        timestamp: mostRecentJob.timestamp,
        attemptsMade: mostRecentJob.attemptsMade,
        finishedOn: mostRecentJob.finishedOn,
        processedOn: mostRecentJob.processedOn,
        failedReason: mostRecentJob.failedReason,
        state: jobState,
      };

      this.logger.debug('Job status retrieved', {
        jobId,
        actualJobId: mostRecentJob.id,
        state: jobState,
        progress: mostRecentJob.progress,
      });

      return jobStatus;
    } catch (error) {
      this.logger.error('Failed to get job status', {
        jobId,
        connector,
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  /**
   * Get all jobs for an organization (limited to last 10 per connector type)
   */
  async getAllJobs(orgId: string): Promise<JobStatus[]> {
    this.logger.debug('Getting all jobs for organization', { orgId });

    try {
      const jobs = await this.queue.getJobs([
        'waiting',
        'active',
        'completed',
        'failed',
        'delayed',
      ] as JobType[]);

      // Filter jobs by orgId
      const orgJobs = jobs.filter((job) => job.data.orgId === orgId);

      // Group jobs by connector type
      const jobsByConnector = new Map<string, Job<CrawlingJobData>[]>();

      for (const job of orgJobs) {
        const connector = job.data.connector;
        if (!jobsByConnector.has(connector)) {
          jobsByConnector.set(connector, []);
        }
        jobsByConnector.get(connector)!.push(job);
      }

      const jobStatuses: JobStatus[] = [];

      // For each connector type, keep only the last 10 jobs
      for (const [_connector, connectorJobs] of jobsByConnector) {
        const sortedJobs = connectorJobs.sort(
          (a, b) => (b.timestamp || 0) - (a.timestamp || 0),
        );
        const last10Jobs = sortedJobs.slice(0, 10);

        for (const job of last10Jobs) {
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
      }

      // Add paused jobs for this organization
      for (const [jobId, pausedJob] of this.pausedJobs) {
        if (pausedJob.orgId === orgId) {
          jobStatuses.push({
            id: jobId,
            name: this.buildJobName(pausedJob.connector),
            data: {
              connector: pausedJob.connector,
              scheduleConfig: pausedJob.scheduleConfig,
              orgId: pausedJob.orgId,
              userId: pausedJob.userId,
              timestamp: pausedJob.pausedAt,
              metadata: pausedJob.options.metadata,
            },
            progress: 0,
            delay: undefined,
            timestamp: pausedJob.pausedAt.getTime(),
            attemptsMade: 0,
            finishedOn: undefined,
            processedOn: undefined,
            failedReason: undefined,
            state: 'paused',
          });
        }
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

  /**
   * Get repeatable jobs, optionally filtered by organization
   */
  async getRepeatableJobs(orgId?: string): Promise<any[]> {
    this.logger.debug('Getting repeatable jobs', { orgId });

    try {
      const repeatableJobs = await this.queue.getRepeatableJobs();

      if (orgId) {
        // We need to check the actual job data to filter by orgId
        const filteredJobs = [];

        for (const repeatableJob of repeatableJobs) {
          // Get a sample job to check the data
          const jobs = await this.queue.getJobs([
            'waiting',
            'active',
            'delayed',
          ] as JobType[]);
          const sampleJob = jobs.find(
            (job) =>
              job.data.orgId === orgId &&
              job.opts?.repeat?.pattern === repeatableJob.pattern &&
              job.opts?.repeat?.tz === repeatableJob.tz,
          );

          if (sampleJob) {
            filteredJobs.push(repeatableJob);
          }
        }

        return filteredJobs;
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

  /**
   * Pause a job by storing its configuration and removing the active job
   */
  async pauseJob(connector: string, orgId: string): Promise<void> {
    const jobId = this.buildJobId(connector, orgId);

    this.logger.info('Pausing job', { jobId, connector, orgId });

    try {
      // First, get the current job to store its configuration
      const currentJobStatus = await this.getJobStatus(connector, orgId);

      if (!currentJobStatus) {
        throw new BadRequestError('No active job found to pause');
      }

      if (currentJobStatus.state === 'paused') {
        throw new BadRequestError('Job is already paused');
      }

      // Store the job configuration for later resume
      const pausedJobInfo: PausedJobInfo = {
        connector,
        scheduleConfig: currentJobStatus.data.scheduleConfig,
        orgId,
        userId: currentJobStatus.data.userId,
        options: {
          metadata: currentJobStatus.data.metadata,
        },
        pausedAt: new Date(),
      };

      this.pausedJobs.set(jobId, pausedJobInfo);

      // Remove the active job
      await this.removeJobInternal(connector, orgId);

      this.logger.info('Job paused successfully', {
        jobId,
        connector,
        orgId,
        pausedAt: pausedJobInfo.pausedAt,
      });
    } catch (error) {
      this.logger.error('Failed to pause job', {
        jobId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  /**
   * Resume a paused job using its stored configuration
   */
  async resumeJob(connector: string, orgId: string): Promise<void> {
    const jobId = this.buildJobId(connector, orgId);

    this.logger.info('Resuming job', { jobId, connector, orgId });

    try {
      // Get the paused job configuration
      const pausedJobInfo = this.pausedJobs.get(jobId);

      if (!pausedJobInfo) {
        throw new BadRequestError('No paused job found to resume');
      }

      // Create a new job with the stored configuration
      await this.scheduleJob(
        connector,
        pausedJobInfo.scheduleConfig,
        orgId,
        pausedJobInfo.userId,
        pausedJobInfo.options,
      );

      // Remove from paused jobs
      this.pausedJobs.delete(jobId);

      this.logger.info('Job resumed successfully', {
        jobId,
        connector,
        orgId,
        resumedAt: new Date(),
      });
    } catch (error) {
      this.logger.error('Failed to resume job', {
        jobId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  /**
   * Get queue statistics
   */
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
        paused: this.pausedJobs.size,
        repeatable: repeatableJobs.length,
        total:
          waiting.length +
          active.length +
          completed.length +
          failed.length +
          delayed.length +
          this.pausedJobs.size,
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

  /**
   * Remove all jobs for an organization
   */
  async removeAllJobs(orgId: string): Promise<void> {
    this.logger.info('Removing all jobs for organization', { orgId });

    try {
      // Remove all repeatable jobs for this org
      const repeatableJobs = await this.queue.getRepeatableJobs();
      const processedJobNames = new Set<string>();

      for (const repeatableJob of repeatableJobs) {
        // Check if this repeatable job belongs to the org
        const jobs = await this.queue.getJobs([
          'waiting',
          'active',
          'delayed',
        ] as JobType[]);
        const matchingJob = jobs.find(
          (job) =>
            job.data.orgId === orgId &&
            job.opts?.repeat?.pattern === repeatableJob.pattern &&
            job.opts?.repeat?.tz === repeatableJob.tz,
        );

        if (matchingJob) {
          try {
            // Get the job name for this connector type
            const jobName = this.buildJobName(matchingJob.data.connector);

            // Skip if we already processed this job name
            if (processedJobNames.has(jobName)) continue;
            processedJobNames.add(jobName);

            await this.queue.removeRepeatable(jobName, {
              pattern: repeatableJob.pattern || undefined,
              tz: repeatableJob.tz || undefined,
              endDate:
                repeatableJob.endDate !== null
                  ? repeatableJob.endDate
                  : undefined,
            });
            this.logger.debug('Removed repeatable job', {
              jobId: repeatableJob.id,
              jobName,
              orgId,
            });
          } catch (error) {
            this.logger.warn('Failed to remove repeatable job', {
              jobId: repeatableJob.id,
              jobName: this.buildJobName(matchingJob.data.connector),
              orgId,
              error: error instanceof Error ? error.message : 'Unknown error',
            });
          }
        }
      }

      // Remove all job instances for this org
      const allJobs = await this.queue.getJobs([
        'waiting',
        'active',
        'delayed',
        'completed',
        'failed',
      ] as JobType[]);
      const orgJobs = allJobs.filter((job) => job.data.orgId === orgId);

      for (const job of orgJobs) {
        try {
          await job.remove();
          this.logger.debug('Removed job', { jobId: job.id, orgId });
        } catch (error) {
          this.logger.warn('Failed to remove job', {
            jobId: job.id,
            orgId,
            error: error instanceof Error ? error.message : 'Unknown error',
          });
        }
      }

      // Remove all paused jobs for this org
      const pausedJobsToRemove: string[] = [];
      for (const [jobId, pausedJob] of this.pausedJobs) {
        if (pausedJob.orgId === orgId) {
          pausedJobsToRemove.push(jobId);
        }
      }

      pausedJobsToRemove.forEach((jobId) => this.pausedJobs.delete(jobId));

      // Clean up mappings for this org
      const keysToDelete: string[] = [];
      for (const [customJobId] of this.repeatableJobMap) {
        if (customJobId.includes(orgId)) {
          keysToDelete.push(customJobId);
        }
      }
      keysToDelete.forEach((key) => this.repeatableJobMap.delete(key));

      this.logger.debug('Cleaned up job mappings', {
        orgId,
        mappingsRemoved: keysToDelete.length,
        pausedJobsRemoved: pausedJobsToRemove.length,
      });

      this.logger.info('All jobs removed successfully', { orgId });
    } catch (error) {
      this.logger.error('Failed to remove all jobs', {
        orgId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  /**
   * Close the scheduler service
   */
  async close(): Promise<void> {
    this.logger.info('Closing CrawlingSchedulerService');

    try {
      await this.queue.close();
      this.repeatableJobMap.clear();
      this.pausedJobs.clear();
      this.logger.info('CrawlingSchedulerService closed successfully');
    } catch (error) {
      this.logger.error('Error closing CrawlingSchedulerService', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  /**
   * Get current repeatable job mappings (useful for debugging)
   */
  getRepeatableJobMappings(): Map<string, string> {
    return new Map(this.repeatableJobMap);
  }

  /**
   * Get paused jobs information (useful for debugging)
   */
  getPausedJobs(): Map<string, PausedJobInfo> {
    return new Map(this.pausedJobs);
  }

  /**
   * Get detailed job information for debugging
   */
  async getJobDebugInfo(
    connector: string,
    orgId: string,
  ): Promise<any> {
    const customJobId = this.buildJobId(connector, orgId);
    const repeatableJobKey = this.repeatableJobMap.get(customJobId);
    const pausedJobInfo = this.pausedJobs.get(customJobId);

    const repeatableJobs = await this.queue.getRepeatableJobs();
    const relevantRepeatableJobs = repeatableJobs.filter(
      (job) => job.key === repeatableJobKey,
    );

    const allJobs = await this.queue.getJobs([
      'waiting',
      'active',
      'delayed',
      'completed',
      'failed',
    ] as JobType[]);
    const matchingJobs = allJobs.filter(
      (job) =>
        job.data.connector === connector && job.data.orgId === orgId,
    );

    return {
      customJobId,
      repeatableJobKey,
      hasMapping: this.repeatableJobMap.has(customJobId),
      isPaused: this.pausedJobs.has(customJobId),
      pausedJobInfo,
      relevantRepeatableJobs,
      matchingJobInstances: matchingJobs.length,
      connector,
      orgId,
    };
  }

  private buildJobName(connector: string): string {
    return `crawl-${connector.toLowerCase().replace(/\s+/g, '-')}`;
  }
}

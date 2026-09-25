import {
  Queue,
  QueueOptions,
  Job,
  JobsOptions,
  RepeatOptions,
  JobType,
  getNextMillis,
} from 'bullmq';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  InternalServerError,
} from '../../../libs/errors/http.errors';
import { CrawlingScheduleType } from '../schema/enums';
import { inject, injectable } from 'inversify';
import { RedisConfig } from '../../../libs/types/redis.types';
import { crawlingQueuePrefix, crawlingRedisProvider } from './crawling_queue';
import {
  CrawlingJobData,
  ScheduleJobOptions,
  JobStatus,
  ICrawlingSchedule,
  IOnceCrawlingSchedule,
} from '../schema/interface';

const UNSCHEDULABLE_MESSAGE =
  "This schedule can't be used: it never produces a run time. Check the cron expression and the timezone name, then try again.";

const RUN_NOT_CANCELLED_MESSAGE =
  'A sync already queued for this connector could not be cancelled, so it may still run. Try again in a moment; if this keeps happening, ask your admin to check the job queue.';

const ONCE_ID_TAKEN_MESSAGE =
  "The previous one-time sync for this connector could not be cleared, so a new one can't be scheduled yet. If it is still running, wait for it to finish; otherwise try again in a moment.";

const SCHEDULE_NOT_REMOVED_MESSAGE =
  "This connector's sync schedule could not be removed, so it may keep running. Try again in a moment; if this keeps happening, ask your admin to check the job queue.";

const ORG_SCHEDULES_NOT_REMOVED_MESSAGE =
  'Some sync schedules in this organization could not be removed, so they may keep running. Try again in a moment; if this keeps happening, ask your admin to check the job queue.';

const PENDING_STATES: JobType[] = ['waiting', 'delayed', 'prioritized'];

// A one-time schedule as it may arrive: either field can be absent.
interface OnceScheduleInput {
  scheduledTime?: unknown;
  scheduleConfig?: { scheduledTime?: unknown };
}

// Interface for storing paused job information
interface PausedJobInfo {
  connector: string;
  connectorId: string;
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

    const provider = crawlingRedisProvider(redisConfig);

    const queueOptions: QueueOptions = {
      // A dedicated blocking-capable client (never the provider's shared
      // `getClient()`): BullMQ issues its own long-lived blocking commands
      // on this connection.
      connection: provider.createClient({ blocking: true }),
      prefix: crawlingQueuePrefix(provider),
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
  private buildJobId(
    connector: string,
    connectorId: string,
    orgId: string,
  ): string {
    return `crawl-${connector.toLowerCase().replace(/\s+/g, '-')}-${connectorId}-${orgId}`;
  }

  // Repeatable jobs may be cron-pattern based or `every`-interval based;
  // match on both fields, plus tz, so callers handle either flavor.
  // Note: BullMQ types `every` as `string | null` on RepeatableJob results
  // but accepts/uses `number` in RepeatOptions input; we coerce to number.
  private repeatOptsMatch(
    a:
      | { pattern?: string | null; every?: number | string | null; tz?: string | null }
      | null
      | undefined,
    b:
      | { pattern?: string | null; every?: number | string | null; tz?: string | null }
      | null
      | undefined,
  ): boolean {
    if (!a || !b) return false;
    if ((a.tz || undefined) !== (b.tz || undefined)) return false;
    if (a.pattern && b.pattern) return a.pattern === b.pattern;
    if (a.every && b.every) return Number(a.every) === Number(b.every);
    return false;
  }

  private buildRemoveRepeatableOpts(repeatableJob: any): RepeatOptions {
    const opts: RepeatOptions = {};
    if (repeatableJob.pattern) opts.pattern = repeatableJob.pattern;
    if (repeatableJob.every) opts.every = Number(repeatableJob.every);
    if (repeatableJob.tz) opts.tz = repeatableJob.tz;
    if (repeatableJob.endDate !== null && repeatableJob.endDate !== undefined) {
      opts.endDate = repeatableJob.endDate;
    }
    return opts;
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

      case CrawlingScheduleType.INTERVAL: {
        // intervalMinutes lives inside the nested scheduleConfig sub-object
        // (IIntervalCrawlingSchedule.scheduleConfig.intervalMinutes), not at
        // the top level of the ICrawlingSchedule envelope.
        const inner = scheduleConfig.scheduleConfig;
        const intervalMinutes = Number(inner?.intervalMinutes);
        if (!Number.isFinite(intervalMinutes) || intervalMinutes < 1) {
          throw new BadRequestError('intervalMinutes must be a positive integer');
        }
        return {
          every: Math.floor(intervalMinutes) * 60 * 1000,
          // Prefer the timezone from the inner config; fall back to the
          // top-level destructure (which defaults to 'UTC').
          tz: (inner?.timezone as string | undefined) ?? timezone,
        };
      }

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
    connectorId: string,
    scheduleConfig: ICrawlingSchedule,
    orgId: string,
    userId: string,
    options: ScheduleJobOptions = {},
  ): Promise<Job<CrawlingJobData>> {
    const jobId = this.buildJobId(connector, connectorId, orgId);

    this.logger.info('Scheduling crawling job', {
      jobId,
      connector,
      connectorId,
      orgId,
      userId,
      scheduleType: scheduleConfig.scheduleType,
      isEnabled: scheduleConfig.isEnabled,
    });

    // A disabled schedule clears the existing one and creates nothing.
    if (!scheduleConfig.isEnabled) {
      await this.removeJobInternal(connector, connectorId, orgId);
      this.pausedJobs.delete(jobId);
      this.logger.info('Schedule is disabled, not creating job', { jobId });
      throw new BadRequestError('Cannot schedule a disabled job');
    }

    const jobData: CrawlingJobData = {
      connector,
      connectorId,
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
      const scheduledTime = this.onceScheduledTime(scheduleConfig);
      const delay = scheduledTime.getTime() - Date.now();

      if (Number.isNaN(delay)) {
        throw new BadRequestError(
          'Scheduled time is missing or is not a valid date',
        );
      }
      if (delay <= 0) {
        throw new BadRequestError('Scheduled time must be in the future');
      }

      jobOptions.delay = delay;
      jobOptions.jobId = jobId; // Use custom job ID for one-time jobs

      this.logger.info('Scheduling one-time job', {
        jobId,
        scheduledTime: scheduledTime.toISOString(),
        delay,
      });
    } else {
      // For repeating jobs
      const repeatOptions = this.transformScheduleConfig(scheduleConfig);
      if (repeatOptions) {
        this.assertSchedulable(repeatOptions);
        jobOptions.repeat = repeatOptions;

        this.logger.info('Scheduling repeating job', {
          jobId,
          pattern: repeatOptions.pattern,
          timezone: repeatOptions.tz,
        });
      }
    }

    // Only now that the new schedule is known to be usable is the old one
    // replaced; a rejected request leaves the existing schedule running.
    await this.removeJobInternal(connector, connectorId, orgId, {
      freeOnceJobId: scheduleConfig.scheduleType === CrawlingScheduleType.ONCE,
    });
    this.pausedJobs.delete(jobId);

    const jobName = this.buildJobName(connector, connectorId);
    const job = await this.queue.add(jobName, jobData, jobOptions);

    // For repeatable jobs, store the mapping to the repeatable job key
    if (scheduleConfig.scheduleType !== CrawlingScheduleType.ONCE) {
      // Wait a moment for the repeatable job to be registered
      await new Promise((resolve) => setTimeout(resolve, 100));

      const repeatableJobs = await this.queue.getRepeatableJobs();
      const repeatOptions = this.transformScheduleConfig(scheduleConfig);
      const matchingRepeatableJob = repeatableJobs.find((rJob) =>
        this.repeatOptsMatch(rJob, repeatOptions),
      );

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
      connectorId,
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
    connectorId: string,
    orgId: string,
    { freeOnceJobId = false }: { freeOnceJobId?: boolean } = {},
  ): Promise<void> {
    const jobId = this.buildJobId(connector, connectorId, orgId);

    try {
      // First, remove any repeatable jobs for this connector/org
      const repeatableJobs = await this.queue.getRepeatableJobs();

      // Get all jobs to find which repeatable job belongs to our connector/org
      const allJobs = await this.queue.getJobs([...PENDING_STATES, 'active']);
      const matchingJobs = allJobs.filter(
        (job) =>
          job.data.connector === connector &&
          job.data.connectorId === connectorId &&
          job.data.orgId === orgId,
      );

      // Remove repeatable jobs that match our connector/org
      for (const repeatableJob of repeatableJobs) {
        // Check if any of our matching jobs uses this repeatable schedule
        const jobUsesThisSchedule = matchingJobs.some((job) =>
          this.repeatOptsMatch(job.opts?.repeat as any, repeatableJob as any),
        );

        if (jobUsesThisSchedule) {
          try {
            await this.queue.removeRepeatable(
              this.buildJobName(connector, connectorId),
              this.buildRemoveRepeatableOpts(repeatableJob),
            );

            this.logger.debug('Removed repeatable job', {
              jobId,
              pattern: repeatableJob.pattern,
              key: repeatableJob.key,
            });
          } catch (error) {
            this.logger.warn('Error removing repeatable job', {
              pattern: repeatableJob.pattern,
              error: error instanceof Error ? error.message : 'Unknown error',
            });
            // Stop before its queued run goes too, so the schedule is left
            // whole and a retry still finds it.
            throw new InternalServerError(SCHEDULE_NOT_REMOVED_MESSAGE);
          }
        }
      }

      const belongsToConnector = (job: Job<CrawlingJobData>): boolean =>
        job.data.connector === connector &&
        job.data.connectorId === connectorId &&
        job.data.orgId === orgId;

      // Runs still waiting to fire go, whatever their number: a one-time run
      // has no repeatable entry, so nothing else removes it. A due run with a
      // priority sits in `prioritized`, not `waiting`.
      const pendingRuns = (
        (await this.queue.getJobs(PENDING_STATES)) as Job<CrawlingJobData>[]
      ).filter(belongsToConnector);

      const history = (
        (await this.queue.getJobs([
          'completed',
          'failed',
        ] as JobType[])) as Job<CrawlingJobData>[]
      )
        .filter(belongsToConnector)
        .sort((a, b) => b.timestamp - a.timestamp);

      // BullMQ ignores an add under a job id that is still taken, and a
      // one-time run keeps its fixed id after it finishes or while it runs.
      const pendingIds = new Set(pendingRuns.map((job) => job.id));
      const onceIdHolders = freeOnceJobId
        ? [...(allJobs as Job<CrawlingJobData>[]), ...history].filter(
            (job) => job.id === jobId && !pendingIds.has(job.id),
          )
        : [];
      const holderIds = new Set(onceIdHolders.map((job) => job.id));

      // Finished runs are history; keep the last 10.
      const oldHistory = history
        .filter((job) => !holderIds.has(job.id))
        .slice(10);

      const stillQueued: Job<CrawlingJobData>[] = [];
      for (const job of pendingRuns) {
        if (!(await this.removeJobInstance(job))) {
          if (await this.stillQueuedAfterFailedRemove(job)) {
            stillQueued.push(job);
          }
        }
      }
      const stillHoldingId: Job<CrawlingJobData>[] = [];
      for (const job of onceIdHolders) {
        if (!(await this.removeJobInstance(job))) {
          if ((await this.readState(job)) !== 'unknown') {
            stillHoldingId.push(job);
          }
        }
      }
      for (const job of oldHistory) {
        await this.removeJobInstance(job);
      }

      if (stillQueued.length > 0) {
        throw new InternalServerError(RUN_NOT_CANCELLED_MESSAGE, {
          jobIds: stillQueued.map((job) => job.id),
        });
      }
      if (stillHoldingId.length > 0) {
        throw new InternalServerError(ONCE_ID_TAKEN_MESSAGE, {
          jobIds: stillHoldingId.map((job) => job.id),
        });
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
      // Callers report success once this returns, so a queue that could not
      // be read must not look like a schedule that was removed.
      throw error;
    }
  }

  // Null when the state could not be read, which is not the same as BullMQ's
  // `unknown` for a job that no longer exists.
  private async readState(job: Job<CrawlingJobData>): Promise<string | null> {
    try {
      return await job.getState();
    } catch {
      return null;
    }
  }

  // A run a worker took in the meantime is no longer queued; a state that
  // cannot be read is treated as still queued.
  private async stillQueuedAfterFailedRemove(
    job: Job<CrawlingJobData>,
  ): Promise<boolean> {
    const state = await this.readState(job);
    return state === null || (PENDING_STATES as string[]).includes(state);
  }

  private async removeJobInstance(job: Job<CrawlingJobData>): Promise<boolean> {
    try {
      await job.remove();
      this.logger.debug('Removed old job instance', { jobId: job.id });
      return true;
    } catch (error) {
      this.logger.warn('Failed to remove job instance', {
        jobId: job.id,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return false;
    }
  }

  /**
   * Remove a job (public method)
   */
  async removeJob(
    connector: string,
    connectorId: string,
    orgId: string,
  ): Promise<void> {
    const jobId = this.buildJobId(connector, connectorId, orgId);

    this.logger.info('Removing crawling job', {
      jobId,
      connector,
      connectorId,
      orgId,
    });

    await this.removeJobInternal(connector, connectorId, orgId);

    // Also remove from paused jobs if it exists
    this.pausedJobs.delete(jobId);

    this.logger.info('Successfully removed crawling job', {
      jobId,
      connector,
      connectorId,
      orgId,
    });
  }

  /**
   * Get job status for a specific connector type
   */
  async getJobStatus(
    connector: string,
    connectorId: string,
    orgId: string,
  ): Promise<JobStatus | null> {
    const jobId = this.buildJobId(connector, connectorId, orgId);

    this.logger.debug('Getting job status', {
      jobId,
      connector,
      connectorId,
      orgId,
    });

    try {
      // First check if the job is paused
      const pausedJob = this.pausedJobs.get(jobId);
      if (pausedJob) {
        this.logger.debug('Job is paused', {
          jobId,
          connector,
          connectorId,
          orgId,
        });
        return {
          id: jobId,
          name: this.buildJobName(connector, connectorId),
          data: {
            connector,
            connectorId,
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

      // Get all *active-schedule* jobs for this connector/org combination.
      // We intentionally exclude 'completed' because BullMQ keeps the last
      // completed record in Redis even after the repeatable schedule has been
      // removed (it is cleaned up by removeOnComplete TTL, not by removeJob).
      // Including 'completed' would make getJobStatus return a stale history
      // record and falsely report the job as still scheduled after it has been
      // deleted.
      const allJobs = await this.queue.getJobs([
        ...PENDING_STATES,
        'active',
        'failed',
      ]);

      const matchingJobs = allJobs.filter(
        (job) =>
          job.data.connector === connector &&
          job.data.connectorId === connectorId &&
          job.data.orgId === orgId,
      );

      if (matchingJobs.length === 0) {
        this.logger.debug('No jobs found', {
          jobId,
          connector,
          connectorId,
          orgId,
        });
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
      // Exclude 'completed' for the same reason as getJobStatus: after a
      // repeatable schedule is removed, BullMQ keeps the last completed record
      // in Redis until removeOnComplete TTL expires. Including 'completed' would
      // make deleted jobs still appear in this listing.
      const jobs = await this.queue.getJobs([
        ...PENDING_STATES,
        'active',
        'failed',
      ]);

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
            name: this.buildJobName(pausedJob.connector, pausedJob.connectorId),
            data: {
              connector: pausedJob.connector,
              connectorId: pausedJob.connectorId,
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
              this.repeatOptsMatch(job.opts?.repeat as any, repeatableJob as any),
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
  async pauseJob(
    connector: string,
    connectorId: string,
    orgId: string,
  ): Promise<void> {
    const jobId = this.buildJobId(connector, connectorId, orgId);

    this.logger.info('Pausing job', { jobId, connector, orgId });

    try {
      // First, get the current job to store its configuration
      const currentJobStatus = await this.getJobStatus(
        connector,
        connectorId,
        orgId,
      );

      if (!currentJobStatus) {
        throw new BadRequestError('No active job found to pause');
      }

      if (currentJobStatus.state === 'paused') {
        throw new BadRequestError('Job is already paused');
      }

      // Store the job configuration for later resume
      const pausedJobInfo: PausedJobInfo = {
        connector,
        connectorId,
        scheduleConfig: currentJobStatus.data.scheduleConfig,
        orgId,
        userId: currentJobStatus.data.userId,
        options: {
          metadata: currentJobStatus.data.metadata,
        },
        pausedAt: new Date(),
      };

      // Marked paused only once its runs are gone, so a failed removal
      // leaves a schedule that still runs and says so.
      await this.removeJobInternal(connector, connectorId, orgId);
      this.pausedJobs.set(jobId, pausedJobInfo);

      this.logger.info('Job paused successfully', {
        jobId,
        connector,
        connectorId,
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
  async resumeJob(
    connector: string,
    connectorId: string,
    orgId: string,
  ): Promise<void> {
    const jobId = this.buildJobId(connector, connectorId, orgId);

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
        connectorId,
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
        connectorId,
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
      // A schedule that could not be removed keeps its queued run, so it is
      // left whole and a retry still finds it.
      const keptJobNames = new Set<string>();
      let notRemoved = 0;

      for (const repeatableJob of repeatableJobs) {
        // Check if this repeatable job belongs to the org
        const jobs = (await this.queue.getJobs([
          ...PENDING_STATES,
          'active',
        ])) as Job<CrawlingJobData>[];
        const matchingJob = jobs.find(
          (job) =>
            job.data.orgId === orgId &&
            this.repeatOptsMatch(job.opts.repeat as any, repeatableJob as any),
        );

        if (matchingJob) {
          // Get the job name for this connector type
          const jobName = this.buildJobName(
            matchingJob.data.connector,
            matchingJob.data.connectorId,
          );
          try {
            // Skip if we already processed this job name
            if (processedJobNames.has(jobName)) continue;
            processedJobNames.add(jobName);

            await this.queue.removeRepeatable(
              jobName,
              this.buildRemoveRepeatableOpts(repeatableJob),
            );
            this.logger.debug('Removed repeatable job', {
              jobId: repeatableJob.id,
              jobName,
              orgId,
            });
          } catch (error) {
            notRemoved += 1;
            keptJobNames.add(jobName);
            this.logger.warn('Failed to remove repeatable job', {
              jobId: repeatableJob.id,
              jobName,
              orgId,
              error: error instanceof Error ? error.message : 'Unknown error',
            });
          }
        }
      }

      const ofOrg = (jobs: Job<CrawlingJobData>[]): Job<CrawlingJobData>[] =>
        jobs.filter((job) => job.data.orgId === orgId);
      const pendingRuns = ofOrg(
        (await this.queue.getJobs(PENDING_STATES)) as Job<CrawlingJobData>[],
      ).filter((job) => !keptJobNames.has(job.name));
      // A running job is locked by its worker and finished ones are history,
      // so failing to remove those does not fail the request.
      const otherRuns = ofOrg(
        (await this.queue.getJobs([
          'active',
          'completed',
          'failed',
        ])) as Job<CrawlingJobData>[],
      );

      for (const job of pendingRuns) {
        if (
          !(await this.removeJobInstance(job)) &&
          (await this.stillQueuedAfterFailedRemove(job))
        ) {
          notRemoved += 1;
        }
      }
      for (const job of otherRuns) {
        await this.removeJobInstance(job);
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

      if (notRemoved > 0) {
        throw new InternalServerError(ORG_SCHEDULES_NOT_REMOVED_MESSAGE, {
          notRemoved,
        });
      }

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
    connectorId: string,
    orgId: string,
  ): Promise<any> {
    const customJobId = this.buildJobId(connector, connectorId, orgId);
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
      (job) => job.data.connector === connector && job.data.connectorId === connectorId && job.data.orgId === orgId,
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
      connectorId,
      orgId,
    };
  }

  // BullMQ parses the cron pattern and timezone only inside queue.add; asking
  // it for the next run first turns a bad one into a 400.
  private assertSchedulable(repeat: RepeatOptions): void {
    let next: number | undefined;
    try {
      next = getNextMillis(Date.now(), repeat);
    } catch {
      next = undefined;
    }
    if (next === undefined) {
      throw new BadRequestError(UNSCHEDULABLE_MESSAGE);
    }
  }

  // The API validator and OpenAPI spec put scheduledTime at the top level; the
  // typed shape nests it under scheduleConfig. Accept either.
  private onceScheduledTime(schedule: IOnceCrawlingSchedule): Date {
    const { scheduledTime, scheduleConfig } =
      schedule as unknown as OnceScheduleInput;
    const value = scheduledTime ?? scheduleConfig?.scheduledTime;
    return new Date(
      value instanceof Date || typeof value === 'string' ? value : NaN,
    );
  }

  private buildJobName(connector: string, connectorId: string): string {
    return `crawl-${connector.toLowerCase().replace(/\s+/g, '-')}-${connectorId}`;
  }
}

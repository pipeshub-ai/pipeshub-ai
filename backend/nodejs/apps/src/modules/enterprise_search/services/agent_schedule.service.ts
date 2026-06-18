import { Queue, Worker } from 'bullmq';
import axios from 'axios';
import { inject, injectable } from 'inversify';
import { AppConfig } from '../../tokens_manager/config/config';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { Logger } from '../../../libs/services/logger.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { extractScheduledTriggersFromFlow } from '../utils/agent_schedule.util';

type AgentScheduleOwner = {
  orgId: string;
  userId: string;
  email: string;
};

type AgentScheduleJobData = {
  agentKey: string;
  triggerId: string;
  orgId: string;
  userId: string;
  email: string;
  input: string;
  timezone: string;
};

const QUEUE_NAME = 'agent-scheduler';
const JOB_NAME_PREFIX = 'agent-schedule';

@injectable()
export class AgentScheduleService {
  private readonly logger: Logger;
  private readonly authTokenService: AuthTokenService;
  private readonly queue: Queue<AgentScheduleJobData>;
  private readonly worker: Worker<AgentScheduleJobData>;

  constructor(@inject('AppConfig') private readonly appConfig: AppConfig) {
    this.logger = Logger.getInstance({ service: 'AgentScheduleService' });
    this.authTokenService = new AuthTokenService(
      this.appConfig.jwtSecret,
      this.appConfig.scopedJwtSecret,
    );

    const connection = {
      host: this.appConfig.redis.host,
      port: this.appConfig.redis.port,
      username: this.appConfig.redis.username,
      password: this.appConfig.redis.password,
      db: this.appConfig.redis.db || 0,
    };

    this.queue = new Queue<AgentScheduleJobData>(QUEUE_NAME, {
      connection,
      defaultJobOptions: {
        removeOnComplete: 20,
        removeOnFail: 50,
        attempts: 3,
        backoff: {
          type: 'exponential',
          delay: 5000,
        },
      },
    });

    this.worker = new Worker<AgentScheduleJobData>(
      QUEUE_NAME,
      async (job) => {
        await this.runScheduledTrigger(job.data);
      },
      {
        connection,
        concurrency: 2,
      },
    );

    this.worker.on('failed', (job, error) => {
      this.logger.error('Scheduled agent trigger failed', {
        jobId: job?.id,
        agentKey: job?.data?.agentKey,
        triggerId: job?.data?.triggerId,
        error: error?.message,
      });
    });

    this.worker.on('completed', (job) => {
      this.logger.info('Scheduled agent trigger completed', {
        jobId: job.id,
        agentKey: job.data.agentKey,
        triggerId: job.data.triggerId,
      });
    });
  }

  private buildJobName(agentKey: string, triggerId: string): string {
    return `${JOB_NAME_PREFIX}:${agentKey}:${triggerId}`;
  }

  private buildJobId(agentKey: string, triggerId: string, orgId: string): string {
    return `${JOB_NAME_PREFIX}-${agentKey}-${triggerId}-${orgId}`;
  }

  private async runScheduledTrigger(data: AgentScheduleJobData): Promise<void> {
    const token = this.authTokenService.generateScopedToken(
      {
        email: data.email,
        orgId: data.orgId,
        userId: data.userId,
        scopes: [TokenScopes.CONVERSATION_CREATE],
      },
      '1h',
    );

    const url = `${this.appConfig.esBackend}/api/v1/agents/${encodeURIComponent(
      data.agentKey,
    )}/conversations/internal/stream`;

    const response = await axios.post(
      url,
      {
        query: data.input,
        timezone: data.timezone,
        chatMode: 'auto',
      },
      {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
          'x-agent-scheduled-trigger': 'true',
        },
        responseType: 'stream',
      },
    );

    await new Promise<void>((resolve, reject) => {
      response.data.on('end', () => resolve());
      response.data.on('error', (err: unknown) => reject(err));
      response.data.resume();
    });
  }

  async syncAgentScheduleFromFlow(
    agentKey: string,
    flow: unknown,
    owner: AgentScheduleOwner,
  ): Promise<void> {
    if (!agentKey) return;

    await this.removeSchedulesForAgent(agentKey);

    const triggers = extractScheduledTriggersFromFlow(flow);
    if (triggers.length === 0) {
      return;
    }

    for (const trigger of triggers) {
      const jobName = this.buildJobName(agentKey, trigger.triggerId);
      const jobId = this.buildJobId(agentKey, trigger.triggerId, owner.orgId);

      await this.queue.add(
        jobName,
        {
          agentKey,
          triggerId: trigger.triggerId,
          orgId: owner.orgId,
          userId: owner.userId,
          email: owner.email,
          input: trigger.input,
          timezone: trigger.timezone,
        },
        {
          jobId,
          repeat: {
            pattern: trigger.cronExpression,
            tz: trigger.timezone,
          },
        },
      );
    }

    this.logger.info('Synchronized agent schedules', {
      agentKey,
      triggerCount: triggers.length,
      ownerOrgId: owner.orgId,
    });
  }

  async removeSchedulesForAgent(agentKey: string): Promise<void> {
    if (!agentKey) return;

    const namePrefix = `${JOB_NAME_PREFIX}:${agentKey}:`;
    const repeatableJobs = await this.queue.getRepeatableJobs();
    for (const repeatableJob of repeatableJobs) {
      if (repeatableJob.name?.startsWith(namePrefix)) {
        await this.queue.removeRepeatableByKey(repeatableJob.key);
      }
    }

    const jobs = await this.queue.getJobs(['waiting', 'active', 'delayed', 'failed', 'completed']);
    for (const job of jobs) {
      if (job.data?.agentKey === agentKey) {
        await job.remove();
      }
    }
  }

  async shutdown(): Promise<void> {
    await this.worker.close();
    await this.queue.close();
  }
}

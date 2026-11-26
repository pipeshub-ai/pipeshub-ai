import { EXECUTION_STATUSES } from '../../../../modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import { TERMINAL_RUN_STATUSES } from '../../../../modules/agent-harness/domain/execution';
import type {
  RunStateReader,
  RunStateSnapshot,
} from '../../../../modules/agent-harness/ports/run-state';
import { AgentRunModel } from '../execution/schema/execution.schema';
import { MongoTime, mongoServerTime } from '../time/mongo';

const ACTIVE = EXECUTION_STATUSES.filter(
  (status) => !TERMINAL_RUN_STATUSES.has(status),
);

/**
 * The run store's state for the merged metrics route (OB-04, OB-05). Only runs that have not ended are read: each
 * query starts on `status` (the `{status, leaseExpiresAt}` index), so its cost follows the live runs, never the
 * collection's history.
 */
export class MongoRunState implements RunStateReader {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  async snapshot(): Promise<RunStateSnapshot> {
    const now = await this.time.read();
    const [byStatus, sessions, leaseExpired, oldest, waiting] =
      await Promise.all([
        AgentRunModel.aggregate<{ _id: string; n: number }>([
          { $match: { status: { $in: ACTIVE } } },
          { $group: { _id: '$status', n: { $sum: 1 } } },
        ]),
        AgentRunModel.aggregate<{ n: number }>([
          {
            $match: {
              status: { $in: ACTIVE },
              'parent.runId': { $exists: false },
            },
          },
          { $group: { _id: { orgId: '$orgId', sessionId: '$sessionId' } } },
          { $count: 'n' },
        ]),
        AgentRunModel.countDocuments({
          status: 'running',
          leaseExpiresAt: { $lt: now },
        }),
        AgentRunModel.findOne({ status: 'running' }, { createdAt: 1 })
          .sort({ createdAt: 1 })
          .lean<{ createdAt?: number }>(),
        // A waiting run commits nothing until a person answers: its last update is when it began to wait.
        AgentRunModel.findOne({ status: 'waiting_approval' }, { updatedAt: 1 })
          .sort({ updatedAt: 1 })
          .lean<{ updatedAt?: number }>(),
      ]);
    return {
      byStatus: Object.fromEntries(byStatus.map((row) => [row._id, row.n])),
      activeSessions: sessions[0]?.n ?? 0,
      leaseExpired,
      oldestRunningAgeS:
        typeof oldest?.createdAt === 'number'
          ? Math.max(0, now - oldest.createdAt) / 1000
          : null,
      oldestApprovalWaitS:
        typeof waiting?.updatedAt === 'number'
          ? Math.max(0, now - waiting.updatedAt) / 1000
          : null,
    };
  }
}

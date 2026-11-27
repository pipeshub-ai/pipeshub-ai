import type { RunStatus } from '../../../../modules/agent-harness/domain/execution';
import type { RunStatusReader } from '../../../../modules/agent-harness/ports/schedules';
import { AgentRunModel } from '../execution/schema/execution.schema';

/** A run's status by its unique `orgId_1_runId_1` index, for the approval watch (P8a). */
export class MongoRunStatusReader implements RunStatusReader {
  async status(orgId: string, runId: string): Promise<RunStatus | null> {
    const found = await AgentRunModel.findOne({ orgId, runId })
      .select('status')
      .lean<{ status: RunStatus }>();
    return found?.status ?? null;
  }
}

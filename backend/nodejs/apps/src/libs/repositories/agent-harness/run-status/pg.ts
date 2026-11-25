import type { RunStatus } from '../../../../modules/agent-harness/domain/execution';
import type { RunStatusReader } from '../../../../modules/agent-harness/ports/schedules';
import { inOrgRead } from '../execution/pg-rows';

/** A run's status by its key, for the approval watch (P8a). */
export class PgRunStatusReader implements RunStatusReader {
  status(orgId: string, runId: string): Promise<RunStatus | null> {
    return inOrgRead(orgId, async (k) => {
      const { rows } = await k.query<{ status: RunStatus }>(
        `/* pipeshub:harness.runs_status */ SELECT status FROM harness.runs WHERE org_id = $1 AND run_id = $2`,
        [orgId, runId],
      );
      return rows[0]?.status ?? null;
    });
  }
}

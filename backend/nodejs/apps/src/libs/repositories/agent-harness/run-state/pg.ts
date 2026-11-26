import { EXECUTION_STATUSES } from '../../../../modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import { TERMINAL_RUN_STATUSES } from '../../../../modules/agent-harness/domain/execution';
import type {
  RunStateReader,
  RunStateSnapshot,
} from '../../../../modules/agent-harness/ports/run-state';
import { jobsUnit } from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';

const ACTIVE = EXECUTION_STATUSES.filter(
  (status) => !TERMINAL_RUN_STATUSES.has(status),
);

/**
 * The run store's state for the merged metrics route (OB-04, OB-05), deployment-wide as the jobs role. Only runs that
 * have not ended are read: each statement starts on `status`, so its cost follows the live runs.
 */
export class PgRunState implements RunStateReader {
  constructor(private readonly time: PgTime = pgServerTime) {}

  snapshot(): Promise<RunStateSnapshot> {
    return jobsUnit('agent-harness.run-state', { write: false }, async (k) => {
      const now = await this.time.now(k);
      const byStatus = await k.query<{ status: string; n: string }>(
        `/* pipeshub:harness.run_state_by_status */ SELECT status, count(*)::text AS n FROM harness.runs
          WHERE status = ANY($1::text[]) GROUP BY status`,
        [ACTIVE],
      );
      const summary = await k.query<{
        sessions: string;
        lease_expired: string;
        oldest_running: number | null;
        oldest_waiting: number | null;
      }>(
        `/* pipeshub:harness.run_state_summary */ SELECT
           (SELECT count(*) FROM (SELECT DISTINCT org_id, session_id FROM harness.runs
              WHERE status = ANY($1::text[]) AND parent_run_id IS NULL) s)::text AS sessions,
           (SELECT count(*) FROM harness.runs WHERE status = 'running' AND lease_expires_at < $2::double precision)::text
             AS lease_expired,
           (SELECT min(created_at) FROM harness.runs WHERE status = 'running') AS oldest_running,
           (SELECT min(updated_at) FROM harness.runs WHERE status = 'waiting_approval') AS oldest_waiting`,
        [ACTIVE, now],
      );
      const row = summary.rows[0];
      const age = (at: number | null | undefined): number | null =>
        typeof at === 'number' ? Math.max(0, now - at) / 1000 : null;
      return {
        byStatus: Object.fromEntries(
          byStatus.rows.map((r) => [r.status, Number(r.n)]),
        ),
        activeSessions: Number(row?.sessions ?? 0),
        leaseExpired: Number(row?.lease_expired ?? 0),
        // A waiting run commits nothing until a person answers: its last update is when it began to wait.
        oldestRunningAgeS: age(row?.oldest_running),
        oldestApprovalWaitS: age(row?.oldest_waiting),
      };
    });
  }
}

import type { Tx } from '../../uow';
import type {
  AbandonedFork,
  AbandonedForks,
} from '../../../../modules/agent-harness/ports/session-forks';
import { jobsUnit, orgUnit } from '../pg-documents/pg';

/** `harness.session_forks` records left uncommitted (CLEANUP-G4): listed across orgs as the jobs role. */
export class PgAbandonedForks implements AbandonedForks<Tx> {
  list(before: number, limit: number): Promise<AbandonedFork[]> {
    if (limit <= 0) return Promise.resolve([]);
    return jobsUnit('abandoned-forks', { write: false }, async (k) => {
      const { rows } = await k.query<{
        org_id: string;
        id: string;
        fork_session_id: string;
      }>(
        `/* pipeshub:harness.abandoned_forks */ SELECT org_id, id, fork_session_id FROM harness.session_forks
          WHERE result IS NULL AND created_at < $1 ORDER BY created_at, org_id, id LIMIT $2`,
        [before, limit],
      );
      return rows.map((row) => ({
        orgId: row.org_id,
        recordId: row.id,
        forkSessionId: row.fork_session_id,
      }));
    });
  }

  dropMany(
    orgId: string,
    forks: readonly AbandonedFork[],
    before: number,
    tx: Tx,
  ): Promise<AbandonedFork[]> {
    if (forks.length === 0) return Promise.resolve([]);
    return orgUnit(tx, orgId, { write: true }, async (k) => {
      // Only while still uncommitted: a commit racing the sweep wins.
      const { rows } = await k.query<{ id: string; fork_session_id: string }>(
        `/* pipeshub:harness.abandoned_forks_drop */ DELETE FROM harness.session_forks AS f
          USING unnest($2::text[], $3::text[]) AS d(id, fork_session_id)
          WHERE f.org_id = $1 AND f.id = d.id AND f.fork_session_id = d.fork_session_id
            AND f.result IS NULL AND f.created_at < $4
          RETURNING f.id, f.fork_session_id`,
        [
          orgId,
          forks.map((fork) => fork.recordId),
          forks.map((fork) => fork.forkSessionId),
          before,
        ],
      );
      return rows.map((row) => ({
        orgId,
        recordId: row.id,
        forkSessionId: row.fork_session_id,
      }));
    });
  }
}

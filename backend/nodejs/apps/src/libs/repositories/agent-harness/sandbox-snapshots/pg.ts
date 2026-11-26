import type {
  SandboxSnapshotIndex,
  StoredSandboxSnapshot,
} from '../../../../modules/agent-harness/ports/sandbox-snapshots';
import type { Tx } from '../../uow';
import { jobsUnit, rowRegion } from '../pg-documents/pg';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';

interface SnapshotRow {
  org_id: string;
  session_id: string;
  run_id: string | null;
  object_id: string;
  taken_ms: string;
  snapshot_region: string;
}

const snapshotOf = (row: SnapshotRow): StoredSandboxSnapshot => ({
  orgId: row.org_id,
  sessionId: row.session_id,
  runId: row.run_id,
  objectId: row.object_id,
  takenAt: Number(row.taken_ms),
  region: row.snapshot_region,
});

/** The sandbox snapshot index on PostgreSQL (`harness.sandbox_snapshots`): one row per stored object, latest time kept. */
export class PgSandboxSnapshotIndex implements SandboxSnapshotIndex<Tx> {
  async record(
    snapshots: readonly StoredSandboxSnapshot[],
    tx?: Tx,
  ): Promise<void> {
    const first = snapshots[0];
    if (first === undefined) return;
    if (snapshots.some((snapshot) => snapshot.orgId !== first.orgId))
      throw new Error('one call records the snapshots of one organisation');
    await orgOrJobsUnit(
      tx,
      first.orgId,
      'sandbox-snapshots',
      { write: true },
      (k) =>
        k.query(
          `/* pipeshub:harness.sandbox_snapshots_record */ INSERT INTO harness.sandbox_snapshots AS s
           (org_id, object_id, session_id, run_id, taken_at, snapshot_region, region)
         SELECT $1, o, se, ru, to_timestamp(t::double precision / 1000), re, $7
           FROM unnest($2::text[], $3::text[], $4::text[], $5::bigint[], $6::text[]) AS u(o, se, ru, t, re)
         ON CONFLICT (org_id, object_id) DO UPDATE SET taken_at = greatest(s.taken_at, EXCLUDED.taken_at)`,
          [
            first.orgId,
            snapshots.map((s) => s.objectId),
            snapshots.map((s) => s.sessionId),
            snapshots.map((s) => s.runId),
            snapshots.map((s) => s.takenAt),
            snapshots.map((s) => s.region),
            rowRegion(),
          ],
        ),
    );
  }

  orgs(): Promise<string[]> {
    return jobsUnit('sandbox-snapshots', { write: false }, async (k) => {
      const { rows } = await k.query<{ org_id: string }>(
        `/* pipeshub:harness.sandbox_snapshots_orgs */ SELECT DISTINCT org_id FROM harness.sandbox_snapshots`,
      );
      return rows.map((row) => row.org_id);
    });
  }

  takenBefore(
    orgId: string,
    before: number,
    limit: number,
  ): Promise<StoredSandboxSnapshot[]> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'sandbox-snapshots',
      { write: false },
      async (k) => {
        const { rows } = await k.query<SnapshotRow>(
          `/* pipeshub:harness.sandbox_snapshots_before */ SELECT org_id, session_id, run_id, object_id,
                (extract(epoch FROM taken_at) * 1000)::bigint::text AS taken_ms, snapshot_region
           FROM harness.sandbox_snapshots
          WHERE org_id = $1 AND taken_at < to_timestamp($2::double precision / 1000)
          ORDER BY taken_at, object_id LIMIT $3`,
          [orgId, before, limit],
        );
        return rows.map(snapshotOf);
      },
    );
  }

  async forget(
    orgId: string,
    objectIds: readonly string[],
    before: number,
  ): Promise<number> {
    if (objectIds.length === 0) return 0;
    return orgOrJobsUnit(
      undefined,
      orgId,
      'sandbox-snapshots',
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.sandbox_snapshots_forget */ DELETE FROM harness.sandbox_snapshots
          WHERE org_id = $1 AND object_id = ANY($2::text[]) AND taken_at < to_timestamp($3::double precision / 1000)`,
          [orgId, [...objectIds], before],
        );
        return rowCount ?? 0;
      },
    );
  }

  forgetSession(orgId: string, sessionId: string): Promise<number> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'sandbox-snapshots',
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.sandbox_snapshots_forget_session */ DELETE FROM harness.sandbox_snapshots
          WHERE org_id = $1 AND session_id = $2`,
          [orgId, sessionId],
        );
        return rowCount ?? 0;
      },
    );
  }
}

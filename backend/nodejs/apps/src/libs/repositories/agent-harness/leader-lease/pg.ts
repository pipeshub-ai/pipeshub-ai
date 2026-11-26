import { LeaderLease } from '../../../../modules/agent-harness/ports/leader-lease';
import { deploymentUnit } from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';

/**
 * A deployment-wide leader lease on PostgreSQL (`harness.leases`, no org: the sweeper's and the firer's leadership is
 * the deployment's). The takeover rule (ours, absent, or lapsed on database time) is the upsert's own condition, so two
 * candidates racing for a lapsed lease serialize on the row and exactly one of them holds it.
 */
export class PgLeaderLease implements LeaderLease {
  constructor(private readonly time: PgTime = pgServerTime) {}

  tryAcquire(name: string, holder: string, ttlMs: number): Promise<boolean> {
    return deploymentUnit({ write: true }, async (k) => {
      const now = await this.time.now(k);
      const { rows } = await k.query<{ holder: string }>(
        `/* pipeshub:harness.leases_acquire */ INSERT INTO harness.leases AS l (name, holder, expires_at_ms)
           VALUES ($1, $2, $3::bigint + $4::bigint)
         ON CONFLICT (name) DO UPDATE SET
           holder = CASE WHEN l.holder = $2 OR l.expires_at_ms <= $3 THEN $2 ELSE l.holder END,
           expires_at_ms = CASE WHEN l.holder = $2 OR l.expires_at_ms <= $3 THEN $3::bigint + $4::bigint ELSE l.expires_at_ms END
         RETURNING holder`,
        [name, holder, now, ttlMs],
      );
      return rows[0]?.holder === holder;
    });
  }

  async release(name: string, holder: string): Promise<void> {
    await deploymentUnit({ write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.leases_release */ DELETE FROM harness.leases WHERE name = $1 AND holder = $2`,
        [name, holder],
      ),
    );
  }
}

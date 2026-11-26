import { jobsUnit } from '../pg-documents/pg';

/**
 * Every org not deleted, read as the jobs role through identity_0005's vetted `identity.gauge_live_orgs()`: since
 * identity_0006 the jobs role holds no SELECT on `identity.orgs`. The orgs a whole control-plane data migration covers.
 */
export function pgActiveOrgIds(): Promise<string[]> {
  return jobsUnit('agent-harness.active-orgs', { write: false }, async (k) => {
    const { rows } = await k.query<{ org_id: string }>(
      `/* pipeshub:harness.active_orgs */ SELECT org_id FROM identity.gauge_live_orgs() ORDER BY org_id`,
    );
    return rows.map((row) => row.org_id);
  });
}

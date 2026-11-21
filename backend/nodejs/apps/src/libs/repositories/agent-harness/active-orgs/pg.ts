import { jobsUnit } from '../pg-documents/pg';

/** Every org not deleted (`identity.orgs`, read as the jobs role): the orgs a whole control-plane data migration covers. */
export function pgActiveOrgIds(): Promise<string[]> {
  return jobsUnit('agent-harness.active-orgs', { write: false }, async (k) => {
    const { rows } = await k.query<{ org_id: string }>(
      `/* pipeshub:harness.active_orgs */ SELECT org_id FROM identity.orgs WHERE NOT is_deleted ORDER BY org_id`,
    );
    return rows.map((row) => row.org_id);
  });
}

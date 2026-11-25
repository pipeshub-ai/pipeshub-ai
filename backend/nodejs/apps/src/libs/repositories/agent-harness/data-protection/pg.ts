import type { WrappedDataKey } from '../../../../modules/agent-harness/domain/data-protection';
import type { DataKeyStore } from '../../../../modules/agent-harness/ports/data-keys';
import { jobsUnit, rowRegion } from '../pg-documents/pg';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';

interface KeyRow {
  org_id: string;
  kid: string;
  version: number;
  kek_id: string;
  wrapped: string;
  created_at_ms: string;
}

/**
 * Wrapped data keys on PostgreSQL (`harness.data_keys`). A version is unique per org and a seal count is one
 * conditional increment, so concurrent creators converge on one key and concurrent sealers never share a count. A
 * key id is unique within its org; across orgs no row of another org is visible, and the KEK binds each wrapped key
 * to its org, so another org's id unwraps nothing here.
 */
export class PgDataKeyStore implements DataKeyStore {
  list(orgId: string): Promise<WrappedDataKey[]> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'data-keys',
      { write: false },
      async (k) => {
        const { rows } = await k.query<KeyRow>(
          `/* pipeshub:harness.data_keys_list */ SELECT org_id, kid, version, kek_id, wrapped, created_at_ms::text
           FROM harness.data_keys WHERE org_id = $1 ORDER BY version`,
          [orgId],
        );
        return rows.map((row) => ({
          orgId: row.org_id,
          kid: row.kid,
          version: row.version,
          kekId: row.kek_id,
          wrapped: row.wrapped,
          createdAt: Number(row.created_at_ms),
        }));
      },
    );
  }

  insert(key: WrappedDataKey): Promise<boolean> {
    return orgOrJobsUnit(
      undefined,
      key.orgId,
      'data-keys',
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.data_keys_insert */ INSERT INTO harness.data_keys
           (org_id, kid, version, kek_id, wrapped, created_at_ms, region) VALUES ($1, $2, $3, $4, $5, $6, $7)
         ON CONFLICT DO NOTHING`,
          [
            key.orgId,
            key.kid,
            key.version,
            key.kekId,
            key.wrapped,
            key.createdAt,
            rowRegion(),
          ],
        );
        return rowCount === 1;
      },
    );
  }

  reserveSeals(
    orgId: string,
    kid: string,
    count: number,
  ): Promise<number | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'data-keys',
      { write: true },
      async (k) => {
        const { rows } = await k.query<{ seals: string }>(
          `/* pipeshub:harness.data_keys_reserve_seals */ UPDATE harness.data_keys SET seals = seals + $3
          WHERE org_id = $1 AND kid = $2 RETURNING seals::text`,
          [orgId, kid, count],
        );
        return rows[0] === undefined ? null : Number(rows[0].seals);
      },
    );
  }

  deleteAll(orgId: string): Promise<number> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'data-keys',
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.data_keys_delete_all */ DELETE FROM harness.data_keys WHERE org_id = $1`,
          [orgId],
        );
        return rowCount ?? 0;
      },
    );
  }

  orgIds(): Promise<string[]> {
    return jobsUnit(
      'agent-harness.secret-key-rotation',
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ org_id: string }>(
          `/* pipeshub:harness.data_keys_orgs */ SELECT DISTINCT org_id FROM harness.data_keys ORDER BY org_id`,
        );
        return rows.map((row) => row.org_id);
      },
    );
  }

  replaceWrap(
    orgId: string,
    kid: string,
    expectedKekId: string,
    next: { kekId: string; wrapped: string },
  ): Promise<boolean> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'data-keys',
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.data_keys_replace_wrap */ UPDATE harness.data_keys SET kek_id = $4, wrapped = $5
          WHERE org_id = $1 AND kid = $2 AND kek_id = $3`,
          [orgId, kid, expectedKekId, next.kekId, next.wrapped],
        );
        return rowCount === 1;
      },
    );
  }
}

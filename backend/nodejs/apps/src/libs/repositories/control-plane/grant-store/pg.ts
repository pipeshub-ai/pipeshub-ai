import {
  grantSchema,
  MAX_GRANT_ENTRIES,
  type Grant,
  type GrantEntry,
  type SubjectRef,
} from '../../../../modules/control-plane/domain/objects';
import { ObjectTooLarge } from '../../../../modules/control-plane/domain/store-errors';
import type { GrantStore } from '../../../../modules/control-plane/ports/grant-store';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import {
  checkedObject,
  compareAndSet,
} from '../../../../modules/control-plane/adapters/store-checks';
import { rowRegion } from '../../agent-harness/pg-documents/pg';
import { cpUnit } from '../pg-units';

interface Row {
  org_id: string;
  subject: string;
  entries: GrantEntry[];
  revision: string;
}

const fromRow = (row: Row): Grant =>
  grantSchema.parse({
    schemaVersion: 1,
    orgId: row.org_id,
    subject: row.subject,
    entries: row.entries,
    revision: Number(row.revision),
  });

/** `GrantStore` over `harness.cp_grants`, one row per subject; a put is a compare-and-set on `revision`. */
export class PgGrantStore implements GrantStore {
  async get(orgId: string, subject: SubjectRef): Promise<Grant | null> {
    return (await this.getMany(orgId, [subject])).get(subject) ?? null;
  }

  getMany(
    orgId: string,
    subjects: readonly SubjectRef[],
  ): Promise<Map<SubjectRef, Grant>> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<Row>(
        `/* pipeshub:harness.cp_grant_get */ SELECT org_id, subject, entries, revision::text FROM harness.cp_grants
          WHERE org_id = $1 AND subject = ANY($2::text[])`,
        [orgId, [...subjects]],
      );
      return new Map(rows.map((row) => [row.subject, fromRow(row)]));
    });
  }

  async put(
    tx: StoreTx,
    grant: Grant,
    expectedRevision: number,
  ): Promise<number> {
    if (grant.entries.length > MAX_GRANT_ENTRIES) {
      throw new ObjectTooLarge(
        `a grant holds at most ${MAX_GRANT_ENTRIES} entries, not ${grant.entries.length}`,
      );
    }
    const checked = checkedObject(
      tx,
      grantSchema,
      grant,
      `grant ${grant.subject}`,
    );
    const revision = expectedRevision + 1;
    const what = `grant ${checked.subject}`;
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        const entries = JSON.stringify(checked.entries);
        const moved =
          expectedRevision === 0
            ? await k.query(
                `/* pipeshub:harness.cp_grant_insert */ INSERT INTO harness.cp_grants (org_id, subject, entries, revision, region)
                 VALUES ($1, $2, $3::jsonb, 1, $4) ON CONFLICT (org_id, subject) DO NOTHING`,
                [tx.orgId, checked.subject, entries, rowRegion()],
              )
            : await k.query(
                `/* pipeshub:harness.cp_grant_update */ UPDATE harness.cp_grants SET entries = $4::jsonb, revision = $5
                  WHERE org_id = $1 AND subject = $2 AND revision = $3`,
                [
                  tx.orgId,
                  checked.subject,
                  expectedRevision,
                  entries,
                  revision,
                ],
              );
        if (moved.rowCount === 1) return;
        const { rows } = await k.query<{ revision: string }>(
          `/* pipeshub:harness.cp_grant_revision */ SELECT revision::text FROM harness.cp_grants WHERE org_id = $1 AND subject = $2`,
          [tx.orgId, checked.subject],
        );
        compareAndSet(Number(rows[0]?.revision ?? 0), expectedRevision, what);
      }),
    );
    return revision;
  }

  usersOf(orgId: string, installation: string): Promise<SubjectRef[]> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ subject: string }>(
        `/* pipeshub:harness.cp_grant_users_of */ SELECT subject FROM harness.cp_grants
          WHERE org_id = $1 AND entries @> jsonb_build_array(jsonb_build_object('installation', $2::text)) ORDER BY subject`,
        [orgId, installation],
      );
      return rows.map((row) => row.subject);
    });
  }
}

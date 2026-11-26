import { PayloadStoreUnavailableError } from '../../../../modules/agent-harness/domain/errors';
import {
  PayloadOwner,
  PayloadReferences,
  PayloadStore,
  TransactionalPayloadStore,
} from '../../../../modules/agent-harness/ports/payload-store';
import type { Tx } from '../../uow';
import { orgUnit, rowRegion, type PgKernelTx } from '../pg-documents/pg';
import { payloadRowId } from './names';

const REF_PREFIX = 'pg:';

/** The row id behind a payload name of this org (a carried `mongo:` name too, `names.ts`); null for another store's or org's. */
export const pgPayloadId = payloadRowId;

/**
 * Claim-check payloads on PostgreSQL (`harness.journal_payloads`), content-addressed per org as on MongoDB: a second
 * put of the same bytes keeps the first row (`ON CONFLICT DO NOTHING`).
 */
export class PgPayloadStore
  implements PayloadStore, PayloadReferences<Tx>, TransactionalPayloadStore<Tx>
{
  async put(
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner?: PayloadOwner,
  ): Promise<string> {
    try {
      return await orgUnit(undefined, orgId, { write: true }, (k) =>
        this.upsert(k, orgId, hash, bytes, owner),
      );
    } catch (error) {
      throw new PayloadStoreUnavailableError('put', error);
    }
  }

  /** Unwrapped: a serialization failure must reach the unit of work, which re-runs it. */
  putIn(
    tx: Tx,
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner?: PayloadOwner,
  ): Promise<string> {
    return orgUnit(tx, orgId, { write: true }, (k) =>
      this.upsert(k, orgId, hash, bytes, owner),
    );
  }

  private async upsert(
    k: PgKernelTx,
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner: PayloadOwner | undefined,
  ): Promise<string> {
    const id = `${orgId}:${hash}`;
    await k.query(
      `/* pipeshub:harness.journal_payloads_put */ INSERT INTO harness.journal_payloads
         (org_id, id, hash, size, bytes, created_at, owner_session_id, owner_run_id, referenced, region)
       VALUES ($1, $2, $3, $4, $5, clock_timestamp(), $6, $7, $8, $9)
       ON CONFLICT (org_id, id) DO NOTHING`,
      [
        orgId,
        id,
        hash,
        bytes.length,
        bytes,
        owner?.sessionId ?? null,
        owner?.runId ?? null,
        owner === undefined ? null : false,
        rowRegion(),
      ],
    );
    return `${REF_PREFIX}${id}`;
  }

  async get(orgId: string, ref: string): Promise<Buffer | null> {
    const id = pgPayloadId(orgId, ref);
    if (id === null) return null;
    try {
      return await orgUnit(undefined, orgId, { write: false }, async (k) => {
        const { rows } = await k.query<{ bytes: Buffer }>(
          `/* pipeshub:harness.journal_payloads_get */ SELECT bytes FROM harness.journal_payloads
            WHERE org_id = $1 AND id = $2`,
          [orgId, id],
        );
        return rows[0]?.bytes ?? null;
      });
    } catch (error) {
      throw new PayloadStoreUnavailableError('get', error);
    }
  }

  async markReferenced(
    orgId: string,
    refs: readonly string[],
    tx: Tx,
  ): Promise<void> {
    const ids = refs.flatMap((ref) => {
      const id = pgPayloadId(orgId, ref);
      return id === null ? [] : [id];
    });
    if (ids.length === 0) return;
    await orgUnit(tx, orgId, { write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.journal_payloads_referenced */ UPDATE harness.journal_payloads SET referenced = true
          WHERE org_id = $1 AND id = ANY($2::text[]) AND referenced = false`,
        [orgId, ids],
      ),
    );
  }
}

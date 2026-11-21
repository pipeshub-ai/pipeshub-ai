import { InternalServerError } from '../../../errors/http.errors';
import {
  holdsNothing,
  QuotaLedgerState,
  QuotaReservation,
  QuotaReservationRequest,
  releaseQuota,
  reservationOf,
  reserveQuota,
} from '../../../../modules/agent-harness/domain/admission-quotas';
import { outsideUnit } from '../../../db';
import {
  docParam,
  decodeDoc,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { inOrgRead } from '../execution/pg-rows';
import type { AdmissionQuotaLedger } from './hold';

export { AdmissionQuotaHold } from './hold';

/** The org a quota key names (`orgQuotaKey`, `userQuotaKey`: a JSON array with the org second). */
export function quotaKeyOrg(key: string): string {
  const parsed = JSON.parse(key) as unknown;
  const orgId: unknown = Array.isArray(parsed) ? parsed[1] : undefined;
  if (typeof orgId !== 'string')
    throw new InternalServerError('an admission quota key names no org');
  return orgId;
}

interface QuotaRow {
  window_start: number;
  count: number;
  holds: unknown;
}

async function lockState(
  k: PgKernelTx,
  orgId: string,
  key: string,
): Promise<QuotaLedgerState | null> {
  const { rows } = await k.query<QuotaRow>(
    `/* pipeshub:harness.admission_quotas_lock */ SELECT window_start, count, holds FROM harness.admission_quotas
      WHERE org_id = $1 AND key = $2 FOR UPDATE`,
    [orgId, key],
  );
  const row = rows[0];
  return row === undefined
    ? null
    : {
        windowStart: row.window_start,
        count: row.count,
        holds: decodeDoc<QuotaLedgerState['holds']>(row.holds),
      };
}

async function writeState(
  k: PgKernelTx,
  orgId: string,
  key: string,
  state: QuotaLedgerState,
): Promise<void> {
  await k.query(
    `/* pipeshub:harness.admission_quotas_write */ INSERT INTO harness.admission_quotas AS q
       (org_id, key, window_start, count, holds, region) VALUES ($1, $2, $3, $4, $5::jsonb, $6)
     ON CONFLICT (org_id, key) DO UPDATE SET window_start = excluded.window_start, count = excluded.count, holds = excluded.holds`,
    [
      orgId,
      key,
      state.windowStart,
      state.count,
      docParam(state.holds),
      rowRegion(),
    ],
  );
}

/**
 * An org's or an actor's quota row on PostgreSQL, written by a unit of its own and never in the admission's: the row
 * lock is held only for that short unit, so admissions of one org or one person queue a moment here instead of for
 * each other's whole admission (PERFB-F1, DOS-MONGO-1). The writes are `reserveQuota` and `releaseQuota` of the domain on the locked row. A first reservation of
 * an org races only on the insert, which `ON CONFLICT` turns into the update of the row the other one wrote.
 */
export class PgAdmissionQuotaLedger implements AdmissionQuotaLedger {
  reserve(
    key: string,
    request: QuotaReservationRequest,
  ): Promise<QuotaReservation> {
    const orgId = quotaKeyOrg(key);
    return outsideUnit(() =>
      inOrgRead(
        orgId,
        async (k) => {
          // Locks the row before the read (a first writer inserts it): two reservations never read one state.
          await k.query(
            `/* pipeshub:harness.admission_quotas_seed */ INSERT INTO harness.admission_quotas
               (org_id, key, window_start, count, holds, region) VALUES ($1, $2, $3, 0, '[]'::jsonb, $4)
             ON CONFLICT (org_id, key) DO NOTHING`,
            [orgId, key, request.now - request.windowMs, rowRegion()],
          );
          const state = reserveQuota(await lockState(k, orgId, key), request);
          await writeState(k, orgId, key, state);
          return reservationOf(state, request);
        },
        true,
      ),
    );
  }

  release(key: string, id: string, refund: boolean): Promise<void> {
    const orgId = quotaKeyOrg(key);
    return outsideUnit(() =>
      inOrgRead(
        orgId,
        async (k) => {
          const state = await lockState(k, orgId, key);
          if (state === null) return;
          const left = releaseQuota(state, id, refund);
          if (holdsNothing(left)) {
            await k.query(
              `/* pipeshub:harness.admission_quotas_drop */ DELETE FROM harness.admission_quotas WHERE org_id = $1 AND key = $2`,
              [orgId, key],
            );
            return;
          }
          await writeState(k, orgId, key, left);
        },
        true,
      ),
    );
  }
}

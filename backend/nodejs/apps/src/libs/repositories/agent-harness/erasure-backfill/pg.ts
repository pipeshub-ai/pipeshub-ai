import type { Tx } from '../../uow';
import { inUnit } from '../../pg-util';
import type {
  BackfillCursor,
  BackfillItem,
  BackfillItemState,
  BackfillMarker,
  BackfillPlan,
} from '../../../../modules/agent-harness/domain/erasure-backfill';
import type { ErasureBackfillStore } from '../../../../modules/agent-harness/ports/erasure-backfill';
import {
  decodeDoc,
  docParam,
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';

const REASON = 'harness erasure backfill';

/**
 * The operator's backfill run spans organisations (a plan with no org filter covers them all), so a call with no
 * transaction runs as the jobs role; one inside the caller's transaction joins it, as the org that batch is for.
 */
const unit = <T>(
  tx: Tx | undefined,
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> =>
  tx !== undefined ? inUnit(tx, opts, fn) : jobsUnit(REASON, opts, fn);

const toItem = (doc: BackfillItem): BackfillItem => ({
  digest: doc.digest,
  orgId: doc.orgId,
  sessionId: doc.sessionId,
  batch: doc.batch,
  fingerprint: doc.fingerprint,
  by: doc.by ?? null,
  updatedAt: doc.updatedAt,
  counts: { ...doc.counts },
  state: doc.state,
});

/**
 * Backfill plans (`harness.erasure_backfill_plans`, the deployment's: a plan may cover every org), their items and the
 * orgs' completion markers on PostgreSQL. The cursor moves by compare-and-set on its whole value, in the statement.
 */
export class PgErasureBackfillStore implements ErasureBackfillStore<Tx> {
  constructor(private readonly time: PgTime = pgServerTime) {}

  now(): Promise<number> {
    return jobsUnit(REASON, { write: false }, (k) => this.time.now(k));
  }

  plan(digest: string): Promise<BackfillPlan | null> {
    return unit(undefined, { write: false }, (k) => this.read(k, digest));
  }

  createPlan(plan: BackfillPlan): Promise<BackfillPlan> {
    return unit(undefined, { write: true }, async (k) => {
      await k.query(
        `/* pipeshub:harness.erasure_backfill_plans_create */ INSERT INTO harness.erasure_backfill_plans
           (digest, doc, region) VALUES ($1, $2::jsonb, $3) ON CONFLICT (digest) DO NOTHING`,
        [plan.digest, docParam(plan), rowRegion()],
      );
      return (await this.read(k, plan.digest)) as BackfillPlan;
    });
  }

  async putItems(items: readonly BackfillItem[]): Promise<void> {
    if (items.length === 0) return;
    await unit(undefined, { write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.erasure_backfill_items_put */ INSERT INTO harness.erasure_backfill_items
           (org_id, digest, session_id, doc, region)
         SELECT i.doc->>'orgId', i.doc->>'digest', i.doc->>'sessionId', i.doc, $2
           FROM jsonb_array_elements($1::jsonb) AS i(doc)
         ON CONFLICT (org_id, digest, session_id) DO NOTHING`,
        [docParam(items), rowRegion()],
      ),
    );
  }

  setPlanState(
    digest: string,
    from: BackfillPlan['state'],
    to: BackfillPlan['state'],
    detail: { staleReason?: string } = {},
  ): Promise<boolean> {
    return unit(undefined, { write: true }, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.erasure_backfill_plans_state */ UPDATE harness.erasure_backfill_plans
            SET doc = doc || $3::jsonb
          WHERE digest = $1 AND state = $2`,
        [
          digest,
          from,
          docParam({
            state: to,
            ...(detail.staleReason !== undefined
              ? { staleReason: detail.staleReason }
              : {}),
          }),
        ],
      );
      return rowCount === 1;
    });
  }

  items(
    digest: string,
    afterSessionId: string | null,
    limit: number,
  ): Promise<BackfillItem[]> {
    return unit(undefined, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.erasure_backfill_items_page */ SELECT doc FROM harness.erasure_backfill_items
          WHERE digest = $1 AND ($2::text IS NULL OR session_id > $2)
          ORDER BY session_id LIMIT $3`,
        [digest, afterSessionId, limit],
      );
      return rows.map((row) => toItem(decodeDoc<BackfillItem>(row.doc)));
    });
  }

  batchItems(
    digest: string,
    orgId: string,
    batch: number,
  ): Promise<BackfillItem[]> {
    return unit(undefined, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.erasure_backfill_items_batch */ SELECT doc FROM harness.erasure_backfill_items
          WHERE org_id = $1 AND digest = $2 AND batch = $3 ORDER BY session_id`,
        [orgId, digest, batch],
      );
      return rows.map((row) => toItem(decodeDoc<BackfillItem>(row.doc)));
    });
  }

  moveCursor(
    digest: string,
    from: BackfillCursor,
    to: BackfillCursor,
    tx?: Tx,
  ): Promise<boolean> {
    return unit(tx, { write: true }, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.erasure_backfill_plans_cursor */ UPDATE harness.erasure_backfill_plans
            SET doc = jsonb_set(doc, '{cursor}', $3::jsonb)
          WHERE digest = $1 AND doc->'cursor' = $2::jsonb`,
        [
          digest,
          docParam({ org: from.org, batch: from.batch, phase: from.phase }),
          docParam({ org: to.org, batch: to.batch, phase: to.phase }),
        ],
      );
      return rowCount === 1;
    });
  }

  async setItemStates(
    digest: string,
    states: ReadonlyArray<{ sessionId: string; state: BackfillItemState }>,
    tx?: Tx,
  ): Promise<void> {
    if (states.length === 0) return;
    // One state per session in one statement: the last named wins, as the ordered bulk write's last update does.
    const last = new Map(
      states.map(({ sessionId, state }) => [sessionId, state]),
    );
    await unit(tx, { write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.erasure_backfill_items_state */ UPDATE harness.erasure_backfill_items i
            SET doc = jsonb_set(i.doc, '{state}', to_jsonb(s.state))
           FROM unnest($2::text[], $3::text[]) AS s(session_id, state)
          WHERE i.digest = $1 AND i.session_id = s.session_id`,
        [digest, [...last.keys()], [...last.values()]],
      ),
    );
  }

  marker(orgId: string): Promise<BackfillMarker | null> {
    return unit(undefined, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.erasure_backfill_markers_get */ SELECT doc FROM harness.erasure_backfill_markers
          WHERE org_id = $1`,
        [orgId],
      );
      return rows[0] === undefined
        ? null
        : decodeDoc<BackfillMarker>(rows[0].doc);
    });
  }

  markers(): Promise<BackfillMarker[]> {
    return unit(undefined, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.erasure_backfill_markers_list */ SELECT doc FROM harness.erasure_backfill_markers
          ORDER BY org_id`,
      );
      return rows.map((row) => decodeDoc<BackfillMarker>(row.doc));
    });
  }

  putMarker(marker: BackfillMarker, tx?: Tx): Promise<boolean> {
    return unit(tx, { write: true }, async (k) => {
      const { rows } = await k.query(
        `/* pipeshub:harness.erasure_backfill_markers_put */ INSERT INTO harness.erasure_backfill_markers
           (org_id, doc, region) VALUES ($1, $2::jsonb, $3)
         ON CONFLICT (org_id) DO NOTHING RETURNING 1 AS created`,
        [marker.orgId, docParam(marker), rowRegion()],
      );
      return rows.length === 1;
    });
  }

  private async read(
    k: PgKernelTx,
    digest: string,
  ): Promise<BackfillPlan | null> {
    const { rows } = await k.query<{ doc: unknown }>(
      `/* pipeshub:harness.erasure_backfill_plans_get */ SELECT doc FROM harness.erasure_backfill_plans
        WHERE digest = $1`,
      [digest],
    );
    if (rows[0] === undefined) return null;
    const plan = decodeDoc<BackfillPlan>(rows[0].doc);
    return { ...plan, staleReason: plan.staleReason ?? null };
  }
}

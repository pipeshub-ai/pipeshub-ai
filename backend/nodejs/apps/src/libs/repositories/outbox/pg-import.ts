/**
 * Documents of the `outbox_events` collection as rows of `platform.outbox`: the state of what is still to be delivered
 * (migration design section 7). `published` rows are history and are not copied; `publishing` rows become `pending` (the claim
 * died with the stopped dispatcher; a row that did go out is sent again and consumers are idempotent); `failed` stays failed.
 *
 * Mongo events carry no organisation field: it is the `orgId` of the event in `value`, else the one in the ordering key. An event
 * that names none cannot be stored per organisation and is reported.
 *
 * The table id is a bigint, so the ObjectId is folded into one that keeps the creation order and can be recomputed from the
 * original: seconds since 2020-01-01 times 2^21 plus the low 21 bits of the ObjectId counter (below 2^53, exact in JSON). A
 * collision (same second, same counter, two processes) is refused by the key and shows in the verify counts. Rows the application
 * writes afterwards take the identity sequence, which starts far below these ids.
 */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';

const EPOCH_2020 = 1577836800;

export function outboxIdOf(hex: string): string {
  const seconds = Math.max(0, parseInt(hex.slice(0, 8), 16) - EPOCH_2020);
  const counter = parseInt(hex.slice(18, 24), 16) & 0x1fffff;
  return String(seconds * 2 ** 21 + counter);
}

const HEX24 = /^[0-9a-f]{24}$/;

/** Two events that fold to one table id: the copy stops, because `ON CONFLICT DO NOTHING` would drop one of them unseen. */
export class OutboxIdCollision extends Error {}

/** What makes an event the same event on a retry: fields the dispatcher never changes (status and attempts move on). */
const sameEvent = (a: Row, b: Row): boolean =>
  ['org_id', 'topic', 'msg_key', 'ordering_key', 'value'].every(
    (c) => a[c] === b[c],
  ) && Date.parse(String(a.created_at)) === Date.parse(String(b.created_at));

function orgOf(d: NeutralDoc): string | null {
  try {
    const org = (
      JSON.parse(String(d.value)) as { payload?: { orgId?: unknown } }
    ).payload?.orgId;
    const id = idText(org);
    if (id) return id;
  } catch {
    // not JSON: fall through to the ordering key
  }
  const m = /^(?:user:|org:)([0-9a-fA-F]{24})/.exec(
    typeof d.orderingKey === 'string' ? d.orderingKey : '',
  );
  return m && HEX24.test((m[1] as string).toLowerCase())
    ? (m[1] as string).toLowerCase()
    : null;
}

export async function importOutboxDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  const hexOf = new Map<string, string>();
  // creation order, so the ids keep it even where two ObjectIds share a second
  const ordered = [...docs].sort((a, b) =>
    String(a._id).localeCompare(String(b._id)),
  );
  for (const d of ordered) {
    const hex = idText(d._id);
    if (d.status === 'published') {
      stats.skipped.push({
        id: String(d._id),
        reason: 'published (history, not copied)',
      });
      continue;
    }
    const org = orgOf(d);
    if (
      !hex ||
      !org ||
      typeof d.topic !== 'string' ||
      typeof d.key !== 'string' ||
      typeof d.value !== 'string' ||
      typeof d.createdAt !== 'string'
    ) {
      stats.skipped.push({
        id: String(d._id),
        reason:
          'event without a usable organisation (or topic, key, value, createdAt)',
      });
      continue;
    }
    const status =
      d.status === 'publishing' || d.status === undefined
        ? 'pending'
        : d.status;
    const id = outboxIdOf(hex);
    const first = rows.find((x) => x.id === id);
    if (first) {
      const row = outboxRow(d, org, id, status);
      // the same event twice in one batch is written once; two different events must not share an id
      if (sameEvent(first, row)) continue;
      throw new OutboxIdCollision(
        `outbox events ${hexOf.get(id) ?? '?'} and ${hex} fold to the same table id ${id}`,
      );
    }
    hexOf.set(id, hex);
    rows.push(outboxRow(d, org, id, status));
  }
  await refuseHeldIds(k, rows, hexOf);
  await writePlan(k, [{ table: 'platform.outbox', rows }], stats);
  return stats;
}

/** An id already in the table must hold this very event (a resumed batch), not another one that folds to the same id. */
async function refuseHeldIds(
  k: KernelTx,
  rows: Row[],
  hexOf: Map<string, string>,
): Promise<void> {
  if (rows.length === 0) return;
  const { rows: held } = await k.query<Row>(
    '/* pipeshub:import.outbox_held */ SELECT id::text AS id, org_id, topic, msg_key, ordering_key, value, created_at FROM platform.outbox WHERE id = ANY($1::bigint[])',
    [rows.map((r) => r.id)],
  );
  for (const h of held) {
    const mine = rows.find((r) => r.id === h.id);
    if (!mine || !sameEvent(mine, h))
      throw new OutboxIdCollision(
        `outbox event ${hexOf.get(String(h.id)) ?? '?'} folds to table id ${String(h.id)}, which another event already holds (topic ${String(h.topic)}, created ${String(h.created_at)}); nothing was dropped, the copy stopped`,
      );
  }
}

function outboxRow(
  d: NeutralDoc,
  org: string,
  id: string,
  status: unknown,
): Row {
  return {
    org_id: org,
    id,
    topic: d.topic,
    msg_key: d.key,
    ordering_key: typeof d.orderingKey === 'string' ? d.orderingKey : 'global',
    value: d.value,
    headers: d.headers ?? null,
    status,
    attempts: typeof d.attempts === 'number' ? d.attempts : 0,
    next_attempt_at: d.nextAttemptAt ?? d.createdAt,
    claimed_at: null,
    last_error: typeof d.lastError === 'string' ? d.lastError : null,
    published_at: null,
    version: typeof d.__v === 'number' ? d.__v : 0,
    created_at: d.createdAt,
    updated_at: d.updatedAt ?? d.createdAt,
  };
}

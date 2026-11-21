import { randomUUID } from 'node:crypto';
import {
  AUDIT_GENESIS_HASH,
  AuditEntry,
  auditEntryDigest,
  AuditRecord,
  AuditUnlinked,
  chainAuditRecords,
  linkOrder,
} from '../../../../modules/agent-harness/domain/audit';
import {
  decodeDoc,
  docParam,
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { AUDIT_LINK_BATCH, AuditPendingMalformedError } from './mongo';

export { AUDIT_LINK_BATCH, AuditPendingMalformedError };

/** One append's entries as `harness.audit_pending` holds them, with what each entry said in the journal's unit. */
interface PendingDoc {
  entries: AuditEntry[];
  digests: string[];
}

interface PendingRow {
  id: string;
  stream_id: string;
  journal_seq: string;
  ts: string;
  doc: unknown;
}

/** Stores one append's entries in the journal's unit `k`; nothing the org's other commits write is touched. */
export async function insertPendingAudit(
  k: PgKernelTx,
  orgId: string,
  entries: readonly [AuditEntry, ...AuditEntry[]],
  ts: string,
): Promise<void> {
  const doc: PendingDoc = {
    entries: [...entries],
    digests: entries.map((entry) => auditEntryDigest(orgId, ts, entry)),
  };
  await k.query(
    `/* pipeshub:harness.audit_pending_insert */ INSERT INTO harness.audit_pending
       (org_id, id, stream_id, journal_seq, ts, doc, region) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)`,
    [
      orgId,
      randomUUID(),
      entries[0].streamId,
      entries[0].journalSeq,
      ts,
      docParam(doc),
      rowRegion(),
    ],
  );
}

const asCommitted = (orgId: string, ts: string, doc: PendingDoc): boolean =>
  Array.isArray(doc.entries) &&
  doc.entries.length > 0 &&
  Array.isArray(doc.digests) &&
  doc.digests.length === doc.entries.length &&
  doc.entries.every(
    (entry, index) => auditEntryDigest(orgId, ts, entry) === doc.digests[index],
  );

/**
 * Links up to one batch of the org's pending appends onto its chain and removes them, in `k`; returns how many it
 * linked. The head's row lock is taken before the batch is read, so two linkers of one org run one after the other and
 * the second finds only what the first left: the chain stays linear.
 */
export async function linkPendingAuditIn(
  k: PgKernelTx,
  orgId: string,
): Promise<number> {
  const waiting = await k.query(
    `/* pipeshub:harness.audit_pending_any */ SELECT 1 FROM harness.audit_pending WHERE org_id = $1 LIMIT 1`,
    [orgId],
  );
  if (waiting.rows.length === 0) return 0;
  await k.query(
    `/* pipeshub:harness.audit_heads_open */ INSERT INTO harness.audit_heads (org_id, seq, hash, anchored_seq, region)
     VALUES ($1, 0, $2, 0, $3) ON CONFLICT (org_id) DO NOTHING`,
    [orgId, AUDIT_GENESIS_HASH, rowRegion()],
  );
  const { rows: heads } = await k.query<{ seq: string; hash: string }>(
    `/* pipeshub:harness.audit_heads_lock */ SELECT seq::text, hash FROM harness.audit_heads WHERE org_id = $1 FOR UPDATE`,
    [orgId],
  );
  const locked = heads[0];
  if (locked === undefined)
    throw new Error('the audit chain head was not written');
  // Stream by stream in journal order: where the batch ends, no append is taken before one of its stream that is left.
  const { rows } = await k.query<PendingRow>(
    `/* pipeshub:harness.audit_pending_batch */ SELECT id, stream_id, journal_seq::text, ts, doc FROM harness.audit_pending
      WHERE org_id = $1 ORDER BY stream_id, journal_seq, id LIMIT $2`,
    [orgId, AUDIT_LINK_BATCH],
  );
  if (rows.length === 0) return 0;
  const appends = rows.map((row) => ({
    id: row.id,
    streamId: row.stream_id,
    journalSeq: Number(row.journal_seq),
    ts: row.ts,
    doc: decodeDoc<PendingDoc>(row.doc),
  }));
  const malformed = appends.find(
    (append) => !asCommitted(orgId, append.ts, append.doc),
  );
  if (malformed !== undefined)
    throw new AuditPendingMalformedError(orgId, malformed.id);
  let head: { seq: number; hash: string } = {
    seq: Number(locked.seq),
    hash: locked.hash,
  };
  const records: AuditRecord[] = [];
  for (const append of linkOrder(appends)) {
    for (const record of chainAuditRecords(
      orgId,
      head,
      append.doc.entries,
      append.ts,
    )) {
      records.push(record);
      head = { seq: record.seq, hash: record.hash };
    }
  }
  await k.query(
    `/* pipeshub:harness.audit_records_insert */ INSERT INTO harness.audit_records
       (org_id, seq, kind, session_id, run_id, ts, doc, region)
     SELECT $1, s, ki, se, ru, t, d, $8
       FROM unnest($2::bigint[], $3::text[], $4::text[], $5::text[], $6::text[], $7::jsonb[]) AS u(s, ki, se, ru, t, d)`,
    [
      orgId,
      records.map((r) => r.seq),
      records.map((r) => r.kind),
      records.map((r) => r.sessionId),
      records.map((r) => r.runId),
      records.map((r) => r.ts),
      records.map((r) => docParam(r)),
      rowRegion(),
    ],
  );
  await k.query(
    `/* pipeshub:harness.audit_heads_advance */ UPDATE harness.audit_heads SET seq = $2, hash = $3 WHERE org_id = $1`,
    [orgId, head.seq, head.hash],
  );
  await k.query(
    `/* pipeshub:harness.audit_pending_linked */ DELETE FROM harness.audit_pending WHERE org_id = $1 AND id = ANY($2::text[])`,
    [orgId, appends.map((append) => append.id)],
  );
  return appends.length;
}

/** Every pending append of the org, a batch per unit, as the jobs role (the link runs outside any request). */
export async function linkAllPendingAuditPg(orgId: string): Promise<void> {
  while (
    (await jobsUnit('audit-link', { write: true }, (k) =>
      linkPendingAuditIn(k, orgId),
    )) === AUDIT_LINK_BATCH
  );
}

/** The orgs with an append committed and not linked. */
export function pendingAuditOrgsPg(): Promise<string[]> {
  return jobsUnit('audit-link', { write: false }, async (k) => {
    const { rows } = await k.query<{ org_id: string }>(
      `/* pipeshub:harness.audit_pending_orgs */ SELECT DISTINCT org_id FROM harness.audit_pending`,
    );
    return rows.map((row) => row.org_id);
  });
}

/** What is committed and not linked, per org (one org when given). */
export function unlinkedAuditPg(orgId?: string): Promise<AuditUnlinked[]> {
  return jobsUnit('audit-link', { write: false }, async (k) => {
    const { rows } = await k.query<{
      org_id: string;
      appends: number;
      oldest_ts: string;
    }>(
      `/* pipeshub:harness.audit_pending_unlinked */ SELECT org_id, count(*)::int AS appends, min(ts) AS oldest_ts
         FROM harness.audit_pending WHERE ($1::text IS NULL OR org_id = $1) GROUP BY org_id ORDER BY org_id`,
      [orgId ?? null],
    );
    return rows.map((row) => ({
      orgId: row.org_id,
      appends: row.appends,
      oldestTs: row.oldest_ts,
    }));
  });
}

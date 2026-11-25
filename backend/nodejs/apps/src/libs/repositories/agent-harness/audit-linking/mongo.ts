import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import type { ClientSession, Db, ObjectId } from 'mongodb';
import {
  AUDIT_GENESIS_HASH,
  AuditEntry,
  AuditRecord,
  auditEntryDigest,
  AuditUnlinked,
  chainAuditRecords,
  linkOrder,
} from '../../../../modules/agent-harness/domain/audit';
import { DURABLE_TRANSACTION_OPTIONS } from '../durable-transaction/mongo';

export const AUDIT_RECORDS = 'agentAuditRecords';
export const AUDIT_HEADS = 'agentAuditHeads';
export const AUDIT_PENDING = 'agentAuditPending';
export const AUDIT_PENDING_ORDER_INDEX = 'org_link_order';

/** Appends linked in one transaction; the next one takes what is left. */
export const AUDIT_LINK_BATCH = 200;

/** One append's entries, committed with their journal events and not yet linked. */
export interface AuditPendingDocument {
  _id: ObjectId;
  orgId: string;
  streamId: string;
  journalSeq: number;
  ts: string;
  entries: AuditEntry[];
  /** `auditEntryDigest` of each entry, taken in the journal's transaction. */
  digests: string[];
}

interface HeadDocument {
  orgId: string;
  seq: number;
  hash: string;
  anchoredSeq: number;
}

/**
 * A pending append that is not what the store wrote: it holds no entries, or
 * an entry does not match the digest taken in its journal transaction.
 * Linking stops for the org rather than pass over it, so it cannot go unseen.
 */
export class AuditPendingMalformedError extends Error {
  constructor(
    readonly orgId: string,
    readonly pendingId: string,
  ) {
    super(
      `Pending audit append ${pendingId} of org ${orgId} is not as it was committed`,
    );
    this.name = 'AuditPendingMalformedError';
  }
}

/** The document an append commits: its entries and what each of them said. */
export const pendingAuditAppend = (
  orgId: string,
  entries: readonly [AuditEntry, ...AuditEntry[]],
  ts: string,
): Omit<AuditPendingDocument, '_id'> => ({
  orgId,
  streamId: entries[0].streamId,
  journalSeq: entries[0].journalSeq,
  ts,
  entries: [...entries],
  digests: entries.map((entry) => auditEntryDigest(orgId, ts, entry)),
});

const asCommitted = (append: AuditPendingDocument): boolean =>
  Array.isArray(append.entries) &&
  append.entries.length > 0 &&
  Array.isArray(append.digests) &&
  append.digests.length === append.entries.length &&
  append.entries.every(
    (entry, index) =>
      auditEntryDigest(append.orgId, append.ts, entry) ===
      append.digests[index],
  );

export interface AuditAppend {
  entries: readonly AuditEntry[];
  ts: string;
}

/**
 * Links `appends` onto the org's chain, in order. The head is claimed first,
 * so two links of one org conflict there and the chain stays linear; under a
 * session in a transaction the three writes commit together.
 */
export async function linkAuditAppends(
  db: Db,
  orgId: string,
  appends: readonly AuditAppend[],
  sessionHandle?: Tx | ClientSession,
): Promise<void> {
  const session = asMongoSession(sessionHandle);
  const count = appends.reduce((sum, append) => sum + append.entries.length, 0);
  if (count === 0) return;
  const heads = db.collection<HeadDocument>(AUDIT_HEADS);
  const options = session === undefined ? {} : { session };
  const before = await heads.findOneAndUpdate(
    { orgId },
    [
      {
        $set: {
          orgId: { $literal: orgId },
          seq: { $add: [{ $ifNull: ['$seq', 0] }, count] },
          hash: { $ifNull: ['$hash', AUDIT_GENESIS_HASH] },
          anchoredSeq: { $ifNull: ['$anchoredSeq', 0] },
        },
      },
    ],
    { upsert: true, returnDocument: 'before', ...options },
  );
  let head = before === null ? null : { seq: before.seq, hash: before.hash };
  const records: AuditRecord[] = [];
  for (const append of appends) {
    for (const record of chainAuditRecords(
      orgId,
      head,
      append.entries,
      append.ts,
    )) {
      records.push(record);
      head = { seq: record.seq, hash: record.hash };
    }
  }
  await db
    .collection<AuditRecord>(AUDIT_RECORDS)
    .insertMany(records, { ordered: true, ...options });
  await heads.updateOne({ orgId }, { $set: { hash: head?.hash } }, options);
}

/**
 * Links up to one batch of the org's pending appends and removes them, in one
 * transaction; returns how many appends it linked.
 */
export async function linkPendingAudit(db: Db, orgId: string): Promise<number> {
  const pending = db.collection<AuditPendingDocument>(AUDIT_PENDING);
  if ((await pending.findOne({ orgId }, { projection: { _id: 1 } })) === null) {
    return 0;
  }
  const session = db.client.startSession();
  try {
    let linked = 0;
    await session.withTransaction(async () => {
      // Stream by stream in journal order: where the batch ends, no append is taken before one of its stream that is left.
      const appends = await pending
        .find({ orgId }, { session })
        .sort({ streamId: 1, journalSeq: 1 })
        .limit(AUDIT_LINK_BATCH)
        .toArray();
      linked = appends.length;
      if (linked === 0) return;
      const malformed = appends.find((append) => !asCommitted(append));
      if (malformed !== undefined) {
        throw new AuditPendingMalformedError(orgId, String(malformed._id));
      }
      await linkAuditAppends(db, orgId, linkOrder(appends), session);
      await pending.deleteMany(
        { _id: { $in: appends.map((append) => append._id) } },
        { session },
      );
    }, DURABLE_TRANSACTION_OPTIONS);
    return linked;
  } finally {
    await session.endSession();
  }
}

/** Every pending append of the org, whatever their number. */
export async function linkAllPendingAudit(
  db: Db,
  orgId: string,
): Promise<void> {
  while ((await linkPendingAudit(db, orgId)) === AUDIT_LINK_BATCH);
}

export async function pendingAuditOrgs(db: Db): Promise<string[]> {
  return db.collection<AuditPendingDocument>(AUDIT_PENDING).distinct('orgId');
}

/** What is committed and not linked, per org (one org when given). */
export async function unlinkedAudit(
  db: Db,
  orgId?: string,
): Promise<AuditUnlinked[]> {
  const rows = await db
    .collection<AuditPendingDocument>(AUDIT_PENDING)
    .aggregate<{ _id: string; appends: number; oldestTs: string }>([
      ...(orgId === undefined ? [] : [{ $match: { orgId } }]),
      {
        $group: {
          _id: '$orgId',
          appends: { $sum: 1 },
          oldestTs: { $min: '$ts' },
        },
      },
      { $sort: { _id: 1 } },
    ])
    .toArray();
  return rows.map((row) => ({
    orgId: row._id,
    appends: row.appends,
    oldestTs: row.oldestTs,
  }));
}

/* eslint-disable @typescript-eslint/no-unsafe-member-access -- filter operators on a driver document */
/** Reading the source: raw documents in `_id` order, in batches bounded by count and bytes. */
import { BSON, Document, ObjectId } from 'mongodb';
import type { Db } from 'mongodb';

const HEX24 = /^[0-9a-fA-F]{24}$/;

/** `_id` filter value for a checkpoint: an ObjectId when the checkpoint is 24 hex digits, else the string. */
export const idFilter = (id: string): ObjectId | string =>
  HEX24.test(id) ? new ObjectId(id) : id;

/** Checkpoint text of an `_id`. Every migrated collection has ObjectId ids (the preflight refuses any other), whose text is already lowercase hex. */
export const idString = (id: unknown): string => String(id).toLowerCase();

export interface RawBatch {
  docs: Document[];
  /** `_id` of the last document (checkpoint). */
  last: string;
}

/**
 * Batches of at most `size` documents and about `maxBytes` bytes, after `after` (exclusive). The cursor is read with
 * `promoteLongs: false`, so a Long beyond 2^53 reaches `neutral` intact.
 */
export async function* readBatches(
  db: Db,
  collection: string,
  after: string | null,
  size: number,
  maxBytes: number,
  upTo?: string,
): AsyncGenerator<RawBatch> {
  const filter: Document = {};
  if (after || upTo) {
    filter._id = {};
    if (after) filter._id.$gt = idFilter(after);
    if (upTo) filter._id.$lte = idFilter(upTo);
  }
  const cursor = db.collection(collection).find(filter, {
    sort: { _id: 1 },
    promoteLongs: false,
    batchSize: Math.min(size, 1000),
  });
  let docs: Document[] = [];
  let bytes = 0;
  try {
    for await (const d of cursor) {
      docs.push(d);
      bytes += BSON.calculateObjectSize(d);
      if (docs.length >= size || bytes >= maxBytes) {
        yield { docs, last: idString(docs[docs.length - 1]?._id) };
        docs = [];
        bytes = 0;
      }
    }
    if (docs.length) yield { docs, last: idString(docs[docs.length - 1]?._id) };
  } finally {
    await cursor.close().catch(() => undefined);
  }
}

/** Collection names present in the database (system collections left out), with document counts. */
export async function collectionCounts(db: Db): Promise<Map<string, number>> {
  const out = new Map<string, number>();
  for (const c of await db.listCollections({}, { nameOnly: true }).toArray()) {
    if (c.name.startsWith('system.')) continue;
    out.set(c.name, await db.collection(c.name).estimatedDocumentCount());
  }
  return out;
}

/**
 * Plain values for the carriers: the harness stores keep a document in `jsonb` as `encodeDoc` writes it (a Date as
 * `{"$date": ms}`, bytes as `{"$bin": ...}`), so a carried document must hold Dates, Buffers, numbers and strings only. A BSON
 * value the stores never write (an ObjectId inside a document, a Decimal128) cannot be given back as it was, so it is refused
 * with its path: the move stops before anything is lost.
 */
import { encodeDoc } from '../../../libs/repositories/agent-harness/pg-documents/pg';
import type { Doc, SourceDoc, SourceRead } from './types';

const bsonType = (v: unknown): string | undefined =>
  v !== null && typeof v === 'object'
    ? (v as { _bsontype?: string })._bsontype
    : undefined;

export class UncarriableValue extends Error {
  override name = 'UncarriableValue';
}

/** A value as the harness stores hold it; throws on a BSON type that would not come back as it was. */
export function plainValue(v: unknown, path: string): unknown {
  if (v === null || v === undefined) return v;
  if (v instanceof Date || Buffer.isBuffer(v)) return v;
  const t = bsonType(v);
  if (t === 'Int32' || t === 'Double') return (v as { value: number }).value;
  if (t === 'Long') {
    const n = Number(String(v));
    if (Number.isSafeInteger(n)) return n;
    throw new UncarriableValue(
      `${path}: a 64-bit integer beyond 2^53 (${String(v)})`,
    );
  }
  if (t === 'Binary') {
    const b = v as { buffer: Uint8Array; position: number };
    return Buffer.from(b.buffer.subarray(0, b.position));
  }
  if (t !== undefined)
    throw new UncarriableValue(
      `${path}: a BSON ${t}, which the PostgreSQL store does not keep`,
    );
  if (Array.isArray(v))
    return v.map((x, i) => plainValue(x, `${path}[${String(i)}]`));
  if (typeof v === 'object') {
    const out: Doc = {};
    for (const [k, x] of Object.entries(v as Doc))
      if (x !== undefined) out[k] = plainValue(x, path ? `${path}.${k}` : k);
    return out;
  }
  return v;
}

/**
 * A raw driver document as a carrier sees it. The `_id` may be an ObjectId (kept as hex) or a string. `pick`: only these top-level
 * paths are read (a carrier of a few fields of a mapped collection never sees the rest); `idPaths`: paths that may hold an ObjectId,
 * read as its lower-case hex (the organisation id of a user or a chat).
 */
export function sourceDoc(
  raw: Doc,
  collection: string,
  opts: SourceRead = {},
): SourceDoc {
  const { _id, ...all } = raw;
  const oid = bsonType(_id) === 'ObjectId' || bsonType(_id) === 'ObjectID';
  if (!oid && typeof _id !== 'string')
    throw new UncarriableValue(
      `${collection}: a document whose _id is a ${bsonType(_id) ?? typeof _id}, not an ObjectId or a string`,
    );
  const id = oid ? String(_id).toLowerCase() : (_id as string);
  const rest: Doc = opts.pick
    ? Object.fromEntries(
        opts.pick.filter((p) => all[p] !== undefined).map((p) => [p, all[p]]),
      )
    : all;
  for (const p of opts.idPaths ?? []) {
    const t = bsonType(rest[p]);
    if (t === 'ObjectId' || t === 'ObjectID')
      rest[p] = String(rest[p]).toLowerCase();
  }
  try {
    return { id, oid, doc: plainValue(rest, '') as Doc };
  } catch (e) {
    if (!(e instanceof UncarriableValue)) throw e;
    return { id, oid, doc: {}, refused: `${collection} ${id}: ${e.message}` };
  }
}

const sorted = (v: unknown): unknown => {
  if (Array.isArray(v)) return v.map(sorted);
  if (v !== null && typeof v === 'object')
    return Object.fromEntries(
      Object.keys(v as Doc)
        .sort()
        .map((k) => [k, sorted((v as Doc)[k])]),
    );
  return v;
};

/** One text per value, key order ignored: what the verify compares. */
export const canonicalText = (v: unknown): string =>
  JSON.stringify(sorted(encodeDoc(v)) ?? null);

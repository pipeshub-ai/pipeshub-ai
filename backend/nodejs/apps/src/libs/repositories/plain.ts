/**
 * Plain, driver-free copies of stored rows: what crosses the repository seam.
 * Every ObjectId becomes its hex string; nothing else about the value changes
 * (dates stay dates, a `Map` that `toObject()` left in place stays a `Map`), so a
 * response built from the copy serialises exactly as one built from the
 * document or lean row did.
 */
type Json = Record<string, unknown>;

const isObjectId = (v: unknown): boolean =>
  v !== null &&
  typeof v === 'object' &&
  (v as { _bsontype?: unknown })._bsontype === 'ObjectId';

const isPlainObject = (v: unknown): v is Json => {
  if (v === null || typeof v !== 'object') return false;
  const proto = Object.getPrototypeOf(v) as unknown;
  return proto === Object.prototype || proto === null;
};

function walk(v: unknown): unknown {
  if (isObjectId(v)) return String(v);
  if (Array.isArray(v)) return v.map(walk);
  if (isPlainObject(v)) {
    const out: Json = {};
    for (const [k, val] of Object.entries(v)) out[k] = walk(val);
    return out;
  }
  return v;
}

/**
 * `doc` is a lean row, or a Mongoose document: a document is read with
 * `toObject()` (Maps stay Maps) or, with `json`, with `toJSON()` (Maps become
 * objects), whichever the response it replaces used.
 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- callers name the row type they expect
export function toPlain<T = unknown>(
  doc: unknown,
  opts: { json?: boolean } = {},
): T {
  const src = doc as
    { toObject?: () => unknown; toJSON?: () => unknown } | null | undefined;
  if (src && typeof src.toObject === 'function') {
    return walk(
      opts.json && typeof src.toJSON === 'function'
        ? src.toJSON()
        : src.toObject(),
    ) as T;
  }
  return walk(doc) as T;
}

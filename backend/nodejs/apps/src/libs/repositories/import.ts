/**
 * What the document-store migration needs from a repository: `importBatch` takes documents as MongoDB stored them (in the
 * store-neutral form `neutral` produces) and writes them, with their own ids, versions and timestamps, as rows of the
 * PostgreSQL tables the repository owns. The mapping from a document to rows lives next to the repository (`pg-import.ts`),
 * so the copy writes what the API's own reads expect.
 *
 * This file imports no database runtime: it only builds the statement a unit of work runs.
 */
import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash } from 'node:crypto';
import type { Tx as PgKernelTx } from '../db';
import { fill } from './pg-util';
import type { Tx } from './uow';

/** A document in store-neutral form: ids as 24 hex digits, dates as ISO strings, binary data as `Buffer`, no `undefined`. */
export type NeutralDoc = Record<string, unknown>;

/** One row, column name to JSON value (timestamps as ISO strings, `bytea` as `\x…` hex, arrays as arrays). */
export type Row = Record<string, unknown>;

/** The rows a document becomes, per table, parents first. */
export type ImportPlan = Array<{ table: string; rows: Row[] }>;

export interface ImportStats {
  /** Documents given. */
  read: number;
  /** Rows written per table (rows that were already there are not counted). */
  inserted: Record<string, number>;
  /** Documents the repository does not copy, with the reason (counted in the verify report as a listed transform). */
  skipped: Array<{ id: string; reason: string }>;
  /** Work for after the copy that needs another role or another unit (e.g. claiming a slug). */
  followUps: Array<{ kind: string; [k: string]: unknown }>;
}

/**
 * What every PostgreSQL repository with a Mongo twin offers the migrator. A repository may own several collections, so the
 * call names the one the documents come from. Documents are already in `neutral` form with U+0000 stripped (the copy counts
 * the strips); `tx` is a unit of work of the jobs role.
 */
export interface Importable {
  importBatch(
    collection: string,
    docs: NeutralDoc[],
    tx: Tx,
  ): Promise<ImportStats>;
}

export const emptyStats = (read: number): ImportStats => ({
  read,
  inserted: {},
  skipped: [],
  followUps: [],
});

/**
 * Tables whose rows a write replaces in place (the follower): `insertRows` then updates a row that is already there instead of leaving
 * it alone. Deleting the row and inserting it again would also delete every row that references it with ON DELETE CASCADE (the
 * messages of a chat when the chat's own document changes), and those are other documents.
 */
const upsertTables = new AsyncLocalStorage<ReadonlySet<string>>();
export const withUpsert = <T>(
  tables: string[],
  fn: () => Promise<T>,
): Promise<T> => upsertTables.run(new Set(tables), fn);

/** Whether a write replaces rows of `table` in place (inside `withUpsert`). */
export const isUpsert = (table: string): boolean =>
  upsertTables.getStore()?.has(table) ?? false;

const keyCache = new Map<string, string[]>();
async function primaryKey(k: PgKernelTx, table: string): Promise<string[]> {
  const hit = keyCache.get(table);
  if (hit) return hit;
  const { rows } = await k.query<{ c: string }>(
    '/* pipeshub:import.primary_key */ SELECT a.attname AS c FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey) WHERE i.indrelid = $1::regclass AND i.indisprimary ORDER BY array_position(i.indkey::int2[], a.attnum)',
    [table],
  );
  const cols = rows.map((r) => r.c);
  keyCache.set(table, cols);
  return cols;
}
const columnCache = new Map<string, string[]>();
async function allColumns(k: PgKernelTx, table: string): Promise<string[]> {
  const hit = columnCache.get(table);
  if (hit) return hit;
  const { rows } = await k.query<{ c: string }>(
    "/* pipeshub:import.columns */ SELECT a.attname AS c FROM pg_attribute a WHERE a.attrelid = $1::regclass AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '' AND a.attidentity <> 'a' ORDER BY a.attnum",
    [table],
  );
  const cols = rows.map((r) => r.c);
  columnCache.set(table, cols);
  return cols;
}

const TABLE_RE = /^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$/;
const COLUMN_RE = /^[a-z_][a-z0-9_]*$/;

/**
 * One multi-row `INSERT … SELECT … FROM jsonb_populate_recordset(…) ON CONFLICT DO NOTHING` per set of columns: the column types
 * come from the table, so timestamps, arrays, `bytea` and domains are checked by the database. `DO NOTHING` because a row
 * already there is either an earlier attempt of this copy or a newer write by the change follower, and neither may be
 * overwritten. A column a row leaves out takes its default, a column it names takes the value (null included), so rows that
 * name different columns go in separate statements. Returns the rows written.
 */
export async function insertRows(
  k: PgKernelTx,
  table: string,
  rows: Row[],
): Promise<number> {
  if (rows.length === 0) return 0;
  if (!TABLE_RE.test(table)) throw new Error(`bad table name '${table}'`);
  const groups = new Map<string, Row[]>();
  for (const r of rows) {
    const sig = Object.keys(r).sort().join(',');
    const g = groups.get(sig);
    if (g) g.push(r);
    else groups.set(sig, [r]);
  }
  let written = 0;
  for (const [sig, group] of groups) {
    const cols = sig.split(',');
    for (const c of cols)
      if (!COLUMN_RE.test(c)) throw new Error(`bad column name '${c}'`);
    const list = cols.join(', ');
    let conflict = 'ON CONFLICT DO NOTHING';
    let batch = group;
    let upsert = false;
    if (isUpsert(table)) {
      // replace in place: the key stays, every other column takes the new value (a column the row leaves out goes back to its default)
      const key = await primaryKey(k, table);
      const set = (await allColumns(k, table))
        .filter((c) => !key.includes(c))
        .map((c) => `${c} = ${cols.includes(c) ? `excluded.${c}` : 'DEFAULT'}`);
      if (key.length > 0 && set.length > 0) {
        conflict = `ON CONFLICT (${key.join(', ')}) DO UPDATE SET ${set.join(', ')}`;
        upsert = true;
        // one statement may not update a row twice (cardinality violation): the last row of a key wins, as separate upserts would leave it
        if (key.every((c) => cols.includes(c)))
          batch = [
            ...new Map(
              group.map((r) => [JSON.stringify(key.map((c) => r[c])), r]),
            ).values(),
          ];
      }
    }
    // xmax is 0 on a row this statement inserted and set on one it updated; rowCount counts both
    const { rows: out, rowCount } = await k.query<{ ins: boolean }>(
      fill(
        '/* pipeshub:import.rows */ INSERT INTO @{T} (@{C}) SELECT @{C} FROM jsonb_populate_recordset(NULL::@{T}, $1::jsonb) @{X}@{R}',
        {
          T: table,
          C: list,
          X: conflict,
          R: upsert ? ' RETURNING (xmax = 0) AS ins' : '',
        },
      ),
      [JSON.stringify(batch)],
    );
    written += upsert ? out.filter((r) => r.ins).length : (rowCount ?? 0);
  }
  return written;
}

/** Runs a plan table by table and adds the counts to `stats`. */
export async function writePlan(
  k: PgKernelTx,
  plan: ImportPlan,
  stats: ImportStats,
): Promise<void> {
  for (const { table, rows } of plan)
    stats.inserted[table] =
      (stats.inserted[table] ?? 0) + (await insertRows(k, table, rows));
}

/** A stored knowledge limit as the value of a jsonb column: absent (no limit) stays NULL, never an empty limit (which would admit nothing). */
export function limitJson(v: unknown): Record<string, string[]> | null {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const ids = (x: unknown): string[] =>
    Array.isArray(x) ? x.filter((i): i is string => typeof i === 'string') : [];
  return {
    apps: ids(o.apps),
    recordGroups: ids(o.recordGroups),
    records: ids(o.records),
  };
}

const HEX24 = /^[0-9a-fA-F]{24}$/;

/** BSON values print themselves (ObjectId, Long, Decimal128, …); the eslint rule cannot see that on `unknown`. */
const text = (v: unknown): string => (v as { toString(): string }).toString();

const isBson = (v: unknown, t: string): boolean =>
  v !== null &&
  typeof v === 'object' &&
  (v as { _bsontype?: unknown })._bsontype === t;

/**
 * A raw driver value in store-neutral form: ObjectId to lower-case hex, Date to an ISO string (milliseconds), Long to a number
 * when it is exact and to a decimal string when it is not, Decimal128 to a string, `undefined` and functions dropped
 * (a field that was never stored), binary to a Buffer. Everything else is copied as it is.
 */
export function neutral(v: unknown): unknown {
  if (v === undefined || typeof v === 'function') return undefined;
  if (v === null) return null;
  if (isBson(v, 'ObjectId') || isBson(v, 'ObjectID'))
    return text(v).toLowerCase();
  if (v instanceof Date)
    return Number.isNaN(v.getTime()) ? null : v.toISOString();
  if (isBson(v, 'Long')) {
    const s = text(v);
    const n = Number(s);
    return Number.isSafeInteger(n) ? n : s;
  }
  if (isBson(v, 'Decimal128')) return text(v);
  if (isBson(v, 'Int32') || isBson(v, 'Double'))
    return (v as { value: number }).value;
  if (isBson(v, 'Binary')) {
    // `buffer` may be larger than the content (`position` bytes) when the Binary was built in memory
    const b = v as { buffer: Uint8Array; position: number };
    return Buffer.from(b.buffer.subarray(0, b.position));
  }
  if (isBson(v, 'BSONRegExp') || isBson(v, 'Timestamp')) return text(v);
  if (isBson(v, 'UUID')) return text(v);
  if (Buffer.isBuffer(v)) return v;
  if (typeof v === 'bigint')
    return Number.isSafeInteger(Number(v)) ? Number(v) : text(v);
  if (Array.isArray(v)) return v.map((x) => neutral(x) ?? null);
  if (v instanceof Map) {
    const out: Record<string, unknown> = {};
    for (const [k, x] of v) {
      const y = neutral(x);
      if (y !== undefined) out[String(k)] = y;
    }
    return out;
  }
  if (typeof v === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
      const y = neutral(x);
      if (y !== undefined) out[k] = y;
    }
    return out;
  }
  return v;
}

/** The stored form of an id value that may be an ObjectId, its hex string, or missing. */
export const idText = (v: unknown): string | null => {
  if (v === undefined || v === null) return null;
  const s = text(v);
  return HEX24.test(s) ? s.toLowerCase() : null;
};

/** `bytea` in JSON form for `jsonb_populate_recordset`. */
export const byteaJson = (
  b: Buffer | string | null | undefined,
): string | null =>
  b === null || b === undefined
    ? null
    : `\\x${Buffer.isBuffer(b) ? b.toString('hex') : Buffer.from(b, 'base64').toString('hex')}`;

/** SHA-256 hex, as the stores keep secrets that are only ever compared (authorization codes). */
export const sha256Hex = (s: string): string =>
  createHash('sha256').update(s).digest('hex');

/** Epoch milliseconds (number or numeric string) to an ISO string; null stays null. */
export const msToIso = (v: unknown): string | null => {
  if (v === undefined || v === null) return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? new Date(n).toISOString() : null;
};

/* eslint-disable @typescript-eslint/no-base-to-string -- BSON values print themselves */
/**
 * The verifier's PostgreSQL-side decoder: rows to the document the mapping describes. It shares no code with the
 * repositories' mappers.
 */
import { createHash } from 'node:crypto';
import { Doc, setPath } from './canonical';
import type { ChildMap, CollectionMapping, FieldMap } from './mapping/types';

type Row = Record<string, unknown>;

const iso = (v: unknown): string => new Date(String(v)).toISOString();

/** A column value in the document's form; undefined: nothing to put. */
export function decodeValue(f: FieldMap, v: unknown): unknown {
  if (v === null || v === undefined) return null;
  switch (f.type) {
    case 'oid':
      return String(v).toLowerCase();
    case 'date':
      return iso(v);
    case 'epoch_ms':
      return new Date(String(v)).getTime();
    case 'bytea_b64': {
      const s = String(v);
      return Buffer.from(
        s.startsWith('\\x') ? s.slice(2) : s,
        s.startsWith('\\x') ? 'hex' : 'base64',
      ).toString('base64');
    }
    case 'bigint': {
      const n = Number(v);
      return Number.isSafeInteger(n) ? n : String(v);
    }
    case 'oid_array':
      return (v as unknown[]).map((x) => String(x).toLowerCase());
    case 'text':
    case 'int':
    case 'float':
    case 'bool':
    case 'json':
    case 'text_array':
    case 'sha256':
      return v;
  }
}

function putField(doc: Doc, f: FieldMap, row: Row, nulls: Set<string>): void {
  const raw = row[f.col];
  if (raw === null || raw === undefined) {
    const mode = f.presence ?? 'omit';
    if (
      mode === 'null' ||
      (mode === 'listed' && (nulls.has(f.path) || nulls.has(f.col)))
    )
      setPath(doc, f.path, null);
    return;
  }
  const v = decodeValue(f, raw);
  if (
    f.absentWhen !== undefined &&
    JSON.stringify(v) === JSON.stringify(f.absentWhen)
  )
    return;
  setPath(doc, f.path, v);
}

function decodeChild(c: ChildMap, rows: Row[]): unknown[] {
  const sorted = [...rows].sort(
    (a, b) => Number(a[c.orderCol]) - Number(b[c.orderCol]),
  );
  return sorted.map((r) => {
    const scalar = c.fields.find((f) => f.path === '');
    if (scalar) return decodeValue(scalar, r[scalar.col]);
    const el: Doc = {};
    for (const f of c.fields) putField(el, f, r, new Set());
    return el;
  });
}

/** `children[table]` holds the rows of that child table whose parent is this root row. */
export function decodeDoc(
  m: CollectionMapping,
  root: Row,
  children: Record<string, Row[]>,
): Doc {
  const doc: Doc = { _id: String(root[m.idCol]).toLowerCase() };
  const nulls = new Set<string>(
    m.nullsCol ? ((root[m.nullsCol] as string[] | null) ?? []) : [],
  );
  for (const f of m.fields) putField(doc, f, root, nulls);
  if (
    m.versionCol &&
    root[m.versionCol] !== null &&
    root[m.versionCol] !== undefined
  )
    doc.__v = Number(root[m.versionCol]);
  for (const c of m.children ?? []) {
    const rows = children[c.table] ?? [];
    // a child table cannot hold an empty array: no rows means [] unless the mapping says the array is left out when empty
    if (rows.length > 0 || c.emptyIsArray !== false)
      setPath(doc, c.path, decodeChild(c, rows));
  }
  return doc;
}

export const sha256 = (s: string): string =>
  createHash('sha256').update(s).digest('hex');

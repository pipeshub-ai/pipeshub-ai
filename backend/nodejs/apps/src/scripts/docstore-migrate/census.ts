/**
 * The path census (migration design section 2 and 5.2.1): every BSON field path found in the data, with its types and its count
 * of `null`s, compared with the paths the mapping knows. A path the mapping does not know is red, whatever the mapper does.
 */
import type { Doc } from './canonical';
import type { CollectionMapping } from './mapping/types';

export interface PathStat {
  count: number;
  nulls: number;
  types: Record<string, number>;
}

const bsonType = (v: unknown): string => {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (v instanceof Date) return 'date';
  if (Buffer.isBuffer(v)) return 'binary';
  if (typeof v === 'object') {
    const t = (v as { _bsontype?: string })._bsontype;
    if (t) return t === 'ObjectID' ? 'ObjectId' : t;
    return 'object';
  }
  return typeof v;
};

export class Census {
  paths = new Map<string, PathStat>();
  docs = 0;
  /** Paths (`path` or `<key>`) that hold U+0000, with the number of documents. */
  nul = new Map<string, number>();
  withoutVersion = 0;

  add(doc: Doc): void {
    this.docs += 1;
    if (doc.__v === undefined) this.withoutVersion += 1;
    const nulSeen = new Set<string>();
    const walk = (v: unknown, path: string): void => {
      if (path) {
        const st = this.paths.get(path) ?? { count: 0, nulls: 0, types: {} };
        const t = bsonType(v);
        st.count += 1;
        if (v === null) st.nulls += 1;
        st.types[t] = (st.types[t] ?? 0) + 1;
        this.paths.set(path, st);
      }
      if (typeof v === 'string' && v.includes('\u0000'))
        nulSeen.add(path || '$');
      if (Array.isArray(v)) {
        for (const x of v) walk(x, `${path}[]`);
      } else if (
        v !== null &&
        typeof v === 'object' &&
        bsonType(v) === 'object'
      ) {
        for (const [k, x] of Object.entries(v as Doc)) {
          if (k.includes('\u0000')) nulSeen.add(`${path}.<key>`);
          walk(x, path ? `${path}.${k}` : k);
        }
      }
    };
    walk(doc, '');
    for (const p of nulSeen) this.nul.set(p, (this.nul.get(p) ?? 0) + 1);
  }
}

export interface Known {
  exact: Set<string>;
  prefixes: string[];
}

/** The paths a mapping accounts for. A `json` column covers everything beneath its path. */
export function knownPaths(m: CollectionMapping): Known {
  const exact = new Set<string>(['_id', '__v']);
  const prefixes: string[] = [];
  const addField = (base: string, f: { path: string; type: string }): void => {
    const p = f.path === '' ? base : base ? `${base}.${f.path}` : f.path;
    exact.add(p);
    if (f.type === 'json') prefixes.push(p);
    if (f.type === 'text_array' || f.type === 'oid_array') exact.add(`${p}[]`);
  };
  for (const f of m.fields) addField('', f);
  for (const c of m.children ?? []) {
    exact.add(c.path);
    exact.add(`${c.path}[]`);
    for (const f of c.fields) addField(`${c.path}[]`, f);
  }
  for (const d of m.dropped ?? []) prefixes.push(d.path);
  for (const p of m.censusKnown ?? []) exact.add(p);
  return { exact, prefixes };
}

export const isKnown = (k: Known, path: string): boolean =>
  k.exact.has(path) ||
  k.prefixes.some(
    (p) => path === p || path.startsWith(`${p}.`) || path.startsWith(`${p}[]`),
  );

export interface CensusFinding {
  kind: 'unmapped_path' | 'null_in_omit_field' | 'nul';
  collection: string;
  path: string;
  count: number;
  detail?: string;
}

/** Paths the mapping does not know, and `null`s in fields the mapping reads as "absent". */
export function judgeCensus(m: CollectionMapping, c: Census): CensusFinding[] {
  const known = knownPaths(m);
  const out: CensusFinding[] = [];
  for (const [path, st] of c.paths) {
    if (!isKnown(known, path))
      out.push({
        kind: 'unmapped_path',
        collection: m.collection,
        path,
        count: st.count,
        detail: Object.keys(st.types).join(','),
      });
  }
  const omit = new Map<string, string>();
  for (const f of m.fields)
    if ((f.presence ?? 'omit') === 'omit' && f.type !== 'json')
      omit.set(f.path, f.col);
  for (const ch of m.children ?? [])
    for (const f of ch.fields)
      if ((f.presence ?? 'omit') === 'omit' && f.type !== 'json' && f.path)
        omit.set(`${ch.path}[].${f.path}`, f.col);
  for (const [path, col] of omit) {
    const st = c.paths.get(path);
    if (st && st.nulls > 0 && !(m.nullOk ?? []).includes(path))
      out.push({
        kind: 'null_in_omit_field',
        collection: m.collection,
        path,
        count: st.nulls,
        detail: `column ${col} reads NULL as absent; reclassify the field (presence null/listed) and fix the mapper`,
      });
  }
  for (const [path, n] of c.nul)
    out.push({ kind: 'nul', collection: m.collection, path, count: n });
  return out;
}

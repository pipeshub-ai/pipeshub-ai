import { createHash } from 'node:crypto';

export type Json =
  null | boolean | number | string | Json[] | { [k: string]: Json };
export type Doc = Record<string, unknown>;

export function getPath(doc: unknown, path: string): unknown {
  let cur: unknown = doc;
  for (const p of path.split('.')) {
    if (
      cur === null ||
      typeof cur !== 'object' ||
      !Object.prototype.hasOwnProperty.call(cur, p)
    )
      return undefined;
    cur = (cur as Doc)[p];
  }
  return cur;
}

export const hasPath = (doc: unknown, path: string): boolean =>
  getPath(doc, path) !== undefined;

export function setPath(doc: Doc, path: string, value: unknown): void {
  const parts = path.split('.');
  let cur = doc;
  for (const p of parts.slice(0, -1)) {
    const next = cur[p];
    if (next === null || typeof next !== 'object' || Array.isArray(next))
      cur[p] = {};
    cur = cur[p] as Doc;
  }
  cur[parts[parts.length - 1] as string] = value;
}

/** Removes the value at a path; empty parents left behind are kept (the document had them). */
export function deletePath(doc: Doc, path: string): boolean {
  const parts = path.split('.');
  const last = parts[parts.length - 1] as string;
  let cur: unknown = doc;
  for (const p of parts.slice(0, -1)) {
    if (cur === null || typeof cur !== 'object') return false;
    cur = (cur as Doc)[p];
  }
  if (
    cur === null ||
    typeof cur !== 'object' ||
    !Object.prototype.hasOwnProperty.call(cur, last)
  )
    return false;
  // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
  delete (cur as Doc)[last];
  return true;
}

/** Buffers as base64, Dates as ISO, so a value is plain JSON. */
export function plainJson(v: unknown): unknown {
  if (Buffer.isBuffer(v)) return v.toString('base64');
  if (v instanceof Date) return v.toISOString();
  if (Array.isArray(v)) return v.map(plainJson);
  if (v !== null && typeof v === 'object') {
    // fromEntries, not `out[k] =`: an own `__proto__` key would set the prototype and vanish
    return Object.fromEntries(
      Object.entries(v as Doc)
        .filter(([, x]) => x !== undefined)
        .map(([k, x]) => [k, plainJson(x)]),
    );
  }
  return v;
}

/** Stable JSON text: keys sorted at every level. */
export function canonicalJson(v: unknown): string {
  const walk = (x: unknown): unknown => {
    if (Array.isArray(x)) return x.map(walk);
    if (x !== null && typeof x === 'object') {
      const o = x as Doc;
      return Object.fromEntries(
        Object.keys(o)
          .sort()
          .map((k) => [k, walk(o[k])]),
      );
    }
    return x;
  };
  return JSON.stringify(walk(v));
}

export const docHash = (v: unknown): string =>
  createHash('sha256').update(canonicalJson(v)).digest('hex');

/** Counts every U+0000 and strips it from strings and keys; returns the counts by path (array indexes shown as `[]`). */
export function stripNulCounted<T>(value: T, counts: Map<string, number>): T {
  const walk = (v: unknown, path: string): unknown => {
    if (typeof v === 'string') {
      if (!v.includes('\u0000')) return v;
      counts.set(path, (counts.get(path) ?? 0) + v.split('\u0000').length - 1);
      return v.split('\u0000').join('');
    }
    if (Array.isArray(v)) return v.map((x) => walk(x, `${path}[]`));
    if (
      v !== null &&
      typeof v === 'object' &&
      !Buffer.isBuffer(v) &&
      !(v instanceof Date)
    ) {
      return Object.fromEntries(
        Object.entries(v as Doc).map(([k, x]) => {
          let k2 = k;
          if (k.includes('\u0000')) {
            counts.set(
              `${path}.<key>`,
              (counts.get(`${path}.<key>`) ?? 0) + k.split('\u0000').length - 1,
            );
            k2 = k.split('\u0000').join('');
          }
          return [k2, walk(x, path ? `${path}.${k2}` : k2)];
        }),
      );
    }
    return v;
  };
  return walk(value, '') as T;
}

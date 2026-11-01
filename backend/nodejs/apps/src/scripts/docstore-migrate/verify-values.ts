/**
 * The verifier's own reading of a raw MongoDB document (migration design 5.1): the driver's canonical Extended JSON (every number typed, so a Long beyond 2^53 is not rounded), turned into plain
 * JSON by a decoder that shares no code with the copy's `neutral()` (libs/repositories/import.ts). A bug in how the copy reads a date,
 * a long or an id therefore shows up as a difference instead of corrupting both sides alike.
 */
import { BSON } from 'mongodb';

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

const ISO_MS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

function scalar(o: Record<string, unknown>): { v: Json } | undefined {
  const keys = Object.keys(o);
  if (keys.length !== 1) return undefined;
  const k = keys[0] as string;
  const x = o[k];
  switch (k) {
    case '$oid':
      return { v: String(x).toLowerCase() };
    case '$date': {
      if (typeof x === 'string')
        return { v: ISO_MS.test(x) ? x : new Date(x).toISOString() };
      const ms = Number((x as { $numberLong: string }).$numberLong);
      return { v: new Date(ms).toISOString() };
    }
    case '$numberLong': {
      const n = Number(x);
      return { v: Number.isSafeInteger(n) ? n : String(x) };
    }
    case '$numberInt':
    case '$numberDouble':
      return { v: Number(x) };
    case '$numberDecimal':
      return { v: String(x) };
    case '$binary':
      return { v: (x as { base64: string }).base64 };
    case '$uuid':
      return { v: String(x) };
    default:
      return undefined;
  }
}

/** Plain JSON for a raw document; `nul` counts U+0000 removed from strings and keys by path. */
export function canonicalDocument(
  raw: unknown,
  nul: Map<string, number>,
): Record<string, Json> {
  // a value that was never stored (JS undefined) is absent; Extended JSON would turn it into null
  const defined = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(defined);
    if (
      v !== null &&
      typeof v === 'object' &&
      [Object.prototype, null].includes(
        Object.getPrototypeOf(v) as object | null,
      )
    )
      return Object.fromEntries(
        Object.entries(v)
          .filter(([, x]) => x !== undefined)
          .map(([k, x]) => [k, defined(x)]),
      );
    return v;
  };
  const ejson = BSON.EJSON.serialize(defined(raw), {
    relaxed: false,
  }) as unknown;
  const strip = (s: string, path: string): string => {
    if (!s.includes('\u0000')) return s;
    nul.set(path, (nul.get(path) ?? 0) + s.split('\u0000').length - 1);
    return s.split('\u0000').join('');
  };
  const walk = (v: unknown, path: string): Json | undefined => {
    if (v === undefined || typeof v === 'function') return undefined;
    if (v === null || typeof v === 'boolean' || typeof v === 'number') return v;
    if (typeof v === 'string') return strip(v, path);
    if (Array.isArray(v)) return v.map((x) => walk(x, `${path}[]`) ?? null);
    const o = v as Record<string, unknown>;
    const s = scalar(o);
    if (s) return s.v;
    const out: Array<[string, Json]> = [];
    for (const [k, x] of Object.entries(o)) {
      let k2 = k;
      if (k.includes('\u0000')) {
        nul.set(
          `${path}.<key>`,
          (nul.get(`${path}.<key>`) ?? 0) + k.split('\u0000').length - 1,
        );
        k2 = k.split('\u0000').join('');
      }
      const y = walk(x, path ? `${path}.${k2}` : k2);
      if (y !== undefined) out.push([k2, y]);
    }
    // fromEntries, not `out[k] =`: an own `__proto__` key would set the prototype and vanish
    return Object.fromEntries(out);
  };
  return walk(ejson, '') as Record<string, Json>;
}

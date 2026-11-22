import { docstoreMetrics } from './metrics';

/**
 * Removes U+0000 from every string and every object key in `value` (PostgreSQL rejects it in text and jsonb;
 * Mongo stored it). Returns the same value when nothing contains one. Counted per repository and field.
 */
export function stripNul<T>(value: T, repo: string, field = ''): T {
  let n = 0;
  const walk = (v: unknown): unknown => {
    if (typeof v === 'string') {
      if (!v.includes('\u0000')) return v;
      n += v.split('\u0000').length - 1;
      return v.split('\u0000').join('');
    }
    if (Array.isArray(v)) {
      const out = v.map(walk);
      return out.some((x, i) => x !== v[i]) ? out : v;
    }
    if (
      v &&
      typeof v === 'object' &&
      [Object.prototype, null].includes(
        Object.getPrototypeOf(v) as object | null,
      )
    ) {
      const src = v as Record<string, unknown>;
      let changed = false;
      const out: Record<string, unknown> = {};
      for (const [k, x] of Object.entries(src)) {
        const k2 = walk(k) as string;
        const x2 = walk(x);
        changed ||= k2 !== k || x2 !== x;
        if (Object.prototype.hasOwnProperty.call(out, k2))
          throw new Error('two keys are the same once U+0000 is removed');
        out[k2] = x2;
      }
      return changed ? out : v;
    }
    return v; // numbers, Dates, Buffers, class instances
  };
  const out = walk(value) as T;
  if (n > 0) docstoreMetrics.nulStripped.inc({ repo, field }, n);
  return out;
}

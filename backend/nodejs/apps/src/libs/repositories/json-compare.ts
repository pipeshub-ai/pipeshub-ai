/**
 * JSON-semantic comparison of two API payloads (what JSON.parse-based clients and response validators can observe): same key SET per object (absent is not null),
 * same JSON type per value (number is not string), arrays in order, object key order ignored. Both sides are first
 * put through JSON, as on the wire (Dates become ISO strings, `undefined` disappears).
 */
const wire = (x: unknown): unknown =>
  x === undefined ? undefined : JSON.parse(JSON.stringify(x));

const own = (o: object, k: string): boolean =>
  Object.prototype.hasOwnProperty.call(o, k);

const kind = (v: unknown): string =>
  v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v;

function walk(a: unknown, b: unknown, path: string, out: string[]): void {
  const ka = kind(a);
  if (ka !== kind(b)) {
    out.push(`${path}: type ${ka} != ${kind(b)}`);
  } else if (ka === 'array') {
    const x = a as unknown[];
    const y = b as unknown[];
    if (x.length !== y.length)
      out.push(`${path}: length ${x.length} != ${y.length}`);
    x.forEach((v, i) => {
      if (i < y.length) walk(v, y[i], `${path}[${i}]`, out);
    });
  } else if (ka === 'object') {
    const x = a as Record<string, unknown>;
    const y = b as Record<string, unknown>;
    for (const k of new Set([...Object.keys(x), ...Object.keys(y)])) {
      if (!own(x, k)) out.push(`${path}.${k}: absent in left`);
      else if (!own(y, k)) out.push(`${path}.${k}: absent in right`);
      else walk(x[k], y[k], `${path}.${k}`, out);
    }
  } else if (a !== b) {
    out.push(`${path}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
  }
}

/** The differences, as `path: reason` lines; empty when the payloads are equal. */
export function jsonDiff(left: unknown, right: unknown): string[] {
  const out: string[] = [];
  walk(wire(left), wire(right), '$', out);
  return out;
}

export const jsonEqual = (left: unknown, right: unknown): boolean =>
  jsonDiff(left, right).length === 0;

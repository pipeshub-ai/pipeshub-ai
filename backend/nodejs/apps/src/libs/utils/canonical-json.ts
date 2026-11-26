import { createHash } from 'crypto';

// RFC 8785 JSON Canonicalization Scheme. Must match harness_core/contracts/canonical_json.py
// byte for byte; shared golden vectors: contracts/harness/vectors/canonical-json.json.
// JCS does not normalize Unicode: NFC and NFD spellings hash differently.

export class CanonicalJsonError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CanonicalJsonError';
  }
}

// Object assignment (zod, spreads) turns a "__proto__" member into a prototype change, so JS
// would silently drop a key Python keeps. The contract forbids the key in both languages.
export const RESERVED_KEY = '__proto__';

function hasReservedKey(value: object): boolean {
  return Object.prototype.hasOwnProperty.call(value, RESERVED_KEY);
}

/** Throws CanonicalJsonError if any object nested in a parsed JSON value has a "__proto__" key. */
export function assertNoReservedKeys(value: unknown): void {
  if (value === null || typeof value !== 'object') return;
  if (Array.isArray(value)) {
    value.forEach(assertNoReservedKeys);
    return;
  }
  if (hasReservedKey(value)) {
    throw new CanonicalJsonError(`object key ${RESERVED_KEY} is reserved`);
  }
  Object.values(value).forEach(assertNoReservedKeys);
}

function hasLoneSurrogate(value: string): boolean {
  for (let i = 0; i < value.length; i++) {
    const unit = value.charCodeAt(i);
    if (unit >= 0xdc00 && unit <= 0xdfff) return true;
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(i + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      i++;
    }
  }
  return false;
}

function formatString(value: string): string {
  if (hasLoneSurrogate(value))
    throw new CanonicalJsonError('lone surrogate in string');
  // Well-formed JSON.stringify escapes exactly what RFC 8785 section 3.2.2.2 requires.
  return JSON.stringify(value);
}

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new CanonicalJsonError('NaN and Infinity are not JSON numbers');
  }
  // ECMAScript Number::toString; JSON.stringify(-0) is "0" as RFC 8785 requires.
  return JSON.stringify(value);
}

function hasToJson(
  value: object,
): value is { toJSON: (key: string) => unknown } {
  return typeof (value as { toJSON?: unknown }).toJSON === 'function';
}

function write(value: unknown, out: string[]): void {
  if (value === null) {
    out.push('null');
    return;
  }
  switch (typeof value) {
    case 'boolean':
      out.push(value ? 'true' : 'false');
      return;
    case 'number':
      out.push(formatNumber(value));
      return;
    case 'string':
      out.push(formatString(value));
      return;
    case 'object':
      break;
    case 'bigint':
    case 'symbol':
    case 'undefined':
    case 'function':
      throw new CanonicalJsonError(`${typeof value} is not a JSON value`);
  }
  if (hasToJson(value)) {
    write(value.toJSON(''), out);
    return;
  }
  if (Array.isArray(value)) {
    out.push('[');
    value.forEach((item: unknown, i) => {
      if (i > 0) out.push(',');
      write(item, out);
    });
    out.push(']');
    return;
  }
  const proto: unknown = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) {
    throw new CanonicalJsonError(
      'only plain objects, arrays and toJSON values are JSON values',
    );
  }
  if (hasReservedKey(value)) {
    throw new CanonicalJsonError(`object key ${RESERVED_KEY} is reserved`);
  }
  // Undefined members are omitted, as JSON.stringify does; default sort is UTF-16 code unit order.
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  out.push('{');
  entries.forEach(([key, item], i) => {
    if (i > 0) out.push(',');
    out.push(formatString(key), ':');
    write(item, out);
  });
  out.push('}');
}

export function canonicalJson(value: unknown): string {
  const out: string[] = [];
  write(value, out);
  return out.join('');
}

export function canonicalJsonBytes(value: unknown): Buffer {
  return Buffer.from(canonicalJson(value), 'utf8');
}

export function canonicalSha256(value: unknown): string {
  return createHash('sha256').update(canonicalJsonBytes(value)).digest('hex');
}

export function canonicalDigest(value: unknown): `sha256:${string}` {
  return `sha256:${canonicalSha256(value)}`;
}

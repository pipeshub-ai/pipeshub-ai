/**
 * Refuses request data that is too big to validate cheaply, before any schema
 * sees it. A schema without `.max()` records one validation issue per failing
 * element, so an array of a million bad items (or a record of 600k bad keys)
 * costs memory and time proportional to its size, and past about a million
 * issues zod itself overflows the stack (GHSA-mrhx-c7xc-m3w5). Schemas that do
 * carry a `.max()` are already protected by zod, this covers the rest.
 *
 * Limits (each read once, when the module loads):
 *  - VALIDATION_MAX_ARRAY_LENGTH  longest single array          (default 10000)
 *  - VALIDATION_MAX_OBJECT_KEYS   most keys in a single object  (default 10000)
 *  - VALIDATION_MAX_TOTAL_ITEMS   array elements plus object keys over the whole
 *                                 request                       (default 100000)
 *  - nesting deeper than 64 levels is refused as well.
 */
import { Logger } from '../services/logger.service';

const DEFAULT_MAX_ARRAY_LENGTH = 10_000;
const DEFAULT_MAX_OBJECT_KEYS = 10_000;
const DEFAULT_MAX_TOTAL_ITEMS = 100_000;
const DEFAULT_MAX_DEPTH = 64;

/** A plain decimal integer of at least 1, nothing else ("1e3", "0x10", "10.5", " 5" are refused). */
export function readPositiveInt(
  name: string,
  fallback: number,
  env: NodeJS.ProcessEnv = process.env,
): number {
  const raw = env[name];
  if (raw === undefined || raw === '') return fallback;
  if (/^[1-9]\d{0,14}$/.test(raw)) return Number(raw);
  Logger.getInstance().warn(
    `${name} must be a whole number of at least 1; using the default ${String(fallback)}`,
  );
  return fallback;
}

export const VALIDATION_MAX_ARRAY_LENGTH = readPositiveInt(
  'VALIDATION_MAX_ARRAY_LENGTH',
  DEFAULT_MAX_ARRAY_LENGTH,
);
export const VALIDATION_MAX_OBJECT_KEYS = readPositiveInt(
  'VALIDATION_MAX_OBJECT_KEYS',
  DEFAULT_MAX_OBJECT_KEYS,
);
export const VALIDATION_MAX_TOTAL_ITEMS = readPositiveInt(
  'VALIDATION_MAX_TOTAL_ITEMS',
  DEFAULT_MAX_TOTAL_ITEMS,
);

export interface ArrayGuardViolation {
  path: string;
  reason: 'array_too_long' | 'too_many_keys' | 'too_many_items' | 'too_deep';
  limit: number;
}

export interface ArrayGuardOptions {
  maxArrayLength?: number;
  maxObjectKeys?: number;
  maxTotalItems?: number;
  maxDepth?: number;
}

/**
 * Plain JSON containers only. Buffers, typed arrays, Dates, Maps and class
 * instances (uploaded file contents travel in req.body as Buffers) are leaves:
 * walking them would cost a string per byte.
 */
function isPlainObject(value: object): boolean {
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/**
 * Walks `roots` (name -> value, e.g. body/query/params) without recursion and
 * returns the first limit that is broken, or undefined.
 */
export function findArrayLengthViolation(
  roots: Record<string, unknown>,
  options: ArrayGuardOptions = {},
): ArrayGuardViolation | undefined {
  const maxArrayLength = options.maxArrayLength ?? VALIDATION_MAX_ARRAY_LENGTH;
  const maxObjectKeys = options.maxObjectKeys ?? VALIDATION_MAX_OBJECT_KEYS;
  const maxTotalItems = options.maxTotalItems ?? VALIDATION_MAX_TOTAL_ITEMS;
  const maxDepth = options.maxDepth ?? DEFAULT_MAX_DEPTH;

  interface Frame {
    value: object;
    key: string;
    parent: Frame | undefined;
    depth: number;
  }
  const pathOf = (frame: Frame): string => {
    const parts: string[] = [];
    for (let f: Frame | undefined = frame; f; f = f.parent) parts.push(f.key);
    return parts.reverse().join('.');
  };
  const isContainer = (v: unknown): v is object =>
    v !== null &&
    typeof v === 'object' &&
    (Array.isArray(v) || isPlainObject(v));

  const stack: Frame[] = [];
  for (const [key, value] of Object.entries(roots)) {
    if (isContainer(value)) {
      stack.push({ value, key, parent: undefined, depth: 0 });
    }
  }

  let totalItems = 0;
  while (stack.length > 0) {
    const frame = stack.pop() as Frame;
    const { value, depth } = frame;

    if (depth > maxDepth) {
      return { path: pathOf(frame), reason: 'too_deep', limit: maxDepth };
    }

    if (Array.isArray(value)) {
      if (value.length > maxArrayLength) {
        return {
          path: pathOf(frame),
          reason: 'array_too_long',
          limit: maxArrayLength,
        };
      }
      totalItems += value.length;
      if (totalItems > maxTotalItems) {
        return {
          path: pathOf(frame),
          reason: 'too_many_items',
          limit: maxTotalItems,
        };
      }
      for (let i = 0; i < value.length; i++) {
        const child: unknown = value[i];
        if (isContainer(child)) {
          stack.push({
            value: child,
            key: String(i),
            parent: frame,
            depth: depth + 1,
          });
        }
      }
      continue;
    }

    const keys = Object.keys(value);
    if (keys.length > maxObjectKeys) {
      return {
        path: pathOf(frame),
        reason: 'too_many_keys',
        limit: maxObjectKeys,
      };
    }
    totalItems += keys.length;
    if (totalItems > maxTotalItems) {
      return {
        path: pathOf(frame),
        reason: 'too_many_items',
        limit: maxTotalItems,
      };
    }
    for (const key of keys) {
      const child = (value as Record<string, unknown>)[key];
      if (isContainer(child)) {
        stack.push({ value: child, key, parent: frame, depth: depth + 1 });
      }
    }
  }
  return undefined;
}

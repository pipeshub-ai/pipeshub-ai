/**
 * QA-14: one canary scan for every Node surface, over the shared token vector
 * (contracts/harness/vectors/token-patterns.json, QA-11's single source).
 *
 * A secret (a planted `pipeshub-canary-<uuid>`, a Node-minted token, a vendor key)
 * may appear on no surface. A private canary (a person's text) may appear only
 * on that person's own views; callers pass it for operator surfaces only.
 */
import { expect } from 'chai';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

interface RawEntry {
  kind: string;
  source: string;
  forbidden?: boolean;
  examples?: string[][];
}

export interface VectorExample {
  id: string;
  kind: string;
  secret: string;
}

const raw = JSON.parse(
  readFileSync(resolve(__dirname, '../../../../../../../contracts/harness/vectors/token-patterns.json'), 'utf8'),
) as { mask: string; patterns: RawEntry[]; redactOnly: RawEntry[] };

export const VECTOR_MASK = raw.mask;

export interface ShapePattern {
  kind: string;
  pattern: RegExp;
  forbidden: boolean;
  redactOnly: boolean;
}

export const SHAPES: readonly ShapePattern[] = [
  ...raw.patterns.map((p) => ({ kind: p.kind, pattern: new RegExp(p.source), forbidden: !!p.forbidden, redactOnly: false })),
  ...raw.redactOnly.map((p) => ({ kind: p.kind, pattern: new RegExp(p.source), forbidden: false, redactOnly: true })),
];

/** Joined from parts here: the vector file holds no literal credential. */
export const VECTOR_EXAMPLES: readonly VectorExample[] = [...raw.patterns, ...raw.redactOnly].flatMap((entry) =>
  (entry.examples ?? []).map((parts, index) => ({ id: `${entry.kind}-${index}`, kind: entry.kind, secret: parts.join('') })),
);

export function canary(label = 'canary'): string {
  return `pipeshub-${label}-${randomUUID()}`;
}

export interface CanaryScanOptions {
  secrets?: readonly string[];
  privates?: readonly string[];
  /** Which vector shapes count as a leak; `vendor` leaves out the redact-only `Bearer` heuristic. */
  shapes?: 'all' | 'vendor' | 'none';
}

/** Every string reachable from `value`, keys included; JSON escaping would hide a key's newline. */
export function stringsIn(value: unknown, seen = new Set<unknown>()): string[] {
  if (typeof value === 'string') return [value];
  if (Buffer.isBuffer(value)) return [value.toString('utf8')];
  if (value === null || value === undefined) return [];
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint' || typeof value === 'symbol') {
    return [String(value)];
  }
  if (typeof value === 'function') return [value.toString()];
  // A value already walked has given its strings once.
  if (seen.has(value)) return [];
  seen.add(value);
  if (value instanceof Map) return [...value.entries()].flatMap(([k, v]) => [...stringsIn(k, seen), ...stringsIn(v, seen)]);
  if (Array.isArray(value) || value instanceof Set) return [...value].flatMap((v) => stringsIn(v, seen));
  return Object.entries(value as Record<string, unknown>).flatMap(([k, v]) => [k, ...stringsIn(v, seen)]);
}

/** What leaked, as `kind` labels; a secret is named by its first 8 characters only. */
export function findCanaries(value: unknown, options: CanaryScanOptions = {}): string[] {
  const texts = stringsIn(value);
  const found: string[] = [];
  const shapes = options.shapes ?? 'all';
  for (const needle of options.secrets ?? []) {
    if (needle && texts.some((t) => t.includes(needle))) found.push(`secret ${needle.slice(0, 8)}...`);
  }
  for (const needle of options.privates ?? []) {
    if (needle && texts.some((t) => t.includes(needle))) found.push(`private ${needle.slice(0, 8)}...`);
  }
  if (shapes !== 'none') {
    for (const shape of SHAPES) {
      if (shape.redactOnly && shapes === 'vendor') continue;
      if (texts.some((t) => shape.pattern.test(t))) found.push(`shape ${shape.kind}`);
    }
  }
  return found;
}

export function assertNoCanary(where: string, value: unknown, options: CanaryScanOptions = {}): void {
  expect(findCanaries(value, options), `${where} carries a canary`).to.deep.equal([]);
}

/**
 * Every carrier, built-in and the edition's (`EDITION_DOCSTORE_COVERAGE.carriers`, src/edition.ts), checked once when loaded: a
 * carrier may carry only collections and fields the mappings hold, and no two carry the same one. `HELD` (mapping/index.ts) is
 * what no carrier carries.
 */
import { EDITION_DOCSTORE_COVERAGE } from '../../../edition';
import { CODING_ENGINE_CHOICES } from './choices';
import { DATA_KEYS_AND_WORKSPACES } from './keys-workspaces';
import { AUDIT_CHAIN } from './audit';
import { DURABLE_RUNS } from './durable-runs';
import { CONTROL_PLANE } from './control-plane';
import { LEDGERS } from './ledgers';
import { PEOPLES_DATA } from './people';
import { ERASURE_JOBS } from './erasure';
import type { Carrier } from './types';

/** The store groups this build carries, in copy order. */
export const BUILT_IN_CARRIERS: readonly Carrier[] = [
  CODING_ENGINE_CHOICES,
  DATA_KEYS_AND_WORKSPACES,
  AUDIT_CHAIN,
  DURABLE_RUNS,
  LEDGERS,
  PEOPLES_DATA,
  ERASURE_JOBS,
  CONTROL_PLANE,
];

/**
 * Checks a list of carriers against what may be carried (`heldCollections`: collection names; `heldFields`: `collection.path`):
 * returns the problems (empty: fine).
 */
export function carrierProblems(
  carriers: readonly Carrier[],
  heldCollections: ReadonlySet<string>,
  heldFields: ReadonlySet<string>,
): string[] {
  const problems: string[] = [];
  const owner = new Map<string, string>();
  const claim = (what: string, by: string): void => {
    const other = owner.get(what);
    if (other) problems.push(`${what} is carried by both ${other} and ${by}`);
    else owner.set(what, by);
  };
  for (const c of carriers) {
    for (const coll of c.collections) {
      if (!heldCollections.has(coll))
        problems.push(
          `${c.name} carries ${coll}, which is not a held collection`,
        );
      claim(coll, c.name);
    }
    for (const [coll, paths] of Object.entries(c.fields))
      for (const p of paths) {
        if (!heldFields.has(`${coll}.${p}`))
          problems.push(
            `${c.name} carries ${coll}.${p}, which is not a held field`,
          );
        claim(`${coll}.${p}`, c.name);
      }
    if (!c.tables.length) problems.push(`${c.name} names no table`);
    // the copy offers one credential per account (credentials.ts) and the follower rewrites an account's credentials outside the
    // document units, so no unit writes a credential's carried fields with it
    if (c.withDocument && (c.fields.userCredentials ?? []).length)
      problems.push(
        `${c.name} writes userCredentials fields with their documents, which the credential copy does not support`,
      );
  }
  return problems;
}

const METHODS = [
  'write',
  'remove',
  'expected',
  'actual',
  'restore',
  'clear',
] as const;

/** An edition's carrier, given as `unknown` by the import-free edition file: refused, with what is wrong, unless it has the shape. */
export function asCarrier(value: unknown, index: number): Carrier {
  const c = value as Partial<Record<string, unknown>> | null;
  const wrong: string[] = [];
  if (c === null || typeof c !== 'object')
    throw new Error(`edition carrier ${String(index)} is not an object`);
  if (typeof c.name !== 'string' || !c.name) wrong.push('name');
  if (typeof c.reason !== 'string') wrong.push('reason');
  if (!Array.isArray(c.collections)) wrong.push('collections');
  if (c.fields === null || typeof c.fields !== 'object') wrong.push('fields');
  if (!Array.isArray(c.tables)) wrong.push('tables');
  for (const m of METHODS) if (typeof c[m] !== 'function') wrong.push(m);
  if (wrong.length)
    throw new Error(
      `edition carrier ${String(index)} (${String(c.name)}) is not a docstore-migrate Carrier: ${wrong.join(', ')}`,
    );
  return c as unknown as Carrier;
}

/** The edition's carriers (src/edition.ts, `EDITION_DOCSTORE_COVERAGE.carriers`). */
export const EDITION_CARRIERS: readonly Carrier[] =
  EDITION_DOCSTORE_COVERAGE.carriers.map(asCarrier);

export const CARRIERS: readonly Carrier[] = [
  ...BUILT_IN_CARRIERS,
  ...EDITION_CARRIERS,
];

/** The carrier of a whole collection, if any. */
export const carrierOfCollection = (collection: string): Carrier | undefined =>
  CARRIERS.find((c) => c.collections.includes(collection));

/** The carriers of fields of a mapped collection. */
export const carriersOfFields = (collection: string): Carrier[] =>
  CARRIERS.filter((c) => (c.fields[collection] ?? []).length > 0);

/** Every collection a carrier reads (whole or for some fields). */
export const carriedSources = (c: Carrier): string[] => [
  ...c.collections,
  ...Object.keys(c.fields).filter((k) => !c.collections.includes(k)),
];

import {
  EDITION_DOCSTORE_COVERAGE,
  type EditionDocstoreCoverage,
} from '../../../edition';
import { CONTENT_IGNORED, CONTENT_MAPPINGS } from './content';
import { CONVERSATION_IGNORED, CONVERSATION_MAPPINGS } from './conversation';
import { HARNESS_HELD, HARNESS_IGNORED } from './harness';
import { IDENTITY_IGNORED, IDENTITY_MAPPINGS } from './identity';
import { CARRIERS, carrierProblems } from '../carry';
import type { Carrier } from '../carry/types';
import type {
  CollectionMapping,
  HeldCollection,
  IgnoredCollection,
} from './types';

/**
 * An edition's held fields joined to the mapping of their collection (a mapping without one is returned as it is). A field of a
 * collection no mapping copies is refused: it would be held by nothing.
 */
export function withEditionHeldFields(
  mappings: CollectionMapping[],
  heldFields: EditionDocstoreCoverage['heldFields'],
): CollectionMapping[] {
  const orphans = heldFields.filter(
    (h) => !mappings.some((m) => m.collection === h.collection),
  );
  if (orphans.length)
    throw new Error(
      `edition held fields of collections no mapping copies: ${orphans.map((h) => `${h.collection}.${h.path}`).join(', ')}`,
    );
  return mappings.map((m) => {
    const extra = heldFields
      .filter((h) => h.collection === m.collection)
      .map(({ path, reason }) => ({ path, reason }));
    return extra.length ? { ...m, held: [...(m.held ?? []), ...extra] } : m;
  });
}

/**
 * Every collection the application stores, parents before children (the order of the copy): identity, then conversation, then
 * content, OAuth and platform data. Foreign keys inside a schema need the parent row to be there first.
 */
export const MAPPINGS: CollectionMapping[] = withEditionHeldFields(
  [...IDENTITY_MAPPINGS, ...CONVERSATION_MAPPINGS, ...CONTENT_MAPPINGS],
  EDITION_DOCSTORE_COVERAGE.heldFields,
);

/** Collections that are deliberately not copied, with the reason. */
export const IGNORED: IgnoredCollection[] = [
  ...IDENTITY_IGNORED,
  ...CONVERSATION_IGNORED,
  ...CONTENT_IGNORED,
  ...HARNESS_IGNORED,
  ...EDITION_DOCSTORE_COVERAGE.ignored.map(({ tables, ...i }) => ({
    ...i,
    ...(tables ? { tables: [...tables] } : {}),
  })),
];

/**
 * Every collection whose data is not copied by the generic path, this build's and the edition's (`EDITION_DOCSTORE_COVERAGE.held`):
 * carried by a carrier (`../carry`, the edition's `carriers` included), or held. A field of a mapped collection is in the same
 * position (`CollectionMapping.held`, the edition's `heldFields` joined in).
 */
export const OUTSIDE_GENERIC: HeldCollection[] = [
  ...HARNESS_HELD,
  ...EDITION_DOCSTORE_COVERAGE.held.map((h) => ({
    ...h,
    tables: [...h.tables],
  })),
];

const problems = carrierProblems(
  CARRIERS,
  new Set(OUTSIDE_GENERIC.map((h) => h.collection)),
  new Set(
    MAPPINGS.flatMap((m) =>
      (m.held ?? []).map((h) => `${m.collection}.${h.path}`),
    ),
  ),
);
if (problems.length)
  throw new Error(`docstore-migrate carriers: ${problems.join('; ')}`);

/** The collections outside the generic path that these carriers copy, and the ones left held. */
export function splitOutside(carriers: readonly Carrier[]): {
  carried: Array<HeldCollection & { carrier: string }>;
  held: HeldCollection[];
} {
  const by = (collection: string): Carrier | undefined =>
    carriers.find((c) => c.collections.includes(collection));
  return {
    carried: OUTSIDE_GENERIC.filter((h) => by(h.collection)).map((h) => ({
      ...h,
      carrier: by(h.collection)?.name ?? '',
    })),
    held: OUTSIDE_GENERIC.filter((h) => !by(h.collection)),
  };
}

const split = splitOutside(CARRIERS);

/** Collections a carrier copies (as a unit with the rest of its store group), with the tables they live in. */
export const CARRIED = split.carried;

/** Collections the copy does not carry yet and must not drop: preflight refuses a move while one holds documents. */
export const HELD: HeldCollection[] = split.held;

/** The carrier of a held field of a mapped collection (undefined: the field is held, and preflight refuses a move while it is set). */
export const fieldCarrier = (
  collection: string,
  path: string,
  carriers: readonly Carrier[] = CARRIERS,
): string | undefined =>
  carriers.find((c) => (c.fields[collection] ?? []).includes(path))?.name;

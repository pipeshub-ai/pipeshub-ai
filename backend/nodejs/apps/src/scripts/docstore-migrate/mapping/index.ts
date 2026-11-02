import { IDENTITY_MAPPINGS } from './identity';
import type { CollectionMapping, IgnoredCollection } from './types';

/**
 * Every collection the application stores, parents before children (the order of the copy): identity, then conversation, then
 * content, OAuth and platform data. Foreign keys inside a schema need the parent row to be there first.
 */
export const MAPPINGS: CollectionMapping[] = [...IDENTITY_MAPPINGS];

/** Collections that are deliberately not copied, with the reason. */
export const IGNORED: IgnoredCollection[] = [];

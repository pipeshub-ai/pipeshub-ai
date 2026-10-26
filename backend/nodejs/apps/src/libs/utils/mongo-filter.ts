import type { QueryFilter } from 'mongoose';

/**
 * Hands a filter that is built up piece by piece to a Mongoose query.
 *
 * Mongoose 9 checks each filter value against the schema type, so a string id
 * for an ObjectId field, or a search clause added after the object was
 * declared, no longer type-checks even though Mongoose casts both at run time.
 * Use this for such a filter instead of an `any` or an object-literal assertion.
 */
export function asQueryFilter<T>(filter: object): QueryFilter<T> {
  return filter;
}

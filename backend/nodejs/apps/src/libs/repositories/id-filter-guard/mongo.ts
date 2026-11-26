/**
 * Refuses a filter whose identifier fields hold anything but a string, an `$eq`
 * of a string or a `$in` over strings, in the harness and control-plane Mongo stores. Callers
 * validate ids already; this keeps a value that slipped past them from
 * widening a query inside the repository.
 *
 * A filter the repository builds itself with another operator on an id field
 * (a page cursor's `$gt`, an `$exists`) says so with `idOperator`.
 *
 * `eq(value)` matches a value exactly whatever it holds: `{ $eq: value }` never
 * reads an object as operators, so a filter field built from a request takes it.
 */
import mongoose, { type Model, type Schema } from 'mongoose';
import { InvalidIdFilterError } from '../errors';

/** Identifier fields guarded wherever a schema declares them as strings. */
export const ID_FILTER_FIELDS: ReadonlySet<string> = new Set([
  'orgId',
  'runId',
  'rootRunId',
  'parentRunId',
  'sessionId',
  'sourceSessionId',
  'forkSessionId',
  'commandId',
  'workspaceId',
  'streamId',
  'attemptId',
  'workerId',
  'scheduleId',
  'memoryId',
  'userId',
  'actorId',
  'agentKey',
  'installation',
  'owner',
  'ownerId',
]);

// Enumerable, so it survives Mongoose's copy of the filter before middleware runs.
const BUILT_BY_REPOSITORY = Symbol('pipeshub.idOperator');

/** Marks an operator object a repository builds on an id field (`{ $gt: cursor }`) as its own. */
export function idOperator<T extends Record<string, unknown>>(operator: T): T {
  (operator as Record<symbol, unknown>)[BUILT_BY_REPOSITORY] = true;
  return operator;
}

/** An exact match on `value`: an object in it is compared, never read as operators. */
export const eq = <T>(value: T): { $eq: T } => ({ $eq: value });

const isRecord = (value: unknown): value is Record<string | symbol, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function idValueAllowed(value: unknown): boolean {
  if (value === undefined || value === null || typeof value === 'string')
    return true;
  if (!isRecord(value)) return false;
  if (value[BUILT_BY_REPOSITORY] === true) return true;
  const keys = Object.keys(value);
  if (keys.length !== 1) return false;
  if (keys[0] === '$eq') return typeof value['$eq'] === 'string';
  const list = value['$in'];
  return (
    keys[0] === '$in' &&
    Array.isArray(list) &&
    list.every((item) => typeof item === 'string')
  );
}

/** Throws `InvalidIdFilterError` when a guarded field of `filter` (or of its `$and`/`$or`/`$nor` branches) is not a string, an `$eq` of a string or a `$in` of strings. */
export function assertIdFilter(
  filter: unknown,
  fields: ReadonlySet<string> = ID_FILTER_FIELDS,
): void {
  if (!isRecord(filter) || fields.size === 0) return;
  for (const [key, value] of Object.entries(filter)) {
    if (key === '$and' || key === '$or' || key === '$nor') {
      if (Array.isArray(value))
        for (const branch of value) assertIdFilter(branch, fields);
    } else if (fields.has(key) && !idValueAllowed(value)) {
      throw new InvalidIdFilterError(key);
    }
  }
}

/** `assertIdFilter` over every `$match` stage of an aggregation pipeline. */
export function assertIdPipeline(
  pipeline: readonly unknown[],
  fields: ReadonlySet<string> = ID_FILTER_FIELDS,
): void {
  for (const stage of pipeline) {
    if (isRecord(stage) && '$match' in stage)
      assertIdFilter(stage['$match'], fields);
  }
}

const guarded = new WeakMap<Schema, ReadonlySet<string>>();

/** The guarded fields `schema` declares as strings (all of them for a schema the plugin never saw). */
export function idFieldsOf(schema: Schema): ReadonlySet<string> {
  return guarded.get(schema) ?? ID_FILTER_FIELDS;
}

const FILTER_BULK_OPS = [
  'updateOne',
  'updateMany',
  'replaceOne',
  'deleteOne',
  'deleteMany',
] as const;

const QUERY_OPS = [
  'countDocuments',
  'distinct',
  'find',
  'findOne',
  'findOneAndDelete',
  'findOneAndReplace',
  'findOneAndUpdate',
  'replaceOne',
  'updateMany',
  'updateOne',
  'deleteMany',
  'deleteOne',
] as const;

/**
 * Schema plugin: checks the filter of every query, every `$match` of an
 * aggregation and every filtered operation of a `bulkWrite` on the model.
 * Applying it twice to one schema is a no-op.
 */
export function guardIdFilters(schema: Schema): void {
  if (guarded.has(schema)) return;
  const fields = new Set(
    [...ID_FILTER_FIELDS].filter(
      (field) =>
        schema.pathType(field) === 'real' &&
        schema.path(field).instance === 'String',
    ),
  );
  guarded.set(schema, fields);
  if (fields.size === 0) return;
  schema.pre([...QUERY_OPS], { query: true, document: false }, function () {
    assertIdFilter(this.getFilter(), fields);
  });
  schema.pre('aggregate', function () {
    assertIdPipeline(this.pipeline(), fields);
  });
  schema.pre('bulkWrite', function (ops: unknown) {
    if (!Array.isArray(ops)) return;
    for (const op of ops) {
      if (!isRecord(op)) continue;
      for (const name of FILTER_BULK_OPS) {
        const body = op[name];
        if (isRecord(body)) assertIdFilter(body['filter'], fields);
      }
    }
  });
}

/** A model with its id filters guarded; registered once per process. */
export function guardedModel<T>(name: string, schema: Schema<T>): Model<T> {
  const existing = mongoose.models[name] as Model<T> | undefined;
  if (existing !== undefined) return existing;
  guardIdFilters(schema as Schema);
  return mongoose.model<T>(name, schema);
}

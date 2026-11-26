import mongoose, { type Model, type Schema } from 'mongoose';
import { deploymentRegion } from '../../../../modules/agent-harness/domain/region';
import { guardIdFilters } from '../../id-filter-guard/mongo';

/** The path every stored harness document carries (OWD-12). */
export const REGION_PATH = 'region';

let region: string | null = null;
/** Read once per process: the deployment's region does not move while it runs. */
const here = (): string => (region ??= deploymentRegion());

/**
 * Stamps the deployment's region on every document the schema inserts:
 * `create`, `insertMany`, `bulkWrite` inserts and upserts (Mongoose applies
 * defaults on insert). An update never moves an existing document's region.
 * Reads leave it out unless they ask for it: it is where the document lives,
 * not part of what the stores hand their callers.
 */
export function stampRegion(schema: Schema): void {
  if (schema.pathType(REGION_PATH) === 'real') return;
  schema.add({ [REGION_PATH]: { type: String, default: here, select: false } });
}

/** A harness model with its region stamped and its id filters guarded; the model is registered once per process. */
export function harnessModel<T>(name: string, schema: Schema<T>): Model<T> {
  const existing = mongoose.models[name] as Model<T> | undefined;
  if (existing !== undefined) return existing;
  stampRegion(schema as Schema);
  guardIdFilters(schema as Schema);
  return mongoose.model<T>(name, schema);
}

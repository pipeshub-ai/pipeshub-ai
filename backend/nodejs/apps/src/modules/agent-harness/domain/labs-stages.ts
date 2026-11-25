/**
 * The org's Labs switches as a pin's `rollout` section (02-lld §11.1, CPD-6).
 * A worker with the pin reads them from it instead of the settings document,
 * so each value is what the reader it replaces gives for the same document
 * (`contracts/harness/vectors/labs-stages.json`): the query service's
 * `read_platform_feature_flag` for the stages the worker reads, Node's own
 * readers for the ones Node reads.
 */
import type { PolicyLayer } from '../contracts/generated/core/policy/policy-layer';
import { EDITION_WORKER_STAGES } from '../../../edition';

export type RolloutStages = NonNullable<PolicyLayer['rollout']>;

/** Read by the durable worker and the query service (`app/services/featureflag/platform_settings.py`); an edition adds its own (`EDITION_WORKER_STAGES`). */
export const WORKER_STAGES = [
  'ENABLE_SKILLS',
  'ENABLE_USER_CONTEXT',
  'ENABLE_KNOWLEDGE_PUBLICATIONS',
  'ENABLE_ACTIONS',
  'ENABLE_MCP',
  'ENABLE_NAMED_ENTITY_EXTRACTION',
] as const;

/** Read by Node at admission and on its routes (`platform-rollout-settings.ts`). */
export const NODE_STAGES = ['ENABLE_SCHEDULED_RUNS'] as const;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Python's `bool(value)` for a JSON value: an empty string, list or object is false. */
const truthy = (value: unknown): boolean => {
  if (Array.isArray(value)) return value.length > 0;
  if (isRecord(value)) return Object.keys(value).length > 0;
  return Boolean(value);
};

/** What the settings registry says of the flags, which the host reads for the domain. */
export interface LabsRegistry {
  /** A flag's release default (`PLATFORM_FEATURE_FLAGS`, equal to the worker's `PLATFORM_FLAG_DEFAULTS`). */
  releaseDefault(key: string): boolean;
  /** Each flag's effective value as Node's readers see the stored document. */
  effective(stored: Record<string, unknown>): Record<string, boolean>;
}

/** One stage as the worker's reader answers it: keys compared in upper case, a stored value by its truth, absent by the release default. */
export function workerStage(
  stored: unknown,
  key: string,
  releaseDefault: boolean,
): boolean {
  const flags = isRecord(stored) ? stored['featureFlags'] : undefined;
  if (!isRecord(flags)) return releaseDefault;
  let found: { value: unknown } | null = null;
  // The last key that normalizes to this one wins, as Python's dict of upper-cased keys.
  for (const [name, value] of Object.entries(flags)) {
    if (name.toUpperCase() === key) found = { value };
  }
  return found === null ? releaseDefault : truthy(found.value);
}

/** Every Labs stage of the dimension table but the two the rollout decides (`ENABLE_AGENT_HARNESS`, the kill switch). */
export function labsStages(
  stored: unknown,
  registry: LabsRegistry,
): RolloutStages {
  const effective = registry.effective(isRecord(stored) ? stored : {});
  return {
    ...Object.fromEntries(
      [...WORKER_STAGES, ...EDITION_WORKER_STAGES].map((key) => [
        key,
        workerStage(stored, key, registry.releaseDefault(key)),
      ]),
    ),
    ...Object.fromEntries(
      NODE_STAGES.map((key) => [key, effective[key] === true]),
    ),
  };
}

import { policyLayerSchema } from '../contracts/generated';
import type { PolicyLayer } from './policy-alias';

/**
 * Rollout keys a layer stored before CTL-5 may still carry (the retired Labs stages). Shared with Python
 * (`harness_core.policy.retired`) through `contracts/harness/vectors/retired-layer-keys.json`.
 */
export const RETIRED_ROLLOUT_KEYS: readonly string[] = Object.freeze([
  'ENABLE_COLLABORATOR_TURNS',
  'ENABLE_AGENT_ACTIVITY_PANEL',
  'ENABLE_FOLLOWUP_SUGGESTIONS',
  'ENABLE_DURABLE_ASSISTANT_AGENT',
]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** `raw` without the retired rollout keys; a rollout section they leave empty goes too. */
function withoutRetiredKeys(raw: unknown): unknown {
  if (!isRecord(raw) || !isRecord(raw['rollout'])) return raw;
  const rollout = Object.fromEntries(
    Object.entries(raw['rollout']).filter(
      ([key]) => !RETIRED_ROLLOUT_KEYS.includes(key),
    ),
  );
  const { rollout: _stored, ...rest } = raw;
  return Object.keys(rollout).length > 0 ? { ...rest, rollout } : rest;
}

/**
 * The layer as its generated schema accepts it; null when it does not parse. A retired rollout key is stripped
 * first and every other dimension kept: dropping the layer for it would stop its denies applying (fail open).
 */
export function readPolicyLayer(raw: unknown): PolicyLayer | null {
  const parsed = policyLayerSchema.safeParse(withoutRetiredKeys(raw));
  return parsed.success ? parsed.data : null;
}

/** `agent:a7` → `agent`; a scope is `{kind}:{id}` or a bare kind (`deployment`). */
export const scopeKindOf = (scope: string): string =>
  scope.split(':', 1)[0] ?? scope;

interface LayerWriter<Tx> {
  write(
    tx: Tx,
    scope: string,
    layer: PolicyLayer | null,
    expectedRevision: number,
  ): Promise<number>;
}

/** Writes each layer in order; scope → its new revision. */
export async function writeEach<Tx>(
  store: LayerWriter<Tx>,
  tx: Tx,
  writes: ReadonlyArray<{
    scope: string;
    layer: PolicyLayer | null;
    expectedRevision: number;
  }>,
): Promise<Map<string, number>> {
  const revisions = new Map<string, number>();
  for (const write of writes) {
    revisions.set(
      write.scope,
      await store.write(tx, write.scope, write.layer, write.expectedRevision),
    );
  }
  return revisions;
}

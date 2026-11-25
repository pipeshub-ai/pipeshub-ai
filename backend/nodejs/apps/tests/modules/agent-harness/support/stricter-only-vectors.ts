/** `contracts/harness/vectors/stricter-only.json` (QA-2), shared with Python's `harness_core` policy and engine tests. */
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { LayerName } from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import type { PinnedEngineProfile } from '../../../../src/modules/agent-harness/domain/engine-profiles';
import type { ScopedLayer } from '../../../../src/modules/agent-harness/domain/policy';

export interface Ladder {
  path: string;
  base?: ScopedLayer[];
  hostOnly?: boolean;
  top: unknown;
  narrow: unknown;
  kept?: unknown;
  setBy?: LayerName;
  widen: unknown;
  at?: string;
}
export interface ChoiceSample {
  path: string;
  ceiling: unknown;
  narrowed: unknown;
  inside: unknown;
  /** A second value inside `ceiling` (C4.02 locks). */
  other: unknown;
  outside: unknown;
}
export interface EffortRow {
  requested: string | null;
  narrowing: string | null;
  byCeiling: Array<{ ceiling: string | null; pinned?: string | null; refused?: true }>;
}
interface StricterOnlyVectors {
  release: PinnedEngineProfile;
  ladders: Ladder[];
  choices: ChoiceSample[];
  operatorSample: unknown;
  efforts: EffortRow[];
  cases: Array<{
    id: string;
    layers: ScopedLayer[];
    expect: { config: unknown; digest: string } | { refusal: { code: string; dimension: string; layer: string } };
  }>;
}

const CONTRACTS = join(__dirname, '../../../../../../../contracts/harness');

/**
 * The vector, with the ladders an edition's overlay adds for the dimensions it adds
 * (`contracts/harness/ee/vectors/stricter-only.json`, when present); a ladder for a path the vector already has is refused.
 */
function withOverlayLadders(vectors: StricterOnlyVectors): StricterOnlyVectors {
  const overlay = join(CONTRACTS, 'ee', 'vectors', 'stricter-only.json');
  if (!existsSync(overlay)) return vectors;
  const { ladders } = JSON.parse(readFileSync(overlay, 'utf8')) as Pick<StricterOnlyVectors, 'ladders'>;
  const known = new Set(vectors.ladders.map((ladder) => ladder.path));
  for (const ladder of ladders) {
    if (known.has(ladder.path)) throw new Error(`${overlay}: a ladder for ${ladder.path} is already defined`);
  }
  return { ...vectors, ladders: [...vectors.ladders, ...ladders] };
}

export const STRICTER_ONLY = withOverlayLadders(
  JSON.parse(readFileSync(join(CONTRACTS, 'vectors', 'stricter-only.json'), 'utf8')) as StricterOnlyVectors,
);

/** `values` with `value` at the dotted `path`. */
export function put(values: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const keys = path.split('.');
  let node = values;
  for (const key of keys.slice(0, -1)) {
    node[key] ??= {};
    node = node[key] as Record<string, unknown>;
  }
  node[keys[keys.length - 1] as string] = value;
  return values;
}

/**
 * CPD5-23: pin-replay determinism across languages. The release layer and each
 * vector's layers resolve through CPD-3's resolver to the vector's config
 * digest, and the pin's members to its digest, as Python's pinning module
 * answers (`tests/unit/control_plane/test_pin_vectors.py`); member order never
 * changes either.
 *
 * CPD10-87: `resolved-run-config.v0.json` holds the pins the tree before
 * CPD-10 made; a stored pin is read as it is, never resolved or digested anew.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { PARENT_DIMENSIONS, PolicyResolver, type ScopedLayer } from '../../../src/modules/agent-harness/domain/policy';
import type { EffectiveRunConfig } from '../../../src/modules/agent-harness/contracts/generated/core/policy/effective-run-config';
import { canonicalDigest } from '../../../src/libs/utils/canonical-json';
import { inheritedDimensions, parentLayer } from '../../../src/modules/control-plane/application/layer-sources';
import { assemblePin, pinDigest, pinScope, type PinFields } from '../../../src/modules/control-plane/application/pin';
import { resolvedRunConfigSchema, type ResolvedRunConfig } from '../../../src/modules/control-plane/contracts/generated';
import { REFUSAL_CODES } from '../../../src/modules/control-plane/domain/refusals';

interface PinVector {
  id: string;
  layers: Array<{ scope: { kind: ScopedLayer['kind'] } } & Record<string, unknown>>;
  /** A child's parent pin: its config travels as a `parent` layer and its `llm` is the child's, unless `pin.llm` names the child's own (MA1). */
  parent?: { effectiveConfig: EffectiveRunConfig; llm: PinFields['llm']; scope?: PinFields['scope'] };
  /** Present: the pin's scope is computed from it and the parent's (`pinScope`), not read from `pin`. */
  subject?: { agentKey: string | null; projectId: string | null; agentOwner: string | null };
  pin: Omit<PinFields, 'schemaVersion' | 'effectiveConfig' | 'llm'> & { llm?: PinFields['llm'] };
  expected:
    | { configDigest: string; digest: string; inherited?: string[]; scope?: PinFields['scope'] }
    | { refusal: { code: string; dimension: string; layer: string } };
}

const VECTORS = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/control-plane/vectors/resolved-run-config.json'), 'utf8'),
) as { cases: PinVector[] };

const V0 = (
  JSON.parse(
    readFileSync(join(__dirname, '../../../../../../contracts/control-plane/vectors/resolved-run-config.v0.json'), 'utf8'),
  ) as { cases: Array<PinVector & { stored?: unknown }> }
).cases.filter((vector) => vector.stored !== undefined);

const shuffled = (value: unknown, seed: number): unknown => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return value;
  const entries = Object.entries(value as Record<string, unknown>);
  const order = entries.map((_, i) => (i * 7 + seed) % entries.length);
  const seen = new Set<number>();
  const out: Record<string, unknown> = {};
  for (const i of [...order, ...entries.keys()]) {
    if (seen.has(i)) continue;
    seen.add(i);
    const [key, inner] = entries[i]!;
    out[key] = shuffled(inner, seed);
  }
  return out;
};

function pinOf(vector: PinVector, seed: number | null): PinVector['expected'] {
  const order = <T>(value: T): T => (seed === null ? value : (shuffled(value, seed) as T));
  const { parent } = vector;
  const resolved = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    ...(parent !== undefined
      ? [{ kind: 'parent' as const, values: order(parentLayer(parent.effectiveConfig, PARENT_DIMENSIONS)) }]
      : []),
    ...vector.layers.map((layer) => ({ kind: layer.scope.kind, values: order(layer) })),
  ]);
  if (resolved.refusal !== null) {
    const { code, dimension, layer } = resolved.refusal;
    return { refusal: { code: REFUSAL_CODES[code], dimension, layer } };
  }
  if (parent === undefined) {
    const pin = assemblePin({ schemaVersion: 1, ...order(vector.pin), effectiveConfig: resolved.config } as PinFields);
    return { configDigest: resolved.digest, digest: pin.digest };
  }
  const config = inheritedDimensions(resolved.config, parent.effectiveConfig, PARENT_DIMENSIONS);
  // A vector with a `subject` has its scope computed, as the pinner does: the owner is the parent pin's.
  const scope =
    vector.subject === undefined
      ? {}
      : { scope: pinScope({ ...vector.subject, parent: parent as unknown as ResolvedRunConfig }) };
  const llm = vector.pin.llm ?? parent.llm;
  const pin = assemblePin({ schemaVersion: 1, ...order(vector.pin), ...scope, llm, effectiveConfig: config } as PinFields);
  return {
    configDigest: canonicalDigest(config),
    digest: pin.digest,
    ...scope,
    inherited: Object.keys(config.dimensions)
      .filter((path) => canonicalDigest(config.dimensions[path]) !== canonicalDigest(resolved.config.dimensions[path] ?? null))
      .sort(),
  };
}

describe('CPD5-23: pin-replay determinism (contracts/control-plane/vectors/resolved-run-config.json)', () => {
  for (const vector of VECTORS.cases) {
    it(`${vector.id}: pins the vector's digest, whatever the member order`, () => {
      expect(pinOf(vector, null)).to.deep.equal(vector.expected);
      for (const seed of [1, 2, 3]) expect(pinOf(vector, seed)).to.deep.equal(vector.expected);
    });
  }

  it('QA37-K4: the vectors hold a root with a model per role under cost ceilings, and a role-assigned child with its own `llm`', () => {
    const root = VECTORS.cases.find((c) => c.id === 'PV-9')!;
    const child = VECTORS.cases.find((c) => c.id === 'PV-10')!;
    const resolved = new PolicyResolver().resolve([
      { kind: 'release', values: RELEASE_DEFAULTS },
      ...root.layers.map((layer) => ({ kind: layer.scope.kind, values: layer })),
    ]);
    const dimensions = resolved.config?.dimensions ?? {};
    expect(dimensions['models.roles']).to.deep.include({ value: { researcher: 'anthropic:claude-haiku-4-5' }, setBy: 'agent' });
    expect(dimensions['models.roleCostCeilings']).to.deep.include({ value: { default: 20, researcher: 6 }, setBy: 'org' });
    // The child's parent is that root, config and model.
    expect(child.parent?.effectiveConfig).to.deep.equal(resolved.config);
    expect(child.parent?.llm).to.deep.equal(root.pin.llm);
    expect(child.layers).to.deep.equal([
      { schemaVersion: 1, scope: { kind: 'request' }, budgets: { maxTurns: 3 }, models: { primary: 'anthropic:claude-haiku-4-5' } },
    ]);
    expect(child.pin.llm).to.deep.include({ modelKey: 'mk-2', provider: 'anthropic', model: 'claude-haiku-4-5' });
    expect(child.pin.llm).not.to.deep.equal(child.parent?.llm);
    // Pinned on its parent's model instead, the child would have another digest: the vector holds the role's.
    const inheriting = { ...child.pin };
    delete inheriting.llm;
    const onParents = pinOf({ ...child, pin: inheriting }, null);
    expect('digest' in onParents && onParents.digest).not.to.equal('digest' in child.expected && child.expected.digest);
    expect('configDigest' in onParents && onParents.configDigest).to.equal('configDigest' in child.expected && child.expected.configDigest);
  });

  it('CPD5-23: the digest moves with any member, the head revision included', () => {
    const vector = VECTORS.cases.find((c) => 'digest' in c.expected && c.parent === undefined)!;
    const fields = { schemaVersion: 1, ...vector.pin, effectiveConfig: { schemaVersion: 1, dimensions: {} } } as PinFields;
    expect(assemblePin(fields).digest).not.to.equal(assemblePin({ ...fields, headRevision: fields.headRevision + 1 }).digest);
  });
});

describe('CPD10-87: pins made before CPD-10 (contracts/control-plane/vectors/resolved-run-config.v0.json)', () => {
  it('the file holds every pin the old tree made', () => {
    expect(V0.map((vector) => vector.id)).to.deep.equal(['PV-1', 'PV-3', 'PV-4', 'PV-5', 'PV-7', 'PV-8']);
  });

  for (const vector of V0) {
    it(`${vector.id}: the stored pin still parses and its digest is over its stored content`, () => {
      const { digest, ...fields } = resolvedRunConfigSchema.parse(vector.stored);
      expect(pinDigest(fields)).to.equal(digest);
      expect(digest).to.equal((vector.expected as { digest: string }).digest);
      expect(Object.keys(fields.effectiveConfig.dimensions).filter((path) => path.startsWith('rollout.durable.'))).to.deep.equal([]);
    });
  }
});

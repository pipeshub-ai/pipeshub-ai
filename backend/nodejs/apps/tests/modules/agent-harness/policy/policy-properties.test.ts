/**
 * CPD3-12..CPD3-18 (POL-12..POL-15, NFR-47): the precedence properties of the
 * generic resolver, the fast-check twin of Python's contract suite.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { propertyRuns } from '../../../support/harness/property-profile';
import type { LayerName } from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import {
  orderOf,
  POLICY_REGISTRY,
  PolicyResolver,
  type ScopedLayer,
} from '../../../../src/modules/agent-harness/domain/policy';
import { POLICY_VECTORS } from '../support/policy-vectors';

const RUNS = propertyRuns(200);
const CHAIN: readonly LayerName[] = ['deployment', 'org', 'team', 'agent', 'trigger', 'parent', 'request', 'live'];
const LEVEL = fc.constantFrom('allow', 'ask', 'deny');

const values: Record<string, fc.Arbitrary<unknown>> = {
  min: fc.oneof(fc.constant(null), fc.integer({ min: 0, max: 50 }), fc.double({ min: 0, max: 50, noNaN: true })),
  subset: fc.oneof(fc.constant(null), fc.uniqueArray(fc.constantFrom('a', 'b', 'c', 'd', 'e'))),
  'min-map': fc.oneof(
    fc.constant(null),
    fc.dictionary(
      fc.constantFrom('default', 'coder', 'researcher'),
      fc.oneof(fc.integer({ min: 0, max: 50 }), fc.double({ min: 0, max: 50, noNaN: true })),
      { maxKeys: 3 },
    ),
  ),
  rank: fc.constantFrom(null, 'low', 'medium', 'high'),
  'rank-raise': fc.constantFrom(null, 'docker', 'gvisor', 'k8s', 'managed', 'microvm'),
  'bool-and': fc.boolean(),
  'bool-or': fc.boolean(),
  'flat-bottom': fc.constantFrom('none', 'allowlist:a', 'allowlist:b'),
  flat: fc.constantFrom(null, { a: 1 }, { a: 2 }, 'x'),
  tree: fc.constantFrom('build', 'plan', 'review'),
  gated: fc
    .tuple(fc.boolean(), fc.integer({ min: 1, max: 4 }), fc.integer({ min: 1, max: 4 }))
    .map(([on, d, c]) =>
      on ? { enabled: true, maxDepth: d, maxConcurrent: c } : { enabled: false, maxDepth: 1, maxConcurrent: 1 },
    ),
  'approval-rules': fc.record(
    {
      writes: LEVEL,
      classes: fc.record({ read: LEVEL, workspace: LEVEL }, { requiredKeys: [] }),
      tools: fc.dictionary(fc.constantFrom('t1', 't2', 't3'), LEVEL, { maxKeys: 3 }),
      flow: fc.record({ level: fc.constantFrom('off', 'ask', 'deny'), above: fc.constantFrom('public', 'private') }),
    },
    { requiredKeys: [] },
  ),
  'rank-raise-rules': fc.oneof(
    fc.constant(null),
    fc.dictionary(
      fc.constantFrom('install:*', 'install:mcp', 'install:skill', 'publish:*', 'publish:skill'),
      fc.constantFrom('none', 'owner', 'teamAdmin', 'orgAdmin'),
      { maxKeys: 4 },
    ),
  ),
};

const layerValues = (kind: LayerName): fc.Arbitrary<Record<string, unknown>> =>
  fc.record(
    {
      models: fc.record({ routes: fc.uniqueArray(fc.constantFrom('a', 'b', 'c', 'd'), { maxLength: 4 }) }, { requiredKeys: [] }),
      engineProfile: fc.record(
        {
          limits: fc.record(
            { maxSteps: fc.integer({ min: 1, max: 80 }), maxWallS: fc.integer({ min: 60, max: 4000 }) },
            { requiredKeys: [] },
          ),
          skills: fc.uniqueArray(fc.constantFrom('pdf', 'xlsx', 'docx'), { maxLength: 3 }),
          reasoningEffort: fc.constantFrom('low', 'medium', 'high'),
          isolationMin: fc.constantFrom('docker', 'gvisor', 'k8s', 'microvm'),
        },
        { requiredKeys: [] },
      ),
      approvals: values['approval-rules']!,
      ...(kind === 'deployment' || kind === 'org'
        ? { rollout: fc.record({ ENABLE_SKILLS: fc.boolean(), AGENT_HARNESS_KILL_SWITCH: fc.boolean() }, { requiredKeys: [] }) }
        : {}),
    },
    { requiredKeys: [] },
  );

const chain = (maxLength = 5): fc.Arbitrary<ScopedLayer[]> =>
  fc
    .uniqueArray(fc.constantFrom(...CHAIN), { minLength: 1, maxLength })
    .chain((kinds) => {
      const sorted = [...kinds].sort((a, b) => CHAIN.indexOf(a) - CHAIN.indexOf(b));
      return fc.tuple(...sorted.map((kind) => layerValues(kind).map((v): ScopedLayer => ({ kind, values: v }))));
    });

const ceilings = POLICY_REGISTRY.all().filter((d) => d.kind === 'ceiling' && d.order !== null && d.order.kind !== 'admits');
const valueAt = (values: unknown, path: string): unknown =>
  path.split('.').reduce<unknown>(
    (node, key) => (typeof node === 'object' && node !== null ? (node as Record<string, unknown>)[key] : undefined),
    values,
  );
const dims = (config: unknown): Record<string, { value: unknown }> =>
  (config as { dimensions: Record<string, { value: unknown }> }).dimensions;

describe('CPD-3 policy resolver: properties', () => {
  const resolver = new PolicyResolver();

  for (const entry of POLICY_VECTORS.orders) {
    it(`CPD3-12 ${entry.id}: meet is idempotent, commutative, associative and below both`, () => {
      const order = orderOf(entry.order);
      const meet = (a: unknown, b: unknown): unknown =>
        a === undefined || b === undefined ? undefined : order.meet(a, b);
      fc.assert(
        fc.property(values[entry.id]!, values[entry.id]!, values[entry.id]!, (x, y, z) => {
          const [a, b, c] = [order.lift(x), order.lift(y), order.lift(z)];
          expect(order.meet(a, a)).to.deep.equal(a);
          expect(order.meet(a, b)).to.deep.equal(order.meet(b, a));
          expect(meet(meet(a, b), c)).to.deep.equal(meet(a, meet(b, c)));
          const met = order.meet(a, b);
          if (met !== undefined) {
            expect(order.leq(met, a)).to.equal(null);
            expect(order.leq(met, b)).to.equal(null);
          }
        }),
        RUNS,
      );
    });
  }

  it('CPD3-13 every ceiling resolves at or below each layer that sets it', () => {
    fc.assert(
      fc.property(chain(), (layers) => {
        const resolved = resolver.resolve(layers);
        if (resolved.config === null) return;
        for (const d of ceilings) {
          const effective = dims(resolved.config)[d.path];
          if (effective === undefined) continue;
          for (const layer of layers) {
            const raw = valueAt(layer.values, d.layerPath);
            if (raw !== undefined) expect(d.order!.leq(effective.value, d.order!.lift(raw)), d.path).to.equal(null);
          }
        }
      }),
      RUNS,
    );
  });

  it('CPD3-13 inserting a layer never widens a dimension', () => {
    fc.assert(
      fc.property(chain(), chain(1), fc.nat(), (layers, [extra], at) => {
        const position = at % (layers.length + 1);
        const before = resolver.resolve(layers);
        const after = resolver.resolve([...layers.slice(0, position), extra!, ...layers.slice(position)]);
        if (before.config === null || after.config === null) return;
        for (const d of ceilings) {
          const old = dims(before.config)[d.path];
          const now = dims(after.config)[d.path];
          if (old !== undefined && now !== undefined) expect(d.order!.leq(now.value, old.value), d.path).to.equal(null);
        }
      }),
      RUNS,
    );
  });

  it('CPD3-15 the refusal names the first widening layer', () => {
    fc.assert(
      fc.property(chain(), (layers) => {
        const resolved = resolver.resolve(layers);
        if (resolved.refusal === null) return;
        const index = layers.findIndex((l) => l.kind === resolved.refusal!.layer);
        expect(resolver.resolve(layers.slice(0, index)).refusal).to.equal(null);
        expect(resolver.assertNarrows(layers[index]!, layers.slice(0, index))).to.deep.equal(resolved.refusal);
      }),
      RUNS,
    );
  });

  it('CPD3-16 resolveFrom(foldPrefix(a), b) equals resolve(a ++ b)', () => {
    fc.assert(
      fc.property(chain(6), fc.nat(), (layers, at) => {
        const split = at % (layers.length + 1);
        const whole = resolver.resolve(layers);
        const prefix = resolver.foldPrefix(layers.slice(0, split));
        if (prefix.refusal !== null) {
          expect(whole.refusal).to.deep.equal(prefix.refusal);
          return;
        }
        expect(resolver.resolveFrom(prefix.prefix, layers.slice(split))).to.deep.equal(whole);
      }),
      RUNS,
    );
  });

  it('CPD3-18 a live layer never widens the pin', () => {
    fc.assert(
      fc.property(chain(), layerValues('live'), (layers, live) => {
        const pin = resolver.foldPrefix(layers);
        if (pin.refusal !== null) return;
        const pinned = resolver.resolveFrom(pin.prefix, []);
        const narrowed = resolver.resolveFrom(pin.prefix, [{ kind: 'live', values: live }]);
        if (pinned.config === null || narrowed.config === null) return;
        for (const d of ceilings) {
          const old = dims(pinned.config)[d.path];
          if (old !== undefined) expect(d.order!.leq(dims(narrowed.config)[d.path]!.value, old.value), d.path).to.equal(null);
        }
      }),
      RUNS,
    );
  });

  it('CPD3-14 a resolved choice lies within its ceiling, else the chooser is refused', () => {
    fc.assert(
      fc.property(
        fc.uniqueArray(fc.constantFrom('a', 'b', 'c', 'd'), { minLength: 1 }),
        fc.constantFrom('a', 'b', 'c', 'd'),
        fc.constantFrom<LayerName>('org', 'agent', 'user', 'request'),
        (routes, primary, chooser) => {
          const resolved = resolver.resolve([
            { kind: 'org', values: { models: { routes } } },
            { kind: chooser, values: { models: { primary } } },
          ]);
          if (routes.includes(primary)) {
            expect(dims(resolved.config)['models.primary']).to.deep.equal({ value: primary, setBy: chooser, ceilings: [] });
          } else {
            expect(resolved.refusal).to.include({ code: 'outside_ceiling', dimension: 'models.primary', layer: chooser });
          }
        },
      ),
      RUNS,
    );
  });
});

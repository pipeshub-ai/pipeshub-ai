/**
 * QA2-1..QA2-6, QA2-9, QA2-10: a lower layer may only make things stricter, for
 * every row of the dimension table in every layer that may set it
 * (`contracts/harness/vectors/stricter-only.json`, shared with Python).
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { propertyRuns } from '../../../support/harness/property-profile';
import { LAYERS, type LayerName } from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import {
  POLICY_REGISTRY,
  PolicyResolver,
  type Dimension,
  type Resolved,
  type ScopedLayer,
} from '../../../../src/modules/agent-harness/domain/policy';
import { put, STRICTER_ONLY, type Ladder } from '../support/stricter-only-vectors';

const RUNS = propertyRuns(200);
const { ladders: LADDERS, choices: CHOICES } = STRICTER_ONLY;
const resolver = new PolicyResolver();

const exposed = (dimension: Dimension): LayerName[] => LAYERS.filter((layer) => dimension.exposure.has(layer));
const layer = (kind: LayerName, dimension: Dimension, value: unknown): ScopedLayer => ({
  kind,
  values: put({}, dimension.layerPath, value),
});
const entry = (resolved: Resolved, path: string): { value: unknown; setBy: string; ceilings: unknown[] } => {
  expect(resolved.refusal).to.equal(null);
  return (resolved.config as { dimensions: Record<string, { value: unknown; setBy: string; ceilings: unknown[] }> })
    .dimensions[path]!;
};
const refusal = (resolved: Resolved): unknown =>
  resolved.refusal && {
    code: resolved.refusal.code,
    dimension: resolved.refusal.dimension,
    layer: resolved.refusal.layer,
  };
const sample = (dimension: Dimension): unknown => {
  if (dimension.kind === 'operator') return STRICTER_ONLY.operatorSample;
  return dimension.kind === 'ceiling'
    ? LADDERS.find((ladder) => ladder.path === dimension.path)!.top
    : CHOICES.find((choice) => choice.path === dimension.path)!.inside;
};
const kept = (ladder: Ladder): unknown => (ladder.kept === undefined ? ladder.narrow : ladder.kept);

describe('QA-2 stricter only: every row of the dimension table, in every layer', () => {
  it('QA2-1 every ceiling row has a ladder and every choice row a sample', () => {
    const rows = POLICY_REGISTRY.all();
    const paths = (kind: Dimension['kind']): string[] => rows.filter((d) => d.kind === kind).map((d) => d.path).sort();
    expect(LADDERS.map((ladder) => ladder.path).sort()).to.deep.equal(paths('ceiling'));
    expect(CHOICES.map((choice) => choice.path).sort()).to.deep.equal(paths('choice'));
  });

  for (const ladder of LADDERS) {
    const dimension = POLICY_REGISTRY.get(ladder.path);
    const [upper, ...lowers] = exposed(dimension) as [LayerName, ...LayerName[]];
    const base = ladder.base ?? [];
    for (const lower of lowers) {
      it(`QA2-2 ${ladder.path}: a narrowing by the ${lower} layer is kept`, () => {
        const resolved = resolver.resolve([
          ...base,
          layer(upper, dimension, ladder.top),
          layer(lower, dimension, ladder.narrow),
        ]);
        const effective = entry(resolved, dimension.path);
        expect(effective.value).to.deep.equal(kept(ladder));
        expect(effective.setBy).to.equal(ladder.setBy ?? lower);
        expect(effective.ceilings.slice(-2)).to.deep.equal([
          { layer: upper, value: ladder.top },
          { layer: lower, value: ladder.narrow },
        ]);
      });

      it(`QA2-3 ${ladder.path}: a widening by the ${lower} layer is refused or met`, () => {
        const resolved = resolver.resolve([
          ...base,
          layer(upper, dimension, ladder.narrow),
          layer(lower, dimension, ladder.widen),
        ]);
        if (dimension.widening === 'meet') {
          expect(entry(resolved, dimension.path).value).to.deep.equal(kept(ladder));
          return;
        }
        expect(refusal(resolved)).to.deep.equal({
          code: 'widening',
          dimension: ladder.at ?? dimension.path,
          layer: lower,
        });
      });
    }
  }

  for (const dimension of POLICY_REGISTRY.all()) {
    for (const outside of LAYERS.slice(1).filter((name) => !dimension.exposure.has(name))) {
      it(`QA2-4 ${dimension.path}: the ${outside} layer may not set it`, () => {
        expect(refusal(resolver.resolve([layer(outside, dimension, sample(dimension))]))).to.deep.equal({
          code: 'not_exposed',
          dimension: dimension.path,
          layer: outside,
        });
      });
    }
  }

  for (const choice of CHOICES) {
    const dimension = POLICY_REGISTRY.get(choice.path);
    const ceiling = POLICY_REGISTRY.get(dimension.within!);
    const top = exposed(ceiling)[0]!;
    const bottom = exposed(ceiling).at(-1)!;
    for (const chooser of exposed(dimension)) {
      it(`QA2-5 ${choice.path}: a choice by the ${chooser} layer lies within its ceiling`, () => {
        expect(LAYERS.indexOf(top)).to.be.at.most(LAYERS.indexOf(chooser));
        expect(LAYERS.indexOf(chooser)).to.be.below(LAYERS.indexOf(bottom));
        const outside = { code: 'outside_ceiling', dimension: dimension.path, layer: chooser };
        const resolve = (chosen: unknown, ...below: ScopedLayer[]): Resolved => {
          const ceilingLayer = put({}, ceiling.layerPath, choice.ceiling);
          return chooser === top
            ? resolver.resolve([{ kind: top, values: put(ceilingLayer, dimension.layerPath, chosen) }, ...below])
            : resolver.resolve([{ kind: top, values: ceilingLayer }, layer(chooser, dimension, chosen), ...below]);
        };
        expect(entry(resolve(choice.inside), dimension.path)).to.deep.equal({
          value: choice.inside,
          setBy: chooser,
          ceilings: [],
        });
        expect(refusal(resolve(choice.outside))).to.deep.equal(outside);
        expect(refusal(resolve(choice.inside, layer(bottom, ceiling, choice.narrowed)))).to.deep.equal(outside);
      });
    }
  }

  for (const vector of STRICTER_ONLY.cases) {
    it(`${vector.id} resolves as the vectors say`, () => {
      const resolved = resolver.resolve(vector.layers);
      if ('refusal' in vector.expect) {
        expect(refusal(resolved)).to.deep.equal(vector.expect.refusal);
      } else {
        expect(resolved.refusal).to.equal(null);
        expect(resolved.config).to.deep.equal(vector.expect.config);
        expect(resolved.digest).to.equal(vector.expect.digest);
      }
    });
  }

  // An admitted item (the engine) is chosen outside its order, as in CPD3-13: QA2-2 and QA2-3 hold it.
  const pools = new Map(
    LADDERS.filter((ladder) => POLICY_REGISTRY.get(ladder.path).order!.kind !== 'admits').map(
      (ladder) => [ladder.path, [ladder.top, ladder.narrow, ladder.widen]] as const,
    ),
  );
  const chainLayers = LAYERS.filter((name) => [...pools.keys()].some((p) => POLICY_REGISTRY.get(p).exposure.has(name)));
  const layerValues = (kind: LayerName, paths: string[]): fc.Arbitrary<ScopedLayer> => {
    const settable = paths.filter((path) => POLICY_REGISTRY.get(path).exposure.has(kind));
    return fc
      .tuple(...settable.map((path) => fc.option(fc.constantFrom(...pools.get(path)!), { nil: undefined })))
      .map((drawn) => {
        const values: Record<string, unknown> = {};
        drawn.forEach((value, index) => {
          if (value !== undefined) put(values, POLICY_REGISTRY.get(settable[index]!).layerPath, value);
        });
        return { kind, values };
      });
  };
  /** A chain over a few rows (so layers meet on the same ones) and one more layer, of a kind not in it. */
  const chains = fc
    .tuple(
      fc.uniqueArray(fc.constantFrom(...pools.keys()), { minLength: 1, maxLength: 4 }),
      fc.uniqueArray(fc.constantFrom(...chainLayers), { minLength: 2, maxLength: 6 }),
      fc.nat(),
    )
    .chain(([paths, kinds, at]) =>
      fc
        .tuple(
          ...[...kinds].sort((a, b) => LAYERS.indexOf(a) - LAYERS.indexOf(b)).map((kind) => layerValues(kind, paths)),
        )
        .map((layers) => {
          const extra = at % layers.length;
          return { chain: layers.filter((_, index) => index !== extra), extra: layers[extra]! };
        }),
    );
  const setBy = (scoped: ScopedLayer): Array<[string, unknown]> =>
    [...pools.keys()].flatMap((path): Array<[string, unknown]> => {
      const raw = POLICY_REGISTRY.get(path)
        .layerPath.split('.')
        .reduce<unknown>(
          (node, key) => (typeof node === 'object' && node !== null ? (node as Record<string, unknown>)[key] : undefined),
          scoped.values,
        );
      return raw === undefined ? [] : [[path, raw]];
    });

  it('QA2-6 every resolved row is at or below each layer that sets it; a refusal names the first widening layer', () => {
    fc.assert(
      fc.property(chains, ({ chain }) => {
        const resolved = resolver.resolve(chain);
        if (resolved.refusal !== null) {
          const refused = resolved.refusal;
          const index = chain.findIndex((scoped) => scoped.kind === refused.layer);
          expect(refused.code).to.equal('widening');
          expect(setBy(chain[index]!).some(([path]) => refused.dimension.startsWith(path))).to.equal(true);
          expect(resolver.resolve(chain.slice(0, index)).refusal).to.equal(null);
          return;
        }
        for (const scoped of chain) {
          for (const [path, raw] of setBy(scoped)) {
            const order = POLICY_REGISTRY.get(path).order!;
            expect(order.leq(entry(resolved, path).value, order.lift(raw)), `${path} ${scoped.kind}`).to.equal(null);
          }
        }
      }),
      RUNS,
    );
  });

  it('QA2-6 inserting a layer never widens a row', () => {
    fc.assert(
      fc.property(chains, ({ chain, extra }) => {
        const longer = [...chain, extra].sort((a, b) => LAYERS.indexOf(a.kind) - LAYERS.indexOf(b.kind));
        const before = resolver.resolve(chain);
        const after = resolver.resolve(longer);
        if (before.refusal !== null || after.refusal !== null) return;
        for (const path of Object.keys((before.config as { dimensions: object }).dimensions)) {
          const order = POLICY_REGISTRY.get(path).order!;
          expect(order.leq(entry(after, path).value, entry(before, path).value), path).to.equal(null);
        }
      }),
      RUNS,
    );
  });
});

/**
 * C4.02 (LK-1..LK-5): any dimension can be locked at a layer, and no layer
 * below it can override the locked value. A locked choice is refused at every
 * lower layer that picks another value; a locked ceiling is refused at every
 * lower layer that widens it (kept at the stricter value where the row meets)
 * and may still be narrowed. Python's `test_c4_02_locks.py` holds the same rows
 * on the same vectors (`contracts/harness/vectors/stricter-only.json`).
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { propertyRuns } from '../../../support/harness/property-profile';
import { canonicalDigest } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { LAYERS, type LayerName } from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import {
  POLICY_REGISTRY,
  PolicyResolver,
  type Dimension,
  type Resolved,
  type ScopedLayer,
} from '../../../../src/modules/agent-harness/domain/policy';
import { put, STRICTER_ONLY } from '../support/stricter-only-vectors';

const RUNS = propertyRuns(200);
const LADDERS = STRICTER_ONLY.ladders.filter((ladder) => ladder.base === undefined);
const CHOICES = STRICTER_ONLY.choices;
const resolver = new PolicyResolver();

const exposed = (dimension: Dimension): LayerName[] => LAYERS.filter((layer) => dimension.exposure.has(layer));
const section = (path: string): string => path.split('.')[0]!;
const entry = (resolved: Resolved, path: string): Record<string, unknown> => {
  expect(resolved.refusal).to.equal(null);
  return (resolved.config as { dimensions: Record<string, Record<string, unknown>> }).dimensions[path]!;
};
const refusal = (resolved: Resolved): unknown =>
  resolved.refusal && {
    code: resolved.refusal.code,
    dimension: resolved.refusal.dimension,
    layer: resolved.refusal.layer,
  };

describe('C4.02 locks: a locked value is not overridable at any lower layer', () => {
  it('LK-0 every lockable row is covered (the admitted engine by the PR vectors)', () => {
    const covered = new Set([
      ...LADDERS.filter((l) => exposed(POLICY_REGISTRY.get(l.path)).length > 1).map((l) => l.path),
      ...CHOICES.filter((c) => exposed(POLICY_REGISTRY.get(c.path)).length > 1).map((c) => c.path),
    ]);
    const lockable = POLICY_REGISTRY.all()
      .filter((d) => d.kind !== 'operator' && exposed(d).length > 1)
      .map((d) => d.path);
    expect(lockable.filter((path) => !covered.has(path))).to.deep.equal(['engineProfile.engine']);
  });

  for (const choice of CHOICES) {
    const dimension = POLICY_REGISTRY.get(choice.path);
    const ceiling = POLICY_REGISTRY.get(dimension.within!);
    const [owner, ...lowers] = exposed(dimension) as [LayerName, ...LayerName[]];
    for (const lower of lowers) {
      it(`LK-1 ${choice.path}: a choice locked by the ${owner} layer is not overridable by the ${lower} layer`, () => {
        expect(ceiling.exposure.has(owner)).to.equal(true);
        const top = put(put({ locks: [dimension.path] }, ceiling.layerPath, choice.ceiling), dimension.layerPath, choice.inside);
        const below = (value: unknown, extra: Record<string, unknown> = {}): Resolved =>
          resolver.resolve([
            { kind: owner, values: top },
            { kind: lower, values: put({ ...extra }, dimension.layerPath, value) },
          ]);
        expect(refusal(below(choice.other))).to.deep.equal({ code: 'locked', dimension: dimension.path, layer: lower });
        expect(entry(below(choice.inside), dimension.path)).to.deep.equal({
          value: choice.inside,
          setBy: owner,
          ceilings: [],
          lockedBy: owner,
        });
        expect(
          entry(below(choice.other, { lockedBy: { [section(dimension.path)]: owner } }), dimension.path),
        ).to.deep.equal({ value: choice.other, setBy: lower, ceilings: [], lockedBy: owner });
        const unset = resolver.resolve([
          { kind: owner, values: { locks: [dimension.path] } },
          { kind: lower, values: put({}, dimension.layerPath, choice.inside) },
        ]);
        expect(refusal(unset)).to.deep.equal({ code: 'locked', dimension: dimension.path, layer: lower });
      });
    }
  }

  for (const ladder of LADDERS) {
    const dimension = POLICY_REGISTRY.get(ladder.path);
    const [owner, ...lowers] = exposed(dimension) as [LayerName, ...LayerName[]];
    for (const lower of lowers) {
      it(`LK-2 ${ladder.path}: a ceiling locked by the ${owner} layer is never widened by the ${lower} layer`, () => {
        const chain = (upper: unknown, value: unknown): Resolved =>
          resolver.resolve([
            { kind: owner, values: put({ locks: [dimension.path] }, dimension.layerPath, upper) },
            { kind: lower, values: put({}, dimension.layerPath, value) },
          ]);
        const narrowed = entry(chain(ladder.top, ladder.narrow), dimension.path);
        expect(narrowed['value']).to.deep.equal(ladder.kept === undefined ? ladder.narrow : ladder.kept);
        expect(narrowed['lockedBy']).to.equal(owner);
        const widened = chain(ladder.narrow, ladder.widen);
        if (dimension.widening === 'meet') {
          expect(entry(widened, dimension.path)['value']).to.deep.equal(ladder.kept ?? ladder.narrow);
          return;
        }
        expect(refusal(widened)).to.deep.equal({ code: 'locked', dimension: ladder.at ?? dimension.path, layer: lower });
      });
    }
  }

  it('LK-3 operator rows are the deployment\'s alone', () => {
    for (const d of POLICY_REGISTRY.all().filter((row) => row.kind === 'operator')) {
      expect(exposed(d)).to.deep.equal(['deployment']);
    }
  });

  const pools = new Map<string, unknown[]>([
    ...LADDERS.map((l) => [l.path, [l.top, l.narrow, l.widen]] as [string, unknown[]]),
    ...CHOICES.map((c) => [c.path, [c.inside, c.other]] as [string, unknown[]]),
  ]);
  const ceilingOf = new Map(CHOICES.map((c) => [POLICY_REGISTRY.get(c.path).within!, c] as const));
  const chainKinds = LAYERS.filter((layer) => layer !== 'release' && layer !== 'live');
  const lockedChains = fc
    .tuple(
      fc.uniqueArray(fc.constantFrom(...pools.keys()), { minLength: 1, maxLength: 3 }),
      fc.uniqueArray(fc.constantFrom(...chainKinds), { minLength: 2, maxLength: 5 }),
    )
    .chain(([paths, drawnKinds]) => {
      const kinds = [...drawnKinds].sort((a, b) => LAYERS.indexOf(a) - LAYERS.indexOf(b));
      return fc
        .tuple(
          ...kinds.map((kind) => {
            const settable = paths.filter((path) => POLICY_REGISTRY.get(path).exposure.has(kind));
            return fc.record({
              set: fc.tuple(...settable.map((path) => fc.option(fc.constantFrom(...pools.get(path)!), { nil: undefined }))),
              locks: fc.subarray(settable),
              owner: fc.option(fc.tuple(fc.constantFrom(...paths), fc.constantFrom(...kinds)), { nil: undefined }),
            }).map(({ set, locks, owner }): ScopedLayer => {
              const values: Record<string, unknown> = {};
              set.forEach((value, index) => {
                if (value !== undefined) put(values, POLICY_REGISTRY.get(settable[index]!).layerPath, value);
              });
              if (locks.length > 0) values['locks'] = locks;
              if (owner !== undefined) values['lockedBy'] = { [section(owner[0])]: owner[1] };
              return { kind, values };
            });
          }),
        )
        .map((layers) => {
          const first = layers[0]!;
          for (const [ceilingPath, choice] of ceilingOf) {
            if (paths.includes(choice.path) && POLICY_REGISTRY.get(ceilingPath).exposure.has(first.kind)) {
              put(first.values as Record<string, unknown>, POLICY_REGISTRY.get(ceilingPath).layerPath, choice.ceiling);
            }
          }
          return layers;
        });
    });
  const carries = (layer: ScopedLayer, path: string, lock: LayerName): boolean => {
    const owner = ((layer.values as { lockedBy?: Record<string, LayerName> }).lockedBy ?? {})[section(path)];
    return owner !== undefined && LAYERS.indexOf(owner) <= LAYERS.indexOf(lock);
  };
  const locksOf = (layer: ScopedLayer): string[] => (layer.values as { locks?: string[] }).locks ?? [];

  it('LK-4 a locked value is never overridden by a layer below without its authority', () => {
    fc.assert(
      fc.property(lockedChains, (chain) => {
        const folded = resolver.foldPrefix(chain);
        if (folded.refusal !== null) {
          const refused = folded.refusal;
          expect(['locked', 'widening', 'outside_ceiling']).to.include(refused.code);
          if (refused.code === 'widening') {
            // A widening of a locked row is named as the lock, unless the layer holds the lock's authority.
            const index = chain.findIndex((l) => l.kind === refused.layer);
            const above = resolver.foldPrefix(chain.slice(0, index)).prefix!;
            for (const [path, lock] of above.locks) {
              if (refused.dimension.startsWith(path)) expect(carries(chain[index]!, path, lock)).to.equal(true);
            }
          }
          return;
        }
        for (const [path, lock] of folded.prefix.locks) {
          const index = chain.findIndex((layer) => locksOf(layer).includes(path));
          expect(chain[index]!.kind).to.equal(lock);
          const held = resolver.foldPrefix(chain.slice(0, index + 1)).prefix!.entries.get(path);
          const final = folded.prefix.entries.get(path);
          const dimension = POLICY_REGISTRY.get(path);
          const authorised = chain.slice(index + 1).some((layer) => carries(layer, path, lock));
          if (dimension.order === null) {
            if (!authorised) {
              if (held?.value === undefined || final?.value === undefined) {
                expect(held?.value, path).to.equal(final?.value);
              } else {
                expect(canonicalDigest(final.value), path).to.equal(canonicalDigest(held.value));
              }
            }
          } else if (held?.value !== undefined) {
            expect(dimension.order.leq(final!.value, held.value), path).to.equal(null);
          }
        }
      }),
      RUNS,
    );
  });

  it('LK-5 a fold of a prefix keeps its locks (CPD3-16 with locks)', () => {
    fc.assert(
      fc.property(lockedChains, fc.nat(4), (chain, at) => {
        const cut = Math.min(at, chain.length);
        const prefix = resolver.foldPrefix(chain.slice(0, cut));
        if (prefix.refusal !== null) return;
        const whole = resolver.resolve(chain);
        const split = resolver.resolveFrom(prefix.prefix, chain.slice(cut));
        expect(split.digest).to.equal(whole.digest);
        expect(refusal(split)).to.deep.equal(refusal(whole));
      }),
      RUNS,
    );
  });
});

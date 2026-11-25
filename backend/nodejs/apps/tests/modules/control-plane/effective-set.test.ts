/**
 * CPD-7: the domain effective-set resolver. ESR-1..6 (CP-SKEL's contract) on
 * the production adapter; ESR-7 (`tools.effects`), ESR-9 / PIN-19 (required
 * items), ESR-10 (the agent owner's share) and ESR-8 / PIN-18 (the shared
 * vectors). `WRITE_EFFECTIVE_SET_VECTORS=1` rewrites the vectors' expected
 * outputs from the resolver; review the diff before committing it.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { CanonicalEffectiveSetResolver } from '../../../src/modules/control-plane/adapters/effective-set-resolver';
import { isProblem } from '../../../src/modules/control-plane/domain/problem';
import { effectiveSetSchema, type EffectiveSet } from '../../../src/modules/control-plane/domain/objects';
import { canonicalDigest } from '../../../src/libs/utils/canonical-json';
import { describeEffectiveSetResolverContract } from './support/effective-set-contract';
import {
  readVectors,
  vectorConfig,
  vectorSnapshot,
  vectorSubject,
  VECTOR_INPUTS,
  writeVectors,
  type Vector,
  type VectorInput,
} from './support/effective-set-vectors';
import { aBinding, instId } from './support/cp-objects';

const resolver = new CanonicalEffectiveSetResolver();

function expectedOf(input: VectorInput): Vector['expected'] {
  const result = resolver.resolve(vectorSubject(input), vectorConfig(input), vectorSnapshot(input));
  if (isProblem(result)) {
    const installation = (result.subject as { installation?: string } | null | undefined)?.installation ?? null;
    return { refusal: result.code, installation };
  }
  return { set: result };
}

const byId = (id: string): VectorInput => VECTOR_INPUTS.find((input) => input.id === id)!;
const setOf = (id: string): EffectiveSet => {
  const expected = expectedOf(byId(id));
  expect('set' in expected, JSON.stringify(expected)).to.equal(true);
  return (expected as { set: EffectiveSet }).set;
};
const reasons = (set: EffectiveSet) => set.items.map((item) => [item.installation, item.state, item.reason]);

if (process.env.WRITE_EFFECTIVE_SET_VECTORS === '1') {
  writeVectors(VECTOR_INPUTS.map((input) => ({ ...input, expected: expectedOf(input) })));
}

describeEffectiveSetResolverContract('CanonicalEffectiveSetResolver', () => new CanonicalEffectiveSetResolver());

describe('CPD-7 effective set', () => {
  it('ESR-7: tools.effects keeps only tools inside the ceiling and drops an unclassified one; an emptied item is not_granted', () => {
    const set = setOf('ES-3');
    expect(set.items[0]!.tools.map((tool) => tool.toolId)).to.deep.equal([`mcp@${instId(1)}/list_issues`]);
    expect(reasons(set)[1]).to.deep.equal([instId(2), 'unavailable', 'not_granted']);
    const unbounded = { ...byId('ES-3'), dimensions: {} };
    const all = expectedOf(unbounded) as { set: EffectiveSet };
    expect(all.set.items[0]!.tools).to.have.length(3);
  });

  it('ESR-9 / PIN-19: a required item without a credential refuses naming it; not required, it is listed and the rest is ready', () => {
    expect(expectedOf(byId('ES-4'))).to.deep.equal({ refusal: 'CP_CREDENTIAL_MISSING', installation: instId(1) });
    expect(reasons(setOf('ES-5'))).to.deep.equal([
      [instId(1), 'unavailable', 'credential_missing'],
      [instId(2), 'ready', null],
    ]);
  });

  it("ESR-9: an agent-credential item is ready on the agent's own binding, and a user's binding does not stand in for it", () => {
    const base = byId('ES-5');
    const withMode = (owner: string): VectorInput => ({
      ...base,
      installations: base.installations.map((inst) => (inst.id === instId(1) ? { ...inst, credentialMode: 'agent' as const } : inst)),
      bindings: [aBinding({ installation: instId(1), owner: owner as never })],
    });
    const state = (input: VectorInput) => reasons((expectedOf(input) as { set: EffectiveSet }).set)[0];
    expect(state(withMode(`agent:${base.subject.agentKey!}`))).to.deep.equal([instId(1), 'ready', null]);
    expect(state(withMode(`user:${base.subject.actorId}`))).to.deep.equal([instId(1), 'unavailable', 'credential_missing']);
  });

  it("ESR-10: the agent owner's items are shared with whoever runs the agent, nobody else's", () => {
    const shared = setOf('ES-6').items[0]!;
    expect([shared.state, shared.provenance.visibleVia]).to.deep.equal(['ready', 'owner']);
    expect(reasons(setOf('ES-7'))).to.deep.equal([[instId(1), 'unavailable', 'not_visible']]);
    const assistantRun = { ...byId('ES-6'), subject: { actorId: 'u3', agentKey: null }, grants: [{ ...byId('ES-6').grants[0]!, subject: 'org' as const }] };
    expect(reasons((expectedOf(assistantRun) as { set: EffectiveSet }).set)).to.deep.equal([[instId(1), 'unavailable', 'not_visible']]);
  });

  it('ESR-10: sharing never makes visible an item that only the agent grant (not its owner) names, for random owners', () => {
    fc.assert(
      fc.property(fc.constantFrom('u1', 'u3', 'u9'), fc.constantFrom('u1', 'u9', null), (owner, agentOwner) => {
        const input = { ...byId('ES-7'), agentOwner, installations: [{ ...byId('ES-7').installations[0]!, owner: `user:${owner}` as const }] };
        const item = (expectedOf(input) as { set: EffectiveSet }).set.items[0]!;
        expect(item.state === 'ready').to.equal(owner === 'u3' || owner === agentOwner);
      }),
      { numRuns: 50, seed: 20261012 },
    );
  });

  describe('ESR-8 / PIN-18: the shared vectors', () => {
    const vectors = readVectors();

    it('holds every input case, and nothing else', () => {
      expect(vectors.map((vector) => vector.id)).to.deep.equal(VECTOR_INPUTS.map((input) => input.id));
    });

    for (const input of VECTOR_INPUTS) {
      it(`${input.id}: ${input.description}`, () => {
        const vector = vectors.find((candidate) => candidate.id === input.id)!;
        expect(vector, 'the file holds the case').to.not.equal(undefined);
        const { expected, ...stored } = vector;
        expect(stored).to.deep.equal(JSON.parse(JSON.stringify(input)));
        expect(expectedOf(input)).to.deep.equal(expected);
        if ('set' in expected) {
          expect(effectiveSetSchema.safeParse(expected.set).success, 'the generated schema accepts it').to.equal(true);
          expect(expected.set.digest).to.equal(canonicalDigest(expected.set.items));
        }
      });
    }
  });
});

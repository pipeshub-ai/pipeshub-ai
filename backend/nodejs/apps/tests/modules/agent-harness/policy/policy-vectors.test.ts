/**
 * CPD3-10, CPD3-11, CPD3-2, CPD3-19: the generic policy resolver on the shared
 * vectors (`contracts/harness/vectors/policy-resolve.json`), which Python's
 * `harness_core.policy` resolves to the same answers.
 */
import { expect } from 'chai';
import { canonicalDigest } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import {
  DimensionRegistry,
  InvalidDimensionTable,
  orderOf,
  PolicyResolver,
} from '../../../../src/modules/agent-harness/domain/policy';
import { POLICY_VECTORS } from '../support/policy-vectors';

const reversedMembers = (node: unknown): unknown =>
  typeof node === 'object' && node !== null && !Array.isArray(node)
    ? Object.fromEntries(Object.entries(node).reverse().map(([k, v]) => [k, reversedMembers(v)]))
    : node;

describe('CPD-3 policy resolver: shared vectors', () => {
  const resolver = new PolicyResolver();

  for (const vector of POLICY_VECTORS.cases) {
    it(`CPD3-10 ${vector.id} resolves as the vectors say`, () => {
      const resolved = resolver.resolve(vector.layers);
      if ('refusal' in vector.expect) {
        expect(resolved.refusal && {
          code: resolved.refusal.code,
          dimension: resolved.refusal.dimension,
          layer: resolved.refusal.layer,
        }).to.deep.equal(vector.expect.refusal);
      } else {
        expect(resolved.refusal).to.equal(null);
        expect(resolved.config).to.deep.equal(vector.expect.config);
        expect(resolved.digest).to.equal(vector.expect.digest);
        expect(canonicalDigest(vector.expect.config)).to.equal(vector.expect.digest);
      }
    });

    if ('config' in vector.expect) {
      const expected = vector.expect;
      it(`CPD3-19 ${vector.id}: the digest does not depend on member order`, () => {
        const layers = vector.layers.map((l) => ({ kind: l.kind, values: reversedMembers(l.values) }));
        expect(resolver.resolve(layers).digest).to.equal(expected.digest);
      });
    }
  }

  for (const entry of POLICY_VECTORS.orders) {
    it(`CPD3-11 the ${entry.id} order compares and meets as the vectors say`, () => {
      const order = orderOf(entry.order);
      for (const c of entry.cases) {
        const widened = order.leq(c.a, c.b);
        expect(widened === null, JSON.stringify(c)).to.equal(c.leq);
        if (widened !== null) expect(widened.at).to.equal(c.at);
        const met = order.meet(c.a, c.b);
        if (c.meetUndefined === true) expect(met, JSON.stringify(c)).to.equal(undefined);
        else expect(met, JSON.stringify(c)).to.deep.equal(c.meet);
      }
    });
  }

  for (const table of POLICY_VECTORS.invalidTables) {
    it(`CPD3-2 ${table.id}: a table that breaks a rule is refused naming the row`, () => {
      expect(() => DimensionRegistry.fromTable({ schemaVersion: 1, dimensions: table.dimensions }))
        .to.throw(InvalidDimensionTable, table.names);
    });
  }

  it('CPD3-2 the release table loads', () => {
    expect(new PolicyResolver().resolve([]).config).to.deep.equal({ schemaVersion: 1, dimensions: {} });
  });
});

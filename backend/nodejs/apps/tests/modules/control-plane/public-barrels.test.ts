/**
 * The public barrels other modules import from (docs/control-plane/03-interfaces.md §1): the control plane's, the
 * policy domain's and the generated contracts'. A re-export caught in an import cycle reads as undefined at the
 * importer and only fails where it is first called; this reads every name each barrel exports, so a cycle or a
 * dropped re-export fails here. Renamed re-exports must be the very binding of their module.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import * as controlPlane from '../../../src/modules/control-plane';
import * as grantAdmin from '../../../src/modules/control-plane/application/grant-admin';
import * as generated from '../../../src/modules/control-plane/contracts/generated';
import * as policy from '../../../src/modules/agent-harness/domain/policy';
import * as orders from '../../../src/modules/agent-harness/domain/policy/orders';

const undefinedExports = (barrel: Record<string, unknown>): string[] =>
  Object.keys(barrel).filter((name) => barrel[name] === undefined);

describe('public barrels', () => {
  it('the control plane barrel resolves every name it exports, the renamed ones to their own binding', () => {
    expect(Object.keys(controlPlane).length).to.be.greaterThan(100);
    expect(undefinedExports(controlPlane)).to.deep.equal([]);
    expect(controlPlane.grantNarrows).to.equal(grantAdmin.narrows);
  });

  it('the generated contracts barrel exports only schemas that parse, and the control plane relays the same problem schema', () => {
    const names = Object.keys(generated);
    expect(undefinedExports(generated)).to.deep.equal([]);
    const schemas = names.filter((name) => name.endsWith('Schema'));
    expect(schemas.length).to.be.greaterThan(10);
    for (const name of schemas) {
      const schema = (
        generated as unknown as Record<string, { safeParse?: unknown }>
      )[name];
      expect(schema?.safeParse, name).to.be.a('function');
    }
    expect(controlPlane.problemSchema).to.equal(generated.problemSchema);
    expect(generated.problemSchema.safeParse({}).success).to.equal(false);
  });

  it("the policy domain barrel resolves every name, its orders the orders module's own", () => {
    expect(undefinedExports(policy)).to.deep.equal([]);
    expect(policy.orderOf).to.equal(orders.orderOf);
    expect(policy.InvalidPolicyValue).to.equal(orders.InvalidPolicyValue);
  });
});

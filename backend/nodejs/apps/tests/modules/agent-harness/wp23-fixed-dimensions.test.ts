/**
 * WP-23 (a setting the deployment fixed shows a lock): the policy section read names the dimensions the layers above fix
 * for the org, resolved by the one resolver.
 */
import { expect } from 'chai';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { fixedDimensions } from '../../../src/modules/agent-harness/domain/policy/fixed';

const resolve = (deployment: Record<string, unknown>) => {
  const resolved = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    { kind: 'deployment', values: { schemaVersion: 1, scope: { kind: 'deployment' }, ...deployment } },
  ]);
  expect(resolved.refusal).to.equal(null);
  return resolved.config!.dimensions;
};

describe('WP-23 dimensions the deployment fixed', () => {
  it("the release's cache TTL is a ceiling the org may shorten, never fixed; a deployment lock fixes it", () => {
    expect(fixedDimensions(resolve({}), 'snapshots')).to.deep.equal([]);
    expect(fixedDimensions(resolve({ snapshots: { ttlDays: 3 } }), 'snapshots')).to.deep.equal([]);
    expect(fixedDimensions(resolve({ snapshots: { ttlDays: 3 }, locks: ['snapshots.ttlDays'] }), 'snapshots')).to.deep.equal([
      'snapshots.ttlDays',
    ]);
  });
});

/**
 * SPENDCONT: the token cap is a runaway backstop and the USD cap decides.
 *
 * One Codex child read 3.47M prompt tokens, 2.88M of them cached, for USD 0.12; with its parent the tree committed
 * 1,001,452 tokens for USD 0.13 and was stopped by the 1M token cap with USD 4.87 of 5 left. The release now pins
 * 10M tokens for coding-engine work (an engine run, and a loop run that may hand off to one, whose tree pays for the
 * engine child) and 2M for a loop run that cannot hand off. A lower limit an admin sets still binds both.
 */
import { expect } from 'chai';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { pinnedBudgets, workTokens } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { legacyBudgets } from '../../../src/modules/agent-harness/domain/run-spec';

const resolve = (layers: Array<{ kind: 'org' | 'agent' | 'request'; values: object }>) => {
  const resolved = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    { kind: 'request', values: { budgets: { activeTimeS: 600 } } },
    ...layers,
  ]);
  expect(resolved.refusal).to.equal(null);
  return resolved.config!;
};

describe('SPENDCONT: token backstops', () => {
  it('the release pins 10M for engine work, 2M for the loop, and keeps USD 5', () => {
    expect(RELEASE_DEFAULTS.budgets.maxTokens).to.equal(10_000_000);
    expect(RELEASE_DEFAULTS.budgets.loopMaxTokens).to.equal(2_000_000);
    expect(RELEASE_DEFAULTS.budgets.maxCostUsd).to.equal(5);
  });

  it('a loop run that may hand off to an engine, and an engine run, get the engine backstop', () => {
    const config = resolve([]);
    expect(pinnedBudgets(config, 'loop', true).maxTokens).to.equal(10_000_000);
    expect(pinnedBudgets(config, 'engine').maxTokens).to.equal(10_000_000);
    expect(legacyBudgets(null, null, 600, true).maxTokens).to.equal(10_000_000);
  });

  it('a loop run that cannot hand off gets the loop backstop', () => {
    expect(pinnedBudgets(resolve([]), 'loop').maxTokens).to.equal(2_000_000);
    expect(legacyBudgets(null, null, 600).maxTokens).to.equal(2_000_000);
    expect(workTokens(10_000_000, false)).to.equal(2_000_000);
  });

  it('a lower limit an admin sets binds every kind of run', () => {
    const config = resolve([{ kind: 'org', values: { budgets: { maxTokens: 500_000 } } }]);
    expect(pinnedBudgets(config, 'loop', true).maxTokens).to.equal(500_000);
    expect(pinnedBudgets(config, 'engine').maxTokens).to.equal(500_000);
    expect(pinnedBudgets(config, 'loop').maxTokens).to.equal(500_000);
  });

  it("an engine profile's own token limit still narrows an engine run", () => {
    const config = resolve([{ kind: 'agent', values: { engineProfile: { limits: { maxTokens: 3_000_000 } } } }]);
    expect(pinnedBudgets(config, 'engine').maxTokens).to.equal(3_000_000);
  });
});

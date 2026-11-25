/**
 * A charged call's spend entry (domain/cost-ledger.ts): the period is the UTC month of the charge, an entry counts a
 * call once (a reconcile counts none), and a refusal from an extension point's shared budget keeps the ledger's code.
 */
import { expect } from 'chai';
import {
  costEntry,
  costEntryId,
  costPeriodOf,
  isBudgetScope,
  SharedBudgetReached,
  reconciledKey,
  reconcileEntry,
} from '../../../src/modules/agent-harness/domain/cost-ledger';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';

const usage = {
  inputTokens: 10,
  outputTokens: 5,
  cacheReadTokens: 2,
  cacheWriteTokens: 1,
};
const who = { sessionId: 's-1', userId: 'u-1', agentKey: 'agent-1' };

describe('cost ledger domain (spend entries)', () => {
  it('a period is the UTC month of an instant, across a year end', () => {
    expect(costPeriodOf(Date.UTC(2026, 10, 30, 23, 59, 59))).to.equal(
      '2026-11',
    );
    expect(costPeriodOf(Date.UTC(2026, 11, 31, 23, 59, 59, 999))).to.equal(
      '2026-12',
    );
    expect(costPeriodOf(Date.UTC(2027, 0, 1))).to.equal('2027-01');
  });

  it("an entry counts one call, carries the run's attribution, copies the usage and defaults the role to primary", () => {
    const at = Date.UTC(2026, 10, 9);
    const entry = costEntry(
      'org-1',
      'run-1',
      'k-1',
      { usdMicros: 1200, tokens: 18 },
      usage,
      who,
      at,
    );
    expect(entry).to.deep.equal({
      orgId: 'org-1',
      runId: 'run-1',
      key: 'k-1',
      sessionId: 's-1',
      userId: 'u-1',
      agentKey: 'agent-1',
      modelRole: 'primary',
      period: '2026-11',
      at,
      usdMicros: 1200,
      tokens: 18,
      usage,
      calls: 1,
      estimatedCalls: 0,
      folded: false,
    });
    expect(entry.usage).to.not.equal(usage);
    const estimated = costEntry(
      'org-1',
      'run-1',
      'k-2',
      { usdMicros: 1, tokens: 1 },
      usage,
      { ...who, modelRole: 'fast' as const },
      at,
      true,
    );
    expect(estimated.estimatedCalls).to.equal(1);
    expect(estimated.modelRole).to.equal('fast');
    expect(costEntryId('org-1', 'run-1', 'k-1')).to.equal('org-1:run-1:k-1');
  });

  it("a reconcile's entry is keyed apart from its call, counts no call and takes the estimate back", () => {
    const entry = reconcileEntry(
      'org-1',
      'run-1',
      'k-1',
      { usdMicros: -300, tokens: -4 },
      usage,
      who,
      0,
    );
    expect(reconciledKey('k-1')).to.equal('k-1#reconciled');
    expect(entry.key).to.equal('k-1#reconciled');
    expect([entry.calls, entry.estimatedCalls, entry.usdMicros]).to.deep.equal([
      0, -1, -300,
    ]);
  });

  it("a shared budget's refusal keeps the ledger's code, names its scope and carries the extension's words and reason", () => {
    const error = new SharedBudgetReached(
      'BUDGET_INSUFFICIENT',
      'pool-a',
      'Too little is left of the pool for the next step',
      'pool_a_spent',
    );
    expect(error).to.be.instanceOf(HarnessConflictError);
    expect(error.name).to.equal('SharedBudgetReached');
    expect([error.code, error.scope, error.reasonCode, error.message]).to.deep.equal([
      'BUDGET_INSUFFICIENT',
      'pool-a',
      'pool_a_spent',
      'Too little is left of the pool for the next step',
    ]);
  });

  it('a budget scope is opaque here: only its shape is checked', () => {
    for (const scope of ['pool-a', 'p', 'a_1']) expect(isBudgetScope(scope)).to.equal(true);
    for (const scope of ['', 'Pool', '1a', 'a'.repeat(33), 7, null]) expect(isBudgetScope(scope)).to.equal(false);
  });
});

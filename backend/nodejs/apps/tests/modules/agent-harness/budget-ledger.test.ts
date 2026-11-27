/**
 * P6.5: the ledger's pure rules and the in-memory store contract (NL-1, NL-2).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import {
  childCap,
  pinnedCap,
  reserveForChild,
  usdMicros,
} from '../../../src/modules/agent-harness/domain/budget-ledger';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { budgetChain } from '../../../src/modules/agent-harness/application/budget-ledger-service';
import { describeBudgetLedgerContract } from './support/budget-ledger-contract';

describe('P6.5 NS-1: lazy opening walks a run tree admitted before the ledger', () => {
  const vector = (
    JSON.parse(
      readFileSync(
        join(
          __dirname,
          '../../../../../../contracts/harness/vectors/pinned-run-spec.json',
        ),
        'utf-8',
      ),
    ) as { pinned: { spec: { budgets: Record<string, unknown> } } }
  ).pinned;
  const pin = (maxCostUsd: number, maxTokens: number) =>
    ({
      ...vector,
      spec: {
        ...vector.spec,
        budgets: { ...vector.spec.budgets, maxCostUsd, maxTokens },
      },
    }) as never;
  const run = (runId: string, parentRunId?: string) =>
    ({
      orgId: 'o',
      runId,
      ...(parentRunId
        ? { parent: { runId: parentRunId, invocationId: 'i', depth: 1 } }
        : {}),
    }) as never;

  it('opens the root from its pin and a child with its share of its parent', async () => {
    const runs = new Map<string, { run: never; pinned: never }>([
      ['root', { run: run('root'), pinned: pin(5, 1_000_000) }],
      ['child', { run: run('child', 'root'), pinned: pin(5, 250_000) }],
    ]);
    const chain = await budgetChain(
      { pinned: async (_org, id) => runs.get(id) ?? null },
      run('child', 'root'),
      pin(5, 250_000),
    );
    expect(chain).to.deep.equal([
      {
        runId: 'root',
        parentRunId: null,
        cap: { usdMicros: 5_000_000, tokens: 1_000_000 },
      },
      {
        runId: 'child',
        parentRunId: 'root',
        cap: { usdMicros: null, tokens: 250_000 },
      },
    ]);
    const ledger = new InMemoryBudgetLedger();
    await ledger.ensure('o', chain, { usdMicros: 10, tokens: 5 });
    expect((await ledger.remaining('o', 'root')).committed).to.deep.equal({
      usdMicros: 10,
      tokens: 5,
    });
  });

  it('a parent with no pin cannot be opened', async () => {
    const error: unknown = await budgetChain(
      { pinned: async () => null },
      run('child', 'gone'),
      pin(1, 1),
    ).catch((caught: unknown) => caught);
    expect(error)
      .to.be.instanceOf(HarnessConflictError)
      .with.property('code', 'BUDGET_ACCOUNT_MISSING');
  });
});

describeBudgetLedgerContract('in-memory', async () => {
  let now = Date.UTC(2026, 9, 3);
  return {
    ledger: new InMemoryBudgetLedger(() => now),
    advance: (seconds: number) => {
      now += seconds * 1000;
    },
  };
});

describe('P6.5 budget ledger rules', () => {
  it('usdMicros rounds as Python does', () => {
    expect([5, 2.0, 0.001, 0.1 + 0.2, 1e-7].map(usdMicros)).to.deep.equal([
      5_000_000, 2_000_000, 1_000, 300_000, 0,
    ]);
  });

  it('pinnedCap reads maxCostUsd and maxTokens', () => {
    expect(pinnedCap({ budgets: { maxTurns: 1, maxTokens: 9, maxCostUsd: 0.5, maxChildren: 0 } } as never)).to.deep.equal({
      usdMicros: 500_000,
      tokens: 9,
    });
  });

  it('childCap: a share only when smaller than the parent pin', () => {
    expect(childCap({ usdMicros: 5, tokens: 10 }, { usdMicros: 5, tokens: 10 })).to.deep.equal({ usdMicros: null, tokens: null });
    expect(childCap({ usdMicros: 5, tokens: 10 }, { usdMicros: 3, tokens: 12 })).to.deep.equal({ usdMicros: 3, tokens: null });
  });

  it('reserveForChild: a share fits what is left; an inheriting child needs something left', () => {
    expect(reserveForChild({ usdMicros: 500, tokens: null }, { usdMicros: 400, tokens: null })).to.deep.equal({ usdMicros: 400, tokens: null });
    for (const [left, share] of [
      [{ usdMicros: 500, tokens: null }, { usdMicros: 600, tokens: null }],
      [{ usdMicros: 0, tokens: 10 }, { usdMicros: null, tokens: null }],
    ] as const) {
      expect(() => reserveForChild(left, share)).to.throw(HarnessConflictError, /left/);
    }
  });
});

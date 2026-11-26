import { expect } from 'chai';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { MongoExecutionStore } from '../../../src/libs/repositories/agent-harness/execution/mongo';
import {
  ExecutionService,
  MAX_SWEEP_LIMIT,
} from '../../../src/modules/agent-harness/application/execution-service';
import { describeExecutionStoreContract } from './support/execution-store-contract';
import { describeChildRunContract } from './support/child-run-contract';
import { T_CLAIM_MS } from '../../../src/modules/agent-harness/domain/execution';
import { inMemoryBackend } from './support/in-memory-backend';

describeExecutionStoreContract('in-memory', (clock, options) =>
  inMemoryBackend(clock, options?.quotas !== undefined ? { quotas: options.quotas } : {}),
);

describeChildRunContract('in-memory', (clock) =>
  inMemoryBackend(clock, { budgets: new InMemoryBudgetLedger(() => clock.now()) }),
);

describe('one admission path', () => {
  it('has no ExecutionStore.submit on any adapter or the service', () => {
    for (const target of [
      InMemoryExecutionStore.prototype,
      MongoExecutionStore.prototype,
      ExecutionService.prototype,
    ]) {
      expect('submit' in target).to.equal(false);
    }
  });
});

describe('ExecutionService.sweep', () => {
  it('clamps the per-call bound', async () => {
    const limits: number[] = [];
    const promotions: Array<[number, number]> = [];
    const service = new ExecutionService({
      store: {
        sweep: async (limit: number) => {
          limits.push(limit);
          return 0;
        },
        promoteInline: async (tClaimMs: number, limit: number) => {
          promotions.push([tClaimMs, limit]);
          return 0;
        },
        unclaimedRuns: async () => [],
      } as never,
      journal: {} as never,
      journals: {} as never,
    });
    await service.sweep(10_000);
    await service.sweep(0);
    expect(limits).to.deep.equal([MAX_SWEEP_LIMIT, 1]);
    // Promotion gets what the expiry pass left of the same bound.
    expect(promotions).to.deep.equal([
      [T_CLAIM_MS, MAX_SWEEP_LIMIT],
      [T_CLAIM_MS, 1],
    ]);
  });
});

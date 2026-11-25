/**
 * Spend behind ports: what the budget ledger asks of budgets runs share at
 * each reservation and commit (`SharedBudgetGuard`), and what the retention sweep
 * and a person's erasure ask of the spend records (`CostEraser`). Unbound,
 * each default leaves the run's own budget as the only bound and keeps no
 * records.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { BudgetLedgerService } from '../../../src/modules/agent-harness/application/budget-ledger-service';
import type { BudgetAmount } from '../../../src/modules/agent-harness/domain/budget-ledger';
import { SharedBudgetReached } from '../../../src/modules/agent-harness/domain/cost-ledger';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  noSharedBudgets,
  noCostEraser,
  type SharedBudgetGuard,
} from '../../../src/modules/agent-harness/ports/cost-ledger';
import {
  sharedBudgetGuardOf,
  costEraserOf,
  runBudgetCeilingOf,
} from '../../../src/modules/agent-harness/binders/cost-ports';
import {
  AGENT_HARNESS_COST_ERASER,
  AGENT_HARNESS_SHARED_BUDGET_GUARD,
  AGENT_HARNESS_RUN_BUDGET_CEILING,
} from '../../../src/modules/agent-harness/binders/tokens';

const attribution = { sessionId: 's1', userId: 'u1', agentKey: null };
const amount = (usdMicros: number, tokens = 0): BudgetAmount => ({ usdMicros, tokens });

describe('shared budget and spend record ports', () => {
  describe('noSharedBudgets', () => {
    it('admits every reservation as asked, a large one and a zero one alike', async () => {
      expect(await noSharedBudgets.admit('o1', attribution, amount(9_000_000_000, 7), amount(1))).to.deep.equal(
        amount(9_000_000_000, 7),
      );
      expect(await noSharedBudgets.admit('o1', attribution, amount(0), amount(0))).to.deep.equal(amount(0));
    });

    it('names no budget that stops a run, and never reads what cut calls hold', async () => {
      let read = false;
      const held = async () => {
        read = true;
        return { org: 0, user: 0, agent: 0 };
      };
      expect(await noSharedBudgets.stoppingLeft('o1', attribution, held)).to.deep.equal({
        left: null,
        scope: null,
      });
      expect(read).to.equal(false);
    });

    it('records no spend, its fold does nothing, and it cannot be changed', async () => {
      expect(noSharedBudgets.recordsSpend).to.equal(false);
      expect(await noSharedBudgets.foldSoon('o1')).to.equal(undefined);
      expect(Object.isFrozen(noSharedBudgets)).to.equal(true);
    });
  });

  describe('noCostEraser', () => {
    it('has nothing to sweep or erase', async () => {
      expect(await noCostEraser.sweep(Date.now())).to.equal(0);
      expect(await noCostEraser.eraseUser('o1', 'u1')).to.equal(0);
      expect(Object.isFrozen(noCostEraser)).to.equal(true);
    });
  });

  describe('SharedBudgetReached', () => {
    it("is a budget conflict that names its scope, in the ledger's own codes, with the extension's reason", () => {
      const error = new SharedBudgetReached('BUDGET_EXHAUSTED', 'pool-a', 'The pool is used up', 'pool_a_spent');
      expect(error).to.be.instanceOf(HarnessConflictError);
      expect(error.code).to.equal('BUDGET_EXHAUSTED');
      expect(error.reasonCode).to.equal('pool_a_spent');
      expect(error.scope).to.equal('pool-a');
      expect(error.message).to.equal('The pool is used up');
    });
  });

  describe('resolved from the container', () => {
    it('unbound: the defaults', () => {
      const container = new Container();
      expect(sharedBudgetGuardOf(container)).to.equal(noSharedBudgets);
      expect(costEraserOf(container)).to.equal(noCostEraser);
      expect(runBudgetCeilingOf(container)).to.equal(null);
    });

    it('bound (an edition binder): what was bound', () => {
      const container = new Container();
      const guard: SharedBudgetGuard = { ...noSharedBudgets, recordsSpend: true };
      const eraser = { sweep: async () => 3, eraseUser: async () => 2 };
      container.bind(AGENT_HARNESS_SHARED_BUDGET_GUARD).toConstantValue(guard);
      const ceiling = { ceiling: async () => null };
      container.bind(AGENT_HARNESS_COST_ERASER).toConstantValue(eraser);
      container.bind(AGENT_HARNESS_RUN_BUDGET_CEILING).toConstantValue(ceiling);
      expect(sharedBudgetGuardOf(container)).to.equal(guard);
      expect(costEraserOf(container)).to.equal(eraser);
      expect(runBudgetCeilingOf(container)).to.equal(ceiling);
    });
  });

  describe('the budget ledger under the default guard', () => {
    const run = {
      orgId: 'o1',
      runId: 'r1',
      sessionId: 's1',
      actorId: 'u1',
      agentKey: null,
      parent: null,
      status: 'running',
      attemptId: 'a1',
      fence: 1,
      leaseExpiresAt: Date.now() + 3_600_000,
    } as unknown as HarnessRun;

    const service = (ledger: InMemoryBudgetLedger, shared?: SharedBudgetGuard) =>
      new BudgetLedgerService({
        ledger,
        runs: { pinned: async () => ({ run, pinned: null }) },
        attempts: {
          verifyAttemptToken: async () => ({ orgId: 'o1', runId: 'r1', attemptId: 'a1', fence: 1 }),
        } as never,
        gateways: null,
        ...(shared !== undefined ? { shared } : {}),
      });

    it('charges a commit to no shared budget: no attribution reaches the ledger', async () => {
      const ledger = new InMemoryBudgetLedger();
      const charges: unknown[] = [];
      const commit = ledger.commit.bind(ledger);
      ledger.commit = async (orgId, runId, key, actual, usage, charge, hold) => {
        charges.push(charge);
        return commit(orgId, runId, key, actual, usage, charge, hold);
      };
      await ledger.openRoot('o1', 'r1', { usdMicros: 5_000_000, tokens: null });
      const ledgerService = service(ledger);
      await ledgerService.reserve('t', 'r1', {
        key: 'k1',
        requested: amount(1_000_000),
        minimum: amount(1),
        ttlS: 60,
      });
      await ledgerService.commit('t', 'r1', 'k1', amount(400_000));
      expect(charges).to.deep.equal([undefined]);
    });

    it("a guard that records spend gets each commit's attribution", async () => {
      const ledger = new InMemoryBudgetLedger();
      const charges: unknown[] = [];
      const folds: string[] = [];
      const commit = ledger.commit.bind(ledger);
      ledger.commit = async (orgId, runId, key, actual, usage, charge, hold) => {
        charges.push(charge);
        return commit(orgId, runId, key, actual, usage, charge, hold);
      };
      await ledger.openRoot('o1', 'r1', { usdMicros: 5_000_000, tokens: null });
      const ledgerService = service(ledger, {
        ...noSharedBudgets,
        recordsSpend: true,
        foldSoon: async (orgId) => void folds.push(orgId),
      });
      await ledgerService.reserve('t', 'r1', { key: 'k1', requested: amount(1_000_000), minimum: amount(1), ttlS: 60 });
      await ledgerService.commit('t', 'r1', 'k1', amount(400_000));
      expect(charges).to.deep.equal([{ ...attribution, modelRole: 'primary' }]);
      expect(folds).to.deep.equal(['o1']);
    });
  });
});

/**
 * EV-10 / C9.01: what the grant auditor reconciles a run's usage from. The
 * audit evidence carries the token counts of every usage-bearing journal event
 * and the run's ledger account, whose `used` is the authority (OWD-8).
 */
import { expect } from 'chai';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { contractClock, createContractHarness, DURABLE_CAPABILITIES } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { testCipher } from './support/data-protection';
import { workerEvent } from './support/journal-fixtures';
import { HARNESS_ENABLED } from './support/rollout';

const RUN_ID = 'run-ev10';

async function world(withLedger: boolean) {
  const clock = contractClock();
  const backend = inMemoryBackend(clock);
  const h = createContractHarness(backend, clock);
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const budgets = new InMemoryBudgetLedger(() => clock.now());
  const service = new ExecutionService({
    store: backend.executionStore,
    journal: backend.journal,
    journals: new JournalService(backend.payloads, testCipher()),
    capabilities: DURABLE_CAPABILITIES,
    ...(withLedger ? { budgets } : {}),
  });
  const admitted = await h.admit(
    { input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } },
    RUN_ID,
  );
  return { service, budgets, orgId: admitted.run.orgId };
}

const response = (usage: Record<string, unknown>) =>
  workerEvent('model.response', {
    messageRef: inlineContentRef(Buffer.from('{"text":"ok","toolCalls":[]}')),
    toolCalls: [],
    usage,
    provider: 'openai',
    transport: 'opencode',
  });

describe('EV-10: the audit evidence carries the journal usage and the ledger account', () => {
  it('lists every model.response usage and the ledger used, and says when the run ended', async () => {
    const w = await world(true);
    await w.budgets.openRoot(w.orgId, RUN_ID, { usdMicros: null, tokens: null });
    const { reservation } = await w.budgets.reserve({
      orgId: w.orgId, runId: RUN_ID, key: 'call-1',
      requested: { usdMicros: 0, tokens: 100 }, minimum: { usdMicros: 0, tokens: 0 }, ttlMs: 60_000,
    });
    await w.budgets.commit(w.orgId, RUN_ID, reservation.key, { usdMicros: 0, tokens: 47 }, {
      inputTokens: 40, outputTokens: 7, cacheReadTokens: 0, cacheWriteTokens: 0,
    });
    const claimed = (await w.service.claim({ orgId: w.orgId, runId: RUN_ID, workerId: 'w', attemptId: 'a1', leaseDurationMs: 60_000 })).run;
    await w.service.commit({
      commandId: 'c1', orgId: w.orgId, runId: RUN_ID, workerId: 'w', attemptId: 'a1', fence: claimed.fence,
      events: [
        workerEvent('turn.started', { turn: 1 }),
        response({ inputTokens: 40, outputTokens: 7, cacheReadTokens: 0, cacheWriteTokens: 0, inputIncludesCached: true }),
      ],
      legacyEvents: [], outbox: [],
    });
    const evidence = await w.service.auditEvidence(w.orgId, RUN_ID);
    expect(evidence?.ended).to.equal(false);
    expect(evidence?.usage).to.deep.equal([
      {
        type: 'model.response', inputTokens: 40, outputTokens: 7, cacheReadTokens: 0, cacheWriteTokens: 0,
        inputIncludesCached: true, provider: 'openai', transport: 'opencode',
      },
    ]);
    expect(evidence?.ledger).to.deep.equal({
      used: { inputTokens: 40, outputTokens: 7, cacheReadTokens: 0, cacheWriteTokens: 0 },
      reserved: { usdMicros: 0, tokens: 0 },
    });
  });

  it('an account no settler told a usage answers used null: there is nothing to reconcile', async () => {
    const w = await world(true);
    await w.budgets.openRoot(w.orgId, RUN_ID, { usdMicros: null, tokens: null });
    const evidence = await w.service.auditEvidence(w.orgId, RUN_ID);
    expect(evidence?.ledger).to.deep.equal({ used: null, reserved: { usdMicros: 0, tokens: 0 } });
  });

  it('a run without a ledger account, or a Node without a ledger, answers ledger null', async () => {
    for (const withLedger of [true, false]) {
      const w = await world(withLedger);
      const evidence = await w.service.auditEvidence(w.orgId, RUN_ID);
      expect(evidence?.usage).to.deep.equal([]);
      expect(evidence?.ledger).to.equal(null);
    }
  });
});

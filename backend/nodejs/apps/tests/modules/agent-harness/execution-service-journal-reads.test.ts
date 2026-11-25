/**
 * What `ExecutionService` reads back from a run's journal for the approval routes and the grant auditor, and
 * the side effects of a worker's transition that only show outside the store:
 * - DECIDE409's decided interactions, the counted approvals, and the BUGS-B rule
 *   grant a card may offer, each from the events their writer may write (a worker's word on a Node-only event
 *   does not count);
 * - the auditor's evidence lists the run's tool intents, ids only;
 * - a worker that yields because it could not reach file storage (CH-09) marks blob storage degraded;
 * - a cancel whose wake hint fails still cancels, and says so in the log.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import type { RunJournalReader } from '../../../src/modules/agent-harness/application/run-journal-reader';
import type { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { dependencyHealth } from '../../../src/modules/agent-harness/ops/dependency-health';
import { contractClock, createContractHarness, DURABLE_CAPABILITIES } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { testCipher } from './support/data-protection';
import { HARNESS_ENABLED } from './support/rollout';

const RUN_ID = 'run-reads';

const event = (type: string, writer: string, data: Record<string, unknown>, attemptId?: string): JournalEvent =>
  ({ type, writer, data, ...(attemptId !== undefined ? { attemptId } : {}) }) as unknown as JournalEvent;

interface Logged {
  level: string;
  event: string;
  meta: Record<string, unknown>;
}

async function world(events: JournalEvent[] = []) {
  const clock = contractClock();
  const backend = inMemoryBackend(clock);
  const h = createContractHarness(backend, clock);
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const logged: Logged[] = [];
  const at = (level: string) => (message: string, meta?: Record<string, unknown>) => {
    logged.push({ level, event: message, meta: meta ?? {} });
  };
  const logger: HarnessLogger = { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') };
  const journal = { read: async () => events, warm: () => undefined } as unknown as RunJournalReader;
  const journals = new JournalService(backend.payloads, testCipher());
  const options = {
    store: backend.executionStore,
    journal: backend.journal,
    journals,
    capabilities: DURABLE_CAPABILITIES,
    logger,
  };
  const reading = new ExecutionService({ ...options, runJournal: journal });
  const service = new ExecutionService(options);
  const admitted = await h.admit(
    { input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } },
    RUN_ID,
  );
  const orgId = admitted.run.orgId;
  return { backend, reading, service, orgId, logged, admitted };
}

describe('ExecutionService: what the approval routes read from a run’s journal', () => {
  afterEach(() => {
    sinon.restore();
  });

  it('decided interactions: each resolved one with its decision; a resolution without one is not decided', async () => {
    const w = await world([
      event('interaction.requested', 'worker', { interactionId: 'i-open' }),
      event('interaction.resolved', 'node', { interactionId: 'i-1', decision: 'approve' }),
      event('interaction.resolved', 'node', { interactionId: 'i-2', decision: 'deny' }),
      event('interaction.resolved', 'node', { interactionId: 'i-3' }),
      event('interaction.resolved', 'node', { decision: 'approve' }),
    ]);
    const decided = await w.reading.interactionDecisions(w.orgId, RUN_ID);
    expect([...decided.entries()]).to.deep.equal([
      ['i-1', 'approve'],
      ['i-2', 'deny'],
    ]);
  });

  it('a rule grant offered: the latest explanation of that ask decides, and one without a full grant offers none', async () => {
    const grant = { instanceId: 'inst-1', toolName: 'send_mail' };
    const w = await world([
      event('interaction.explained', 'worker', { interactionId: 'i-1', ruleGrant: grant }),
      event('interaction.explained', 'worker', { interactionId: 'i-2', ruleGrant: grant }),
      event('interaction.explained', 'worker', { interactionId: 'i-2', ruleGrant: { instanceId: 'inst-1' } }),
      event('interaction.explained', 'node', { interactionId: 'i-3', ruleGrant: grant }),
    ]);
    expect(await w.reading.ruleGrantOffered(w.orgId, RUN_ID, 'i-1')).to.deep.equal(grant);
    expect(await w.reading.ruleGrantOffered(w.orgId, RUN_ID, 'i-2')).to.equal(null);
    expect(await w.reading.ruleGrantOffered(w.orgId, RUN_ID, 'i-3')).to.equal(null);
    expect(await w.reading.ruleGrantOffered(w.orgId, RUN_ID, 'i-missing')).to.equal(null);
  });

  it('the auditor’s evidence lists each tool intent by id and attempt, and nothing of a run that does not exist', async () => {
    const w = await world([
      event('tool.intent', 'worker', { invocationId: 't1.0', toolId: 'local@default/send_mail', args: { to: 'x' } }, 'a1'),
      event('tool.outcome', 'worker', { invocationId: 't1.0', status: 'ok' }, 'a1'),
      event('tool.intent', 'worker', {}),
    ]);
    const evidence = await w.reading.auditEvidence(w.orgId, RUN_ID);
    expect(evidence?.intents).to.deep.equal([
      { invocationId: 't1.0', toolId: 'local@default/send_mail', attemptId: 'a1' },
      { invocationId: '', toolId: '', attemptId: null },
    ]);
    expect(evidence?.ended).to.equal(false);
    expect(await w.reading.auditEvidence(w.orgId, 'run-none')).to.equal(null);
  });
});

describe('ExecutionService: what a transition or a cancel tells outside the store', () => {
  afterEach(() => sinon.restore());

  it('a worker that yields for an unreachable store marks blob storage degraded; another yield does not', async () => {
    const w = await world();
    const report = sinon.stub(dependencyHealth, 'report');
    const claim = async (attemptId: string) =>
      (await w.service.claim({ orgId: w.orgId, runId: RUN_ID, workerId: 'w', attemptId, leaseDurationMs: 60_000 })).run;
    const yieldFor = async (attemptId: string, reason: 'store_wait' | 'drain') => {
      const run = await claim(attemptId);
      await w.service.commit({
        commandId: `yield-${attemptId}`,
        orgId: w.orgId,
        runId: RUN_ID,
        workerId: 'w',
        attemptId,
        fence: run.fence,
        events: [],
        legacyEvents: [],
        transition: { status: 'queued', detail: { kind: 'yield', reason, notBefore: new Date(0).toISOString() } },
        outbox: [],
      });
    };
    await yieldFor('a1', 'drain');
    expect(report.called).to.equal(false);
    await yieldFor('a2', 'store_wait');
    expect(report.args).to.deep.equal([['blob']]);
    expect(w.logged.filter((line) => line.event === 'run.transition')).to.have.length(2);
  });

  it('a cancel whose wake hint fails is still cancelled, marks the KV store degraded and logs the error type', async () => {
    const w = await world();
    const report = sinon.stub(dependencyHealth, 'report');
    w.service.wakeOnCancel({
      wake: async () => {
        throw new RangeError('kv down');
      },
    } as never);
    await w.service.claim({ orgId: w.orgId, runId: RUN_ID, workerId: 'w', attemptId: 'a1', leaseDurationMs: 60_000 });
    const run = await w.backend.executionStore.get(w.orgId, RUN_ID);
    const receipt = await w.service.control({
      orgId: w.orgId,
      runId: RUN_ID,
      commandId: 'cancel-1',
      dispatchCommandId: 'cancel-dispatch-1',
      action: 'cancel',
      expectedRevision: run!.revision,
    });
    expect(receipt.run.status).to.equal('cancelling');
    expect(report.args).to.deep.equal([['kv']]);
    const failed = w.logged.find((line) => line.event === 'run.cancel_wake_failed');
    expect(failed?.level).to.equal('warn');
    expect(failed?.meta).to.include({ runId: RUN_ID, errorType: 'RangeError' });
  });
});

/**
 * EV-06 (C2.49): every tool call ends with exactly one outcome by its run's
 * end. A worker closes the calls it opened; when Node ends a run itself (a
 * cancel while no worker holds it, the sweeper) it journals a cancelled
 * outcome for each call a crashed attempt left open, ahead of the terminal
 * event. The root hook's history check (H8) judges every journal here too.
 */
import { expect } from 'chai';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { openToolCalls } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { contractClock, createContractHarness, DURABLE_CAPABILITIES } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { testCipher } from './support/data-protection';
import { workerEvent } from './support/journal-fixtures';
import { HARNESS_ENABLED } from './support/rollout';
import { journalHistoryViolations } from '../../support/harness/journal-history';

const RUN_ID = 'run-ev06';
const INTENT = (invocationId: string) =>
  workerEvent('tool.intent', {
    invocationId,
    toolId: 'local@default/send_mail',
    argsDigest: `sha256:${'a'.repeat(64)}`,
    effect: 'write',
    sink: { egress: 'external', argsLeaveBoundary: true },
  });
const OUTCOME = (invocationId: string) =>
  workerEvent('tool.outcome', { invocationId, status: 'ok', labels: { integrity: 'internal' } });

async function world() {
  const clock = contractClock();
  const backend = inMemoryBackend(clock);
  const h = createContractHarness(backend, clock);
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const journals = new JournalService(backend.payloads, testCipher());
  const service = new ExecutionService({
    store: backend.executionStore,
    journal: backend.journal,
    journals,
    capabilities: DURABLE_CAPABILITIES,
  });
  const admitted = await h.admit(
    { input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } },
    RUN_ID,
  );
  const orgId = admitted.run.orgId;
  const claim = async (attemptId: string) =>
    (await service.claim({ orgId, runId: RUN_ID, workerId: 'w', attemptId, leaseDurationMs: 60_000 })).run;
  const commit = (attemptId: string, fence: number, commandId: string, events = [INTENT('t1.0')]) =>
    service.commit({
      commandId,
      orgId,
      runId: RUN_ID,
      workerId: 'w',
      attemptId,
      fence,
      events,
      legacyEvents: [],
      outbox: [],
    });
  const journal = async (): Promise<JournalEvent[]> =>
    journals.resolve(await backend.journal.readRun(orgId, RUN_ID, 0, 1_000));
  const revision = async (): Promise<number> => (await backend.executionStore.get(orgId, RUN_ID))!.revision;
  return { clock, service, orgId, claim, commit, journal, admitted, revision };
}

const ending = (events: JournalEvent[]) =>
  events
    .filter((e) => e.type.startsWith('tool.') || e.type.startsWith('run.'))
    .map((e) => [e.type, e.writer, (e.data?.['invocationId'] ?? e.data?.['status'] ?? null) as unknown]);

describe('EV-06: Node closes the calls a run left open when it ends the run itself', () => {
  it('openToolCalls: an intent without an outcome is open; an outcome or an unknown ends it; a re-call reopens it', () => {
    const at = (type: string, invocationId: string) => ({ runId: 'r', type, data: { invocationId } });
    expect(openToolCalls([at('tool.intent', 'a'), at('tool.intent', 'b'), at('tool.outcome', 'a')], 'r')).to.deep.equal(['b']);
    expect(openToolCalls([at('tool.intent', 'a'), at('tool.unknown', 'a')], 'r')).to.deep.equal([]);
    expect(openToolCalls([at('tool.intent', 'a'), at('tool.unknown', 'a'), at('tool.intent', 'a')], 'r')).to.deep.equal(['a']);
    expect(openToolCalls([at('tool.intent', 'a')], 'other')).to.deep.equal([]);
  });

  it('a cancel of a run no worker holds journals a cancelled outcome for its open call before run.cancelled', async () => {
    const w = await world();
    const claimed = await w.claim('a1');
    await w.commit('a1', claimed.fence, 'intent-1');
    await w.service.release({
      orgId: w.orgId,
      runId: RUN_ID,
      workerId: 'w',
      attemptId: 'a1',
      fence: claimed.fence,
      reason: 'engine_gone',
    });

    await w.service.control({
      orgId: w.orgId,
      runId: RUN_ID,
      commandId: 'cancel-1',
      dispatchCommandId: 'cancel-dispatch-1',
      action: 'cancel',
      expectedRevision: await w.revision(),
    });

    const events = await w.journal();
    expect(ending(events).slice(-3)).to.deep.equal([
      ['run.cancel_requested', 'node', null],
      ['tool.outcome', 'node', 't1.0'],
      ['run.cancelled', 'node', null],
    ]);
    const closing = events.find((e) => e.type === 'tool.outcome')!;
    expect(closing.data).to.deep.equal({ invocationId: 't1.0', status: 'cancelled', labels: { integrity: 'internal' } });
    expect(journalHistoryViolations(events)).to.deep.equal([]);
  });

  it('a call the worker already ended is not closed again', async () => {
    const w = await world();
    const claimed = await w.claim('a1');
    await w.commit('a1', claimed.fence, 'intent-1', [INTENT('t1.0'), OUTCOME('t1.0')]);
    await w.service.release({
      orgId: w.orgId,
      runId: RUN_ID,
      workerId: 'w',
      attemptId: 'a1',
      fence: claimed.fence,
      reason: 'engine_gone',
    });
    await w.service.control({
      orgId: w.orgId,
      runId: RUN_ID,
      commandId: 'cancel-1',
      dispatchCommandId: 'cancel-dispatch-1',
      action: 'cancel',
      expectedRevision: await w.revision(),
    });
    const events = await w.journal();
    expect(events.filter((e) => e.type === 'tool.outcome').map((e) => e.writer)).to.deep.equal(['worker']);
    expect(journalHistoryViolations(events)).to.deep.equal([]);
  });

  it('a cancel whose worker never answered: the sweeper ends the run and closes the call it left open', async () => {
    const w = await world();
    const claimed = await w.claim('a1');
    await w.commit('a1', claimed.fence, 'intent-1');
    await w.service.control({
      orgId: w.orgId,
      runId: RUN_ID,
      commandId: 'cancel-1',
      dispatchCommandId: 'cancel-dispatch-1',
      action: 'cancel',
      expectedRevision: await w.revision(),
    });
    w.clock.set((claimed.leaseExpiresAt ?? 0) + 1);

    expect(await w.service.sweep(10)).to.be.greaterThan(0);

    expect(ending(await w.journal()).slice(-3)).to.deep.equal([
      ['run.cancel_requested', 'node', null],
      ['tool.outcome', 'node', 't1.0'],
      ['run.cancelled', 'node', null],
    ]);
  });

  it('the sweeper failing a run at its deadline closes the call its lapsed attempt left open', async () => {
    const w = await world();
    const claimed = await w.claim('a1');
    await w.commit('a1', claimed.fence, 'intent-1');
    w.clock.set(Math.max(claimed.absoluteDeadlineAt, claimed.leaseExpiresAt ?? 0) + 1);

    expect(await w.service.sweep(10)).to.be.greaterThan(0);

    const events = await w.journal();
    expect(ending(events).slice(-2)).to.deep.equal([
      ['tool.outcome', 'node', 't1.0'],
      ['run.failed', 'node', null],
    ]);
    expect(journalHistoryViolations(events)).to.deep.equal([]);
  });
});

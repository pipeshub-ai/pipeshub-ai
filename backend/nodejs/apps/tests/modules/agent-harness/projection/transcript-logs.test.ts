import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryJournalTail } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { RunStreamService } from '../../../../src/modules/agent-harness/projection/stream-service';
import { contractClock, createContractHarness, DURABLE_CAPABILITIES } from '../support/execution-store-contract';
import { workerEvent } from '../support/journal-fixtures';
import { HARNESS_ENABLED } from '../support/rollout';
import { testCipher } from '../support/data-protection';

type Line = [level: string, message: string, meta: Record<string, unknown>];
const recording = (): { logger: HarnessLogger; lines: Line[] } => {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } };
};

/** Ground rule 9: where the log is the only signal of a failure, a test asserts it. */
describe('agent-harness projection logs (ground rule 9)', () => {
  it('a transcript write that fails is logged with its run and command, and nothing of it', async () => {
    const clock = contractClock();
    let fail = true;
    const store = new InMemoryExecutionStore(clock, undefined, undefined, {
      record: () => (fail ? Promise.reject(new TypeError('boom: secret answer text')) : Promise.resolve(true)),
    });
    const payloads = new InMemoryPayloadStore();
    const h = createContractHarness(
      { executionStore: store, journal: store.journal, payloads, admissionStore: store, dispatches: async () => [], deletePayload: async () => undefined },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const { logger, lines } = recording();
    const service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
    const claimed = await service.claim({ orgId: admitted.run.orgId, runId: admitted.run.runId, workerId: 'w', attemptId: 'a1', leaseDurationMs: 1_000 });
    const complete = () =>
      service.commit({
        commandId: 'a1:complete:1',
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'w',
        attemptId: 'a1',
        fence: claimed.run.fence,
        events: [
          workerEvent(
            'x.pipeshub.answer.finalized',
            { outcome: 'answered', answer: 'secret answer text', partTexts: ['secret answer text'], citations: [], confidence: null },
            { critical: false },
          ),
        ],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      });
    await complete().then(
      () => expect.fail('the commit should have failed'),
      () => undefined,
    );
    const failed = lines.filter(([, message]) => message === 'transcript.projection_failed');
    expect(failed).to.have.length(1);
    expect(failed[0]![0]).to.equal('error');
    expect(failed[0]![2]).to.include({
      runId: admitted.run.runId,
      commandId: 'a1:complete:1',
      kind: 'answer',
      errorType: 'TypeError',
      outcome: 'commit_rolled_back',
    });
    expect(JSON.stringify(lines)).to.not.include('secret answer text');

    fail = false;
    await complete();
    const projected = lines.filter(([, message]) => message === 'transcript.projected');
    expect(projected[0]![2]).to.include({ runId: admitted.run.runId, kind: 'answer', parts: 1, citations: 0, questions: 0 });
  });

  describe('PA-10: the sweeper projecting a deadline failure', () => {
    const sweptWith = async (save: () => Promise<boolean>) => {
      const clock = contractClock();
      const store = new InMemoryExecutionStore(clock, undefined, undefined, { record: save });
      const payloads = new InMemoryPayloadStore();
      const h = createContractHarness(
        { executionStore: store, journal: store.journal, payloads, admissionStore: store, dispatches: async () => [], deletePayload: async () => undefined },
        clock,
      );
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      const { logger, lines } = recording();
      const service = new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(payloads, testCipher()),
        capabilities: DURABLE_CAPABILITIES,
        logger,
      });
      const { run } = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      clock.set(clock.now() + 60 * 60_000);
      expect(await service.sweep(10)).to.equal(1);
      expect((await service.get(run.orgId, run.runId))?.status).to.equal('failed');
      return { run, lines };
    };

    it('logs projection.deadline_failure with the run and the code, and nothing of the answer', async () => {
      const { run, lines } = await sweptWith(() => Promise.resolve(true));
      const projected = lines.filter(([, message]) => message === 'projection.deadline_failure');
      expect(projected).to.have.length(1);
      expect(projected[0]![0]).to.equal('info');
      expect(projected[0]![2]).to.include({
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        code: 'deadline_exceeded',
      });
      expect(JSON.stringify(lines)).to.not.include('took too long');
    });

    it('a session that no longer exists falls back to the status and is logged', async () => {
      const { run, lines } = await sweptWith(() => Promise.resolve(false));
      expect(lines.some(([, message]) => message === 'projection.deadline_failure')).to.equal(false);
      const skipped = lines.find(([, message]) => message === 'projection.deadline_failure_skipped')!;
      expect(skipped[0]).to.equal('warn');
      expect(skipped[2]).to.include({ runId: run.runId, code: 'deadline_exceeded', reason: 'session_missing' });
    });

    it('a transcript write that fails is logged; the run still ends', async () => {
      const { run, lines } = await sweptWith(() => Promise.reject(new TypeError('boom')));
      const failed = lines.find(([, message]) => message === 'transcript.projection_failed')!;
      expect(failed[0]).to.equal('error');
      expect(failed[2]).to.include({ runId: run.runId, code: 'deadline_exceeded', errorType: 'TypeError', outcome: 'status_only' });
    });
  });

  it('a stream that cannot be projected is logged, ended, and unsubscribed', async () => {
    const { logger, lines } = recording();
    const tail = new InMemoryJournalTail();
    const streams = new RunStreamService({
      journal: { readRun: () => Promise.reject(new RangeError('journal read failed')) },
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      tail,
      conversations: { finishedConversation: () => Promise.resolve(null) },
      liveBus: null,
      logger,
    });
    let ended = false;
    const handle = streams.open(
      { run: { runId: 'run-x', orgId: 'org-1', sessionId: 'ses-1', actorId: 'u', createdAt: 0, updatedAt: 0 } as unknown as HarnessRun, audience: 'owner' },
      { write: () => true, end: () => { ended = true; }, onDrain: () => undefined, pending: 0 },
    );
    await handle.done;
    expect(ended).to.equal(true);
    const [level, , meta] = lines.find(([, message]) => message === 'projection.failed')!;
    expect(level).to.equal('error');
    expect(meta).to.include({ runId: 'run-x', errorType: 'RangeError' });
    expect(lines.find(([, message]) => message === 'projection.unsubscribe')![2]).to.include({ reason: 'projection_failed' });
    expect(tail.watching).to.equal(0);
  });
});

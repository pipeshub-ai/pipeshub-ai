/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. Commits: the active run, atomic and idempotent commits, tenant scoping, batched journal commits, transcripts.
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { HarnessNotFoundError, JournalPayloadMissingError, JournalRejectedError, ConversationDeletedError, TranscriptProjectionError } from '../../../../../src/modules/agent-harness/domain/errors';
import { CommitBatch, HarnessRun } from '../../../../../src/modules/agent-harness/domain/execution';
import { HARNESS_ENABLED } from '../rollout';
import { JOURNAL_INLINE_PAYLOAD_MAX_BYTES } from '../../../../../src/modules/agent-harness/domain/journal';
import { bulkyOutcome, turnStarted, workerEvent } from '../journal-fixtures';
import { ContractHarness, START, DEADLINE_MS, conflictCode } from './harness';

export function describeCommitCases(
  harness: () => ContractHarness,
): void {
  let h: ContractHarness;
  beforeEach(() => {
    h = harness();
  });

  it('finds the active run by tenant session and clears it on completion', async () => {
    const { run, sessionId } = await h.admit();
    expect(
      (await h.service.getActive(h.tenant.orgId, sessionId))?.runId,
    ).to.equal(run.runId);
    expect(
      await h.service.getActive(h.newTenant().orgId, sessionId),
    ).to.equal(null);

    const claimed = await h.claim(run);
    await h.service.commit({
      commandId: 'complete-1',
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: claimed.attemptId as string,
      fence: claimed.fence,
      expectedRevision: claimed.revision,
      status: 'completed',
      outcome: 'answer',
      events: [{ type: 'run_completed', data: { answer: 'done' } }],
      outbox: [],
    });
    expect(await h.service.getActive(h.tenant.orgId, sessionId)).to.equal(
      null,
    );
  });

  it('atomically commits a checkpoint, ordered events and outbox command', async () => {
    const admitted = await h.admit();
    const { run } = admitted;
    const claimed = await h.claim(run);
    const command = {
      commandId: 'commit-1',
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: claimed.attemptId as string,
      fence: claimed.fence,
      expectedRevision: claimed.revision,
      status: 'completed' as const,
      outcome: 'answer' as const,
      checkpoint: {
        objectId: 'checkpoint-1',
        contentHash: 'sha256:cp',
        sizeBytes: 42,
      },
      events: [
        {
          type: 'text_snapshot',
          data: { messageId: 'message-2', text: 'done' },
        },
        { type: 'run_completed', data: { outcome: 'answer' } },
      ],
      outbox: [
        {
          commandId: 'project-message-1',
          runId: run.runId,
          kind: 'run_resume' as const,
        },
      ],
    };
    const receipt = await h.service.commit(command);
    const duplicate = await h.service.commit(command);
    expect(duplicate).to.deep.equal({ ...receipt, replayed: true });
    expect(receipt.firstEventSeq).to.equal(admitted.lastEventSeq + 1);
    expect(receipt.lastEventSeq).to.equal(admitted.lastEventSeq + 2);
    expect(receipt.run.checkpoint?.objectId).to.equal('checkpoint-1');
    expect(receipt.run.workerId).to.equal(null);
    expect(
      (await h.backend.dispatches(run.orgId, run.runId)).map(
        (item) => item.commandId,
      ),
    ).to.include('project-message-1');
    expect(
      await h.service.readEvents(run.orgId, admitted.sessionId),
    ).to.have.length(3);

    const next = await h.admit({ sessionId: admitted.sessionId });
    expect(next.created).to.equal(true);
  });

  it('does not reveal a run through another tenant scope', async () => {
    const runId = `shared-${randomUUID()}`;
    const { run } = await h.admit({}, runId);
    const other = h.newTenant();
    expect(await h.service.get(other.orgId, runId)).to.equal(null);
    const otherRun = await h.admit(other, runId);
    expect(otherRun.created).to.equal(true);
    expect((await h.service.get(run.orgId, runId))?.actorId).to.equal(
      h.tenant.actorId,
    );
    expect((await h.service.get(other.orgId, runId))?.orgId).to.equal(
      other.orgId,
    );
    try {
      await h.claim({ ...run, orgId: h.newTenant().orgId });
      expect.fail('Expected not found');
    } catch (error) {
      expect(error).to.be.instanceOf(HarnessNotFoundError);
    }
  });

  it('treats semantically identical commit payloads as idempotent', async () => {
    const { run } = await h.admit();
    const claimed = await h.claim(run);
    const command = {
      commandId: 'commit-ordered',
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: claimed.attemptId as string,
      fence: claimed.fence,
      expectedRevision: claimed.revision,
      status: 'completed' as const,
      outcome: 'answer' as const,
      outbox: [],
      events: [{ type: 'result', data: { alpha: 1, beta: 2 } }],
    };
    const first = await h.service.commit(command);
    const second = await h.service.commit({
      ...command,
      events: [{ type: 'result', data: { beta: 2, alpha: 1 } }],
    });
    expect(second).to.deep.equal({ ...first, replayed: true });
  });

  describe('batched journal commits (P1.1)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };

    const claimed = async (major: 1 | 2 = 2) => {
      h.setRollout(major === 2 ? V2 : HARNESS_ENABLED);
      const admitted = await h.admit();
      const claim = await h.service.claim({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-a',
        leaseDurationMs: 1_000,
      });
      return { admitted, claim, run: claim.run };
    };

    const batch = (
      run: HarnessRun,
      overrides: Partial<CommitBatch> = {},
    ): CommitBatch => ({
      commandId: `attempt-a:turn:${String(run.revision)}`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: run.attemptId as string,
      fence: run.fence,
      events: [],
      legacyEvents: [],
      outbox: [],
      ...overrides,
    });

    /** Node writes these at admission and claim; the cases below are about the rest. */
    const LIFECYCLE = new Set([
      'run.admitted',
      'run.claimed',
      'run.spec_pinned',
      // Node's own note with a takeover's claim (CH-01): written by the claim, never by an attempt.
      'x.pipeshub.run.notice',
    ]);
    /** run.admitted, run.claimed and run.spec_pinned precede every worker event. */
    const CLAIMED_HEAD = 3;
    const stream = async (run: HarnessRun) =>
      (
        await h.backend.journal.readStream(run.orgId, run.sessionId, 0, 100)
      ).filter((event) => !LIFECYCLE.has(event.type));

    it('J-8: claim returns lastSeq of the run stream, which a fold must reach', async () => {
      const { run, claim } = await claimed();
      expect(claim.journal.lastSeq).to.deep.equal({
        [run.sessionId]: CLAIMED_HEAD,
      });
      await h.service.commit(
        batch(run, { events: [turnStarted(1), turnStarted(2)] }),
      );
      h.setNow(START + 1_001);
      const again = await h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-b',
        attemptId: 'attempt-b',
        leaseDurationMs: 1_000,
      });
      // Two worker events, then the re-claim's own run.claimed and, the lease having lapsed, its notice (CH-01).
      expect(again.journal.lastSeq).to.deep.equal({
        [run.sessionId]: CLAIMED_HEAD + 4,
      });
      const read = await h.service.readRunJournal({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-b',
        attemptId: 'attempt-b',
        fence: again.run.fence,
        afterSeq: 0,
        limit: 100,
        scope: 'stream',
      });
      expect(read.map((event) => event.seq)).to.deep.equal([
        1, 2, 3, 4, 5, 6, 7,
      ]);
      expect(
        read
          .filter((event) => event.writer === 'worker')
          .every((event) => event.fence === run.fence),
      ).to.equal(true);
    });

    it('J-6: a retried commandId replays its receipt and writes nothing twice', async () => {
      const { run } = await claimed();
      const command = batch(run, { events: [turnStarted(1)] });
      const first = await h.service.commit(command);
      const retry = await h.service.commit(structuredClone(command));
      expect(first.replayed).to.equal(undefined);
      expect(retry).to.deep.equal({ ...first, replayed: true });
      expect(first.journal).to.deep.equal({
        streamId: run.sessionId,
        firstSeq: CLAIMED_HEAD + 1,
        lastSeq: CLAIMED_HEAD + 1,
      });
      expect(await stream(run)).to.have.length(1);
      expect(
        await conflictCode(
          h.service.commit({ ...command, events: [turnStarted(2)] }),
        ),
      ).to.equal('IDEMPOTENCY_CONFLICT');
    });

    it('J-6: a timed-out commit replays its receipt even after the lease moved on', async () => {
      const { run } = await claimed();
      const command = batch(run, { events: [turnStarted(1)] });
      const first = await h.service.commit(command);
      h.setNow(START + 1_001);
      await h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-b',
        attemptId: 'attempt-b',
        leaseDurationMs: 1_000,
      });
      expect(await h.service.commit(command)).to.deep.equal({
        ...first,
        replayed: true,
      });
      expect(await stream(run)).to.have.length(1);
    });

    it('J-2: a worker commit carrying a node-only event is refused whole', async () => {
      const { run } = await claimed();
      try {
        await h.service.commit(
          batch(run, {
            events: [
              turnStarted(1),
              workerEvent('run.completed', { outcome: 'answered' }),
            ],
            transition: {
              expectedRevision: run.revision,
              status: 'completed',
              outcome: 'answer',
            },
          }),
        );
        expect.fail('expected a rejection');
      } catch (error) {
        expect(error).to.be.instanceOf(JournalRejectedError);
        expect((error as JournalRejectedError).code).to.equal(
          'WRITER_NOT_ALLOWED',
        );
      }
      expect(await stream(run)).to.deep.equal([]);
      const current = await h.service.get(run.orgId, run.runId);
      expect(current?.status).to.equal('running');
      expect(current?.revision).to.equal(run.revision);
    });

    it('journals Node-authored run.* events for a v2 terminal transition', async () => {
      const { run, admitted } = await claimed();
      const receipt = await h.service.commit(
        batch(run, {
          events: [turnStarted(1)],
          transition: {
            expectedRevision: run.revision,
            status: 'completed',
            outcome: 'answer',
          },
        }),
      );
      expect(receipt.run.status).to.equal('completed');
      expect(receipt.journal).to.deep.equal({
        streamId: run.sessionId,
        firstSeq: CLAIMED_HEAD + 1,
        lastSeq: CLAIMED_HEAD + 2,
      });
      const events = await stream(run);
      expect(
        events.map((event) => [event.type, event.writer, event.data]),
      ).to.deep.equal([
        ['turn.started', 'worker', { turn: 1 }],
        ['run.completed', 'node', { outcome: 'answered' }],
      ]);
      expect(
        await h.service.getActive(run.orgId, admitted.sessionId),
      ).to.equal(null);
    });

    it('keeps durable v1 events out of v2 runs and journal-free v1 commits unchanged', async () => {
      const { run } = await claimed();
      expect(
        await conflictCode(
          h.service.commit(
            batch(run, { legacyEvents: [{ type: 'x', data: {} }] }),
          ),
        ),
      ).to.equal('INVALID_COMMIT');
      const v1 = await claimed(1);
      const receipt = await h.service.commit({
        commandId: 'v1-progress',
        orgId: v1.run.orgId,
        runId: v1.run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-a',
        fence: v1.run.fence,
        expectedRevision: v1.run.revision,
        status: 'running',
        events: [{ type: 'TEXT_MESSAGE_CONTENT', data: { delta: 'x' } }],
        outbox: [],
      });
      expect(receipt.journal).to.equal(null);
      expect(receipt.lastEventSeq).to.equal(2);
      expect(await stream(v1.run)).to.deep.equal([]);
    });

    it('fences journal commits: a stale attempt appends nothing', async () => {
      const { run } = await claimed();
      h.setNow(START + 1_001);
      await h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-b',
        attemptId: 'attempt-b',
        leaseDurationMs: 1_000,
      });
      expect(
        await conflictCode(
          h.service.commit(batch(run, { events: [turnStarted(1)] })),
        ),
      ).to.equal('STALE_ATTEMPT');
      expect(await stream(run)).to.deep.equal([]);
    });

    it('refuses the owner once its own lease lapsed, before anyone claims again', async () => {
      // Every token check passes; only the lease, on store time, has run out.
      const { run } = await claimed();
      h.setNow(START + 1_001);
      expect(
        await conflictCode(
          h.service.commit(batch(run, { events: [turnStarted(1)] })),
        ),
      ).to.equal('STALE_ATTEMPT');
      expect(await stream(run)).to.deep.equal([]);
    });

    it('moves a workspace HEAD by CAS in the same commit as workspace.committed', async () => {
      const { run } = await claimed();
      const committed = (revision: number) =>
        workerEvent('workspace.committed', {
          workspaceId: 'ws-1',
          revision,
          changed: ['/notes.md'],
        });
      const first = await h.service.commit(
        batch(run, {
          events: [committed(1)],
          workspaceHeadCAS: {
            workspaceId: 'ws-1',
            expectedRevision: 0,
            revision: 1,
          },
        }),
      );
      expect(
        await conflictCode(
          h.service.commit(
            batch(first.run, {
              events: [committed(2)],
              workspaceHeadCAS: {
                workspaceId: 'ws-1',
                expectedRevision: 0,
                revision: 2,
              },
            }),
          ),
        ),
      ).to.equal('WORKSPACE_CONFLICT');
      expect(
        await conflictCode(
          h.service.commit(
            batch(first.run, {
              commandId: 'no-event',
              workspaceHeadCAS: {
                workspaceId: 'ws-1',
                expectedRevision: 1,
                revision: 2,
              },
            }),
          ),
        ),
      ).to.equal('INVALID_COMMIT');
      const second = await h.service.commit(
        batch(first.run, {
          commandId: 'second',
          events: [committed(2)],
          workspaceHeadCAS: {
            workspaceId: 'ws-1',
            expectedRevision: 1,
            revision: 2,
          },
        }),
      );
      expect(second.journal?.lastSeq).to.equal(CLAIMED_HEAD + 2);
      expect(await stream(run)).to.have.length(2);
    });

    it('J-4: a missing claim-checked payload fails the run with journal_payload_missing', async () => {
      const { run, admitted } = await claimed();
      await h.service.commit(
        batch(run, {
          events: [bulkyOutcome(JOURNAL_INLINE_PAYLOAD_MAX_BYTES + 10)],
        }),
      );
      const [stored] = await stream(run);
      expect(stored?.payloadRef).to.not.equal(undefined);
      const current = (await h.service.get(run.orgId, run.runId))!;
      const read = (after: number) =>
        h.service.readRunJournal({
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'worker-a',
          attemptId: 'attempt-a',
          fence: current.fence,
          afterSeq: after,
          limit: 10,
          scope: 'run',
        });
      const [resolved] = await read(CLAIMED_HEAD);
      expect(resolved?.data).to.deep.equal(
        bulkyOutcome(JOURNAL_INLINE_PAYLOAD_MAX_BYTES + 10).data,
      );

      await h.backend.deletePayload(run.orgId, stored!.payloadRef!.ref);
      try {
        await read(0);
        expect.fail('expected a missing payload');
      } catch (error) {
        expect(error).to.be.instanceOf(JournalPayloadMissingError);
      }
      const failed = await h.service.get(run.orgId, run.runId);
      expect(failed?.status).to.equal('failed');
      expect(
        await h.service.getActive(run.orgId, admitted.sessionId),
      ).to.equal(null);
      const last = (await stream(run)).at(-1);
      expect([last?.type, last?.writer, last?.data]).to.deep.equal([
        'run.failed',
        'node',
        { errorType: 'journal_payload_missing' },
      ]);
    });

    it('reads the journal only for the owning attempt', async () => {
      const { run } = await claimed();
      expect(
        await conflictCode(
          h.service.readRunJournal({
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'worker-a',
            attemptId: 'attempt-a',
            fence: run.fence + 1,
            afterSeq: 0,
            limit: 10,
            scope: 'stream',
          }),
        ),
      ).to.equal('STALE_ATTEMPT');
    });
  });

  describe('transcript projection in the commit transaction (P1.8)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };
    const presented = (extra: Record<string, unknown> = {}) =>
      workerEvent(
        'x.pipeshub.answer.finalized',
        {
          outcome: 'answered',
          answer: 'Twenty-five days.',
          partTexts: ['Twenty-five days.'],
          citations: [],
          confidence: null,
          ...extra,
        },
        { critical: false },
      );
    const shown = workerEvent(
      'x.pipeshub.message.presented',
      { text: 'Twenty-five days.' },
      { critical: false },
    );
    const complete = (run: HarnessRun, commandId = 'worker-a:complete') =>
      h.service.commit({
        commandId,
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        events: [presented()],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      });

    beforeEach(function () {
      if (h.backend.transcripts === undefined) this.skip();
      h.setRollout(V2);
    });

    it('writes the answer the stream shows with the run.completed that ends it', async () => {
      const admitted = await h.admit(agentRun);
      const run = await h.claim(admitted.run);
      await complete(run);
      const written = await h.backend.transcripts!.written(run);
      expect(written.map((m) => m['messageType'])).to.deep.equal(['bot_response']);
      expect(written[0]!['content']).to.equal('Twenty-five days.');
      expect(written[0]!['parts']).to.deep.equal([
        { type: 'text', content: 'Twenty-five days.', isFinal: true },
      ]);
      shown;
    });

    it('EU-22 (QA21-F4): completing a run whose conversation was deleted is refused naming the cause', async () => {
      const admitted = await h.admit(agentRun);
      const run = await h.claim(admitted.run);
      await h.backend.transcripts!.deleteConversation(run);
      let refused: unknown = null;
      try {
        await complete(run);
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(ConversationDeletedError);
      expect((refused as ConversationDeletedError).code).to.equal('CONVERSATION_DELETED');
      const tail = await h.backend.journal.readRun(run.orgId, run.runId, 0, 100);
      expect(tail.map((e) => e.type)).to.not.include('run.completed');
    });

    it('EU-22 (QA21-F4): after erasure stopped the run, its completion still names the deleted conversation', async () => {
      const admitted = await h.admit(agentRun);
      const run = await h.claim(admitted.run);
      await h.backend.transcripts!.deleteConversation(run);
      await h.service.control({
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'cancel',
        by: 'p_system_retention',
        commandId: `erase:${run.runId}`,
        dispatchCommandId: `erase:${run.runId}:dispatch`,
      });
      let refused: unknown = null;
      try {
        await complete({ ...run, fence: run.fence });
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(ConversationDeletedError);
    });

    it('EU-23 (R30-LAT, ADR-RUN-05 D4): a stopped run whose conversation was deleted ends cancelled; its transcript is dropped', async () => {
      const admitted = await h.admit(agentRun);
      const run = await h.claim(admitted.run);
      await h.backend.transcripts!.deleteConversation(run);
      await h.service.control({
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'cancel',
        by: 'p_user',
        commandId: `erase:${run.runId}`,
        dispatchCommandId: `erase:${run.runId}:dispatch`,
      });
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('cancelling');
      await h.service.commit({
        commandId: 'worker-a:cancelled',
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        events: [presented({ outcome: 'stopped' })],
        legacyEvents: [],
        transition: { status: 'cancelled', errorType: 'cancelled' },
        outbox: [],
      });
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('cancelled');
      expect(await h.backend.transcripts!.written(run)).to.deep.equal([]);
    });

    it('a stopped run whose conversation exists is refused as before', async () => {
      const admitted = await h.admit(agentRun);
      const run = await h.claim(admitted.run);
      await h.service.control({
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'cancel',
        by: 'p_user',
        commandId: `stop:${run.runId}`,
        dispatchCommandId: `stop:${run.runId}:dispatch`,
      });
      let refused: unknown = null;
      try {
        await complete(run);
      } catch (error) {
        refused = error;
      }
      expect(refused).to.not.be.instanceOf(ConversationDeletedError);
    });

    it('L-11: a transcript write that fails rolls the commit back; the retried command writes both once', async () => {
      const admitted = await h.admit(agentRun);
      const run = await h.claim(admitted.run);
      h.backend.transcripts!.failNextWrite();
      let failed: unknown = null;
      try {
        await complete(run);
      } catch (error) {
        failed = error;
      }
      expect(failed).to.be.instanceOf(TranscriptProjectionError);
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('running');
      const tail = await h.backend.journal.readRun(run.orgId, run.runId, 0, 100);
      expect(tail.map((e) => e.type)).to.not.include('run.completed');
      expect(tail.map((e) => e.type)).to.not.include('x.pipeshub.answer.finalized');
      expect(await h.backend.transcripts!.written(run)).to.deep.equal([]);

      const receipt = await complete(run);
      expect(receipt.run.status).to.equal('completed');
      const replayed = await complete(run);
      expect(replayed.replayed).to.equal(true);
      const written = await h.backend.transcripts!.written(run);
      expect(written).to.have.length(1);
      const types = (await h.backend.journal.readRun(run.orgId, run.runId, 0, 100)).map((e) => e.type);
      expect(types.filter((t) => t === 'run.completed')).to.have.length(1);
    });

    it('L-19: a question is written as its tools[] row before the fallback answer', async () => {
      const admitted = await h.admit(agentRun);
      const run = await h.claim(admitted.run);
      const toolData = { name: 'ask_user_question', questions: [{ question: 'Which region?' }] };
      await h.service.commit({
        commandId: 'worker-a:asked',
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        events: [
          workerEvent('x.pipeshub.question.asked', { callId: 'call-ask', status: 'success', toolData }, { critical: false }),
          presented({
            outcome: 'needs_input',
            answer: "I wasn't able to generate a response. Please try rephrasing.",
            partTexts: ["I wasn't able to generate a response. Please try rephrasing."],
            confidence: 'Low',
            answerMatchType: 'Fallback Response',
          }),
        ],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'needs_input' },
        outbox: [],
      });
      const written = await h.backend.transcripts!.written(run);
      expect(written.map((m) => m['messageType'])).to.deep.equal(['tool_call', 'bot_response']);
      expect(written[0]!['tools']).to.deep.equal([{ toolName: 'ask_user_question', toolResult: toolData }]);
    });

    it("WP-36 IQ-5: the person's runs that ended asking them are listed until the conversation's next run", async () => {
      const ask = async (admitted: { run: HarnessRun }, worker: string) => {
        const run = await h.claim(admitted.run, worker);
        await h.service.commit({
          commandId: `${worker}:asked`,
          orgId: run.orgId,
          runId: run.runId,
          workerId: worker,
          attemptId: `attempt-${worker}`,
          fence: run.fence,
          events: [
            workerEvent('x.pipeshub.question.asked', { callId: 'call-ask', status: 'success', toolData: 'Which region?' }, { critical: false }),
            presented({ outcome: 'needs_input', answer: 'Which region?', partTexts: ['Which region?'] }),
          ],
          legacyEvents: [],
          transition: { status: 'completed', outcome: 'needs_input' },
          outbox: [],
        });
        return run;
      };
      const store = h.backend.executionStore;
      const first = await ask(await h.admit(agentRun), 'worker-a');
      h.setNow(START + 100);
      const other = await ask(await h.admit(agentRun), 'worker-b');
      const ids = async (filter: { actorId: string; agentKey?: string; limit: number }, orgId = first.orgId) =>
        (await store.unansweredQuestionRuns(orgId, filter)).map((run) => run.runId);
      expect(await ids({ actorId: first.actorId, limit: 10 })).to.deep.equal([other.runId, first.runId]);
      expect(await ids({ actorId: first.actorId, limit: 1 })).to.deep.equal([other.runId]);
      expect(await ids({ actorId: first.actorId, agentKey: 'agent-other', limit: 10 })).to.deep.equal([]);
      expect(await ids({ actorId: 'someone-else', limit: 10 })).to.deep.equal([]);
      expect(await ids({ actorId: first.actorId, limit: 10 }, h.newTenant().orgId)).to.deep.equal([]);
      // The answer is the conversation's next run: the question leaves the inbox.
      h.setNow(START + 200);
      await h.admit({ ...agentRun, sessionId: first.sessionId });
      expect(await ids({ actorId: first.actorId, limit: 10 })).to.deep.equal([other.runId]);
    });

    it('L-18: a run failing without an answer leaves its failure in the conversation', async () => {
      const admitted = await h.admit(agentRun);
      const run = await h.claim(admitted.run);
      await h.service.commit({
        commandId: 'worker-a:failed',
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        events: [],
        legacyEvents: [],
        transition: { status: 'failed', errorType: 'capability_lost', detail: { kind: 'fail' } },
        outbox: [],
      });
      const written = await h.backend.transcripts!.written(run);
      expect(written.map((m) => m['messageType'])).to.deep.equal(['error']);
      expect(String(written[0]!['content'])).to.include('no longer available');
    });

    describe('DL-1: a run the sweeper fails at its absolute deadline leaves the legacy error answer (PA-10)', () => {
      const DEADLINE_MESSAGE =
        'This run took too long and was stopped. Please try sending your message again.';
      const errorAnswer = [
        {
          messageType: 'error',
          content: DEADLINE_MESSAGE,
          confidence: 'Low',
          parts: [{ type: 'text', content: DEADLINE_MESSAGE, isFinal: true }],
        },
      ];
      const shape = (messages: Array<Record<string, unknown>>) =>
        messages.map(({ messageType, content, confidence, parts }) => ({ messageType, content, confidence, parts }));
      const journaled = async (run: HarnessRun) =>
        (await h.backend.journal.readRun(run.orgId, run.runId, 0, 100)).map((e) => e.type);

      it('an idle run past its deadline', async () => {
        const admitted = await h.admit(agentRun);
        h.setNow(START + DEADLINE_MS);
        expect(await h.service.sweep(10)).to.equal(1);
        expect(shape(await h.backend.transcripts!.written(admitted.run))).to.deep.equal(errorAnswer);
        expect((await h.service.get(admitted.run.orgId, admitted.run.runId))?.status).to.equal('failed');
        expect((await journaled(admitted.run)).slice(-1)).to.deep.equal(['run.failed']);
        expect(await h.service.sweep(10)).to.equal(0);
        expect(await h.backend.transcripts!.written(admitted.run)).to.have.length(1);
      });

      it('a lease-expired run whose deadline elapsed', async () => {
        const admitted = await h.admit(agentRun);
        const run = await h.claim(admitted.run);
        h.setNow(START + DEADLINE_MS);
        expect(await h.service.sweep(10)).to.equal(1);
        expect(shape(await h.backend.transcripts!.written(run))).to.deep.equal(errorAnswer);
      });

      it('a transcript write that fails rolls back with the sweep, which then ends the run with its status only', async () => {
        const admitted = await h.admit(agentRun);
        h.setNow(START + DEADLINE_MS);
        h.backend.transcripts!.failNextWrite();
        expect(await h.service.sweep(10)).to.equal(1);
        expect(await h.backend.transcripts!.written(admitted.run)).to.deep.equal([]);
        expect((await h.service.get(admitted.run.orgId, admitted.run.runId))?.status).to.equal('failed');
        expect((await journaled(admitted.run)).filter((t) => t === 'run.failed')).to.have.length(1);
      });
    });

    it('a cancel of a run no worker holds writes the stopped transcript in the control transaction', async () => {
      const admitted = await h.admit(agentRun);
      await h.service.control({
        commandId: 'user:cancel',
        dispatchCommandId: 'user:cancel:dispatch',
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        expectedRevision: admitted.run.revision,
        action: 'cancel',
      });
      const written = await h.backend.transcripts!.written(admitted.run);
      expect(written.map((m) => m['messageType'])).to.deep.equal(['bot_response']);
      expect(written[0]!['status']).to.equal('stopped');
    });
  });
}

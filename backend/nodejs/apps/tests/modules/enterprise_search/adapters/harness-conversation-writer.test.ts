import { expect } from 'chai';
import sinon from 'sinon';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { HarnessConversationWriter } from '../../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';

const run = {
  runId: 'run-1',
  orgId: '507f1f77bcf86cd799439011',
  actorId: '507f191e810c19729de860ea',
  sessionId: '507f1f77bcf86cd799439012',
  agentKey: 'research-agent',
} as HarnessRun;

/**
 * The one read of the session a write makes: `row` is what it finds (with no
 * history ledger the write is not accounted). Its inserts and its one update
 * of the session are recorded. The store tests on a replica set
 * (harness-conversation-writer.mongo.itest.ts) say what is stored.
 */
const session = (row: Record<string, unknown> | null) => {
  const find = sinon.stub(ChatSession, 'findOne').returns({
    select: () => ({ session: () => ({ lean: sinon.stub().resolves(row) }) }),
  } as never);
  const update = sinon.stub(ChatSession, 'updateOne').resolves({} as never);
  const insert = sinon.stub(ChatSessionMessage, 'insertMany').resolves([] as never);
  const rows = () =>
    (insert.firstCall.args[0] as unknown as Array<{ toObject(): Record<string, unknown> }>).map((message) =>
      message.toObject(),
    );
  return { find, update, insert, rows };
};

const stored = { _id: run.sessionId, orgId: run.orgId, nextSeq: 3, rev: 7 };
const COLLABORATOR = '507f191e810c19729de860eb';

/** The session write a call made: its one update's second argument. */
const sessionWrite = (update: sinon.SinonStub) =>
  update.firstCall.args[1] as { $set?: Record<string, unknown>; $inc?: Record<string, unknown>; $push?: Record<string, unknown> };

describe('HarnessConversationWriter', () => {
  afterEach(() => sinon.restore());

  it('projects run status onto the owner session inside the transaction', async () => {
    const update = sinon.stub(ChatSession, 'updateOne').resolves({} as never);
    const tx = { id: 'tx' };
    await new HarnessConversationWriter().setConversationStatus(
      run,
      'Failed',
      42,
      tx as never,
    );
    expect(update.firstCall.args).to.deep.equal([
      { _id: run.sessionId, orgId: run.orgId, userId: run.actorId },
      { $set: { status: 'Failed', lastActivityAt: 42 } },
      { session: tx },
    ]);
  });

  it('reports a missing durable session instead of saving the answer', async () => {
    const { find, update, insert } = session(null);
    const saved = await new HarnessConversationWriter().saveCompletedConversation(
      run,
      { answer: 'done' },
      {} as never,
    );
    expect(saved).to.equal(false);
    expect(find.firstCall.args[0]).to.deep.include({
      _id: run.sessionId,
      orgId: run.orgId,
      userId: run.actorId,
      agentKey: 'research-agent',
      sessionType: 'agent',
      harnessVersion: 'durable-v1',
      isDeleted: false,
    });
    expect(insert.called).to.equal(false);
    expect(update.called).to.equal(false);
  });

  it("appends a collaborator's user turn at the session's next seq and returns its id", async () => {
    const { update, rows } = session(stored);
    const sender = '507f191e810c19729de860eb';
    const tx = { id: 'tx' };
    const id = await new HarnessConversationWriter().appendUserTurn(
      { orgId: run.orgId, sessionId: run.sessionId } as never,
      { query: 'what changed?', chatMode: 'quick', attachments: [], senderId: sender },
      tx as never,
    );
    const [row] = rows();
    expect(String(row!._id)).to.equal(id);
    // Stamped as the legacy row writer stamps a row: the session's next change, which the same write takes.
    expect(row).to.deep.include({ messageType: 'user_query', content: 'what changed?', seq: 4, changeRev: 8 });
    expect(String(row!.sessionId)).to.equal(run.sessionId);
    expect(String(row!.senderId)).to.equal(sender);
    expect(update.firstCall.args).to.deep.equal([{ _id: run.sessionId }, { $inc: { nextSeq: 1, rev: 1 } }, { session: tx }]);
  });

  it('stores an answer as the legacy row writer does: each row stamped from the session, the answer sealed', async () => {
    const { update, rows } = session(stored);
    await new HarnessConversationWriter().saveRunTranscript(
      run,
      {
        kind: 'answer',
        completion: { answer: 'Twenty days.', citations: [] },
        questions: [{ toolData: { questions: [{ question: 'Which country?' }] } }],
      } as never,
      {} as never,
    );
    const written = rows();
    expect(written.map((r) => [r.messageType, r.seq, r.changeRev])).to.deep.equal([
      ['tool_call', 4, 8],
      ['bot_response', 5, 9],
    ]);
    // A fresh version of the answer's text, which history names it by; a card has none.
    expect(written[1]!.labelVer).to.match(/^[0-9a-f]{24}$/);
    expect(written[0]).to.not.have.property('labelVer');
    expect(sessionWrite(update).$inc).to.deep.equal({ nextSeq: 2, rev: 2 });
  });

  it("leaves the sender off the owner's own turn", async () => {
    const { rows } = session(stored);
    await new HarnessConversationWriter().appendUserTurn(
      { orgId: run.orgId, sessionId: run.sessionId } as never,
      { query: 'q', chatMode: 'quick', attachments: [] },
      {} as never,
    );
    expect(rows()[0]).not.to.have.property('senderId');
  });

  it('refuses a user turn for a session that is gone, as allocating its seq did', async () => {
    session(null);
    const error = await new HarnessConversationWriter()
      .appendUserTurn({ orgId: run.orgId, sessionId: run.sessionId } as never, { query: 'q', chatMode: 'quick', attachments: [] }, {} as never)
      .catch((caught: unknown) => caught);
    expect((error as Error).name).to.equal('NotFoundError');
  });

  describe('a failed run (C-21)', () => {
    const input = { type: 'user_input', content: 'keep me', messageSeq: 1, runId: run.runId, consumed: false };

    it('keeps the steering messages with the error message', async () => {
      const { rows, update } = session(stored);
      const saved = await new HarnessConversationWriter().saveRunTranscript(
        run,
        { kind: 'failure', code: 'quarantined', message: 'It kept failing.', parts: [input] },
        {} as never,
      );
      expect(saved).to.equal(true);
      expect(rows()[0]).to.deep.include({ messageType: 'error', content: 'It kept failing.', parts: [input] });
      const [, write] = update.firstCall.args as unknown as [unknown, Record<string, Record<string, unknown>>];
      expect(write.$set).to.deep.include({ status: 'Failed', failReason: 'It kept failing.' });
      const errors = write.$push!.conversationErrors as { $each: Array<Record<string, unknown>> };
      expect(errors.$each[0]).to.deep.include({ message: 'It kept failing.', errorType: 'quarantined' });
    });

    it('writes the error message as before when the run left none', async () => {
      const { rows } = session(stored);
      await new HarnessConversationWriter().saveRunTranscript(
        run,
        { kind: 'failure', code: 'quarantined', message: 'It kept failing.' },
        {} as never,
      );
      // The schema's empty array, as an insert of the legacy message stored it.
      expect(rows()[0]!.parts).to.deep.equal([]);
    });

    it("records whose run failed, and the owner's reason with who asked, kept with the session's newest", async () => {
      const { update, rows } = session(stored);
      await new HarnessConversationWriter().saveRunTranscript(
        run,
        { kind: 'failure', code: 'quarantined', message: 'It kept failing.' },
        {} as never,
      );
      const write = sessionWrite(update);
      expect(String(write.$set!.failedRunBy)).to.equal(run.actorId);
      const errors = write.$push!.conversationErrors as { $each: Array<Record<string, unknown>>; $slice: number };
      expect(String(errors.$each[0]!.requestedBy)).to.equal(run.actorId);
      expect(errors.$slice).to.equal(-50);
      // A push no longer moves the version: the legacy writer's update does not.
      expect(write.$inc).to.deep.equal({ nextSeq: 1, rev: 1 });
      expect(rows()[0]).to.deep.include({ changeRev: 8 });
      expect(rows()[0]!.labelVer).to.match(/^[0-9a-f]{24}$/);
    });

    it("keeps a collaborator's reason on its error message: the conversation says only whose run failed", async () => {
      const { update, rows } = session(stored);
      await new HarnessConversationWriter().saveRunTranscript(
        { ...run, actorId: COLLABORATOR, sessionRole: 'collaborator' } as HarnessRun,
        { kind: 'failure', code: 'quarantined', message: 'It kept failing.' },
        {} as never,
      );
      const write = sessionWrite(update);
      expect(write.$set).to.deep.include({ status: 'Failed' });
      expect(String(write.$set!.failedRunBy)).to.equal(COLLABORATOR);
      expect(write.$set).to.not.have.property('failReason');
      expect(write).to.not.have.property('$push');
      expect(rows()[0]).to.deep.include({ messageType: 'error', content: 'It kept failing.' });
    });

    it('an answer classified as a failure records it as a failed run does, the reason the owner’s only', async () => {
      const failing = { answer: 'Model error.', answerMatchType: 'Error', errorCode: 'model_failed', citations: [] };
      const owner = session(stored);
      await new HarnessConversationWriter().saveCompletedConversation(run, failing, {} as never);
      const own = sessionWrite(owner.update);
      expect(own.$set).to.deep.include({ status: 'Failed', failReason: 'Model error.' });
      expect(String(own.$set!.failedRunBy)).to.equal(run.actorId);
      const errors = own.$push!.conversationErrors as { $each: Array<Record<string, unknown>> };
      expect(String(errors.$each[0]!.requestedBy)).to.equal(run.actorId);
      sinon.restore();

      const other = session(stored);
      await new HarnessConversationWriter().saveCompletedConversation(
        { ...run, actorId: COLLABORATOR, sessionRole: 'collaborator' } as HarnessRun,
        failing,
        {} as never,
      );
      const theirs = sessionWrite(other.update);
      expect(theirs.$set).to.deep.include({ status: 'Failed' });
      expect(String(theirs.$set!.failedRunBy)).to.equal(COLLABORATOR);
      expect(theirs.$set).to.not.have.property('failReason');
      expect(theirs).to.not.have.property('$push');
    });

    it('LC2FIX: the error message names its run and code, so the chat stays on the new harness and words why', async () => {
      const { rows } = session(stored);
      await new HarnessConversationWriter().saveRunTranscript(
        run,
        { kind: 'failure', code: 'budget_exhausted', message: 'This task reached its spending limit and was stopped.' },
        {} as never,
      );
      expect(rows()[0]).to.deep.include({ messageType: 'error', runId: run.runId, errorCode: 'budget_exhausted' });
    });
  });
});

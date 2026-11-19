/**
 * Compare models branches against a real MongoDB. Skipped unless FORK_TEST_MONGO_URI is set
 * (any reachable MongoDB; each run uses its own database and drops it afterwards).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import * as drain from '../../../../src/libs/repositories/drain';
import { getConversationRepository } from '../../../../src/libs/repositories/conversation';
import { asAccount } from '../../../../src/libs/context/orgContext';
import {
  chooseCompare,
  createCompareBranchHandler,
  listCompareBranches,
  purgeBranchArtifacts,
} from '../../../../src/modules/enterprise_search/controller/compare.controller';

const URI = process.env.FORK_TEST_MONGO_URI;
const describeIfMongo = URI ? describe : describe.skip;
const oid = () => new mongoose.Types.ObjectId();

interface Result {
  status?: number;
  body?: any;
  error?: { statusCode?: number; message?: string };
}

const call = async (
  handler: (req: any, res: any, next: any) => Promise<void>,
  user: { userId: string; orgId: string },
  params: Record<string, string>,
  body: Record<string, unknown> = {},
): Promise<Result> => {
  const out: Result = {};
  const res = {
    headersSent: false,
    status(code: number) {
      out.status = code;
      return {
        json: (b: unknown) => (out.body = b),
        set: () => ({ json: () => undefined }),
      };
    },
  };
  await asAccount(user.orgId, user.userId, 'test', () =>
    handler(
      { user, params, body, context: { requestId: 'r' }, headers: {} },
      res,
      (e: any) => (out.error = e),
    ),
  );
  return out;
};

describeIfMongo('Compare models branches on a real MongoDB', function () {
  this.timeout(60000);
  const orgId = oid();
  const owner = oid();
  const stranger = oid();
  const me = { userId: String(owner), orgId: String(orgId) };
  const other = { userId: String(stranger), orgId: String(orgId) };
  const repo = () => getConversationRepository();

  before(async () => {
    await mongoose.connect(URI as string, {
      dbName: `compare_test_${Date.now()}`,
    });
    await ChatSession.syncIndexes();
  });
  after(async () => {
    await mongoose.connection.dropDatabase();
    await mongoose.disconnect();
  });
  beforeEach(() => {
    sinon.stub(drain, 'refuseIfDraining').resolves(false);
  });
  afterEach(() => sinon.restore());

  /** Two finished turns (user, tool_call, bot each, except turn 1 has no tool) and a third question. */
  const seedChat = async (extra: Record<string, unknown> = {}) => {
    const session = await ChatSession.create({
      sessionType: 'chat',
      userId: owner,
      initiator: owner,
      orgId,
      title: 'Quarterly numbers',
      status: 'Complete',
      modelInfo: { modelKey: 'm-a', modelName: 'model-a' },
      ...extra,
    });
    const rows = [
      { messageType: 'user_query', content: 'Q1' },
      {
        messageType: 'bot_response',
        content: 'A1',
        modelInfo: { modelKey: 'm-a' },
      },
      { messageType: 'user_query', content: 'Q2' },
      { messageType: 'tool_call', content: 'tool', tools: [] },
      {
        messageType: 'bot_response',
        content: 'A2-model-a',
        modelInfo: { modelKey: 'm-a' },
      },
      { messageType: 'user_query', content: 'Q3' },
      {
        messageType: 'bot_response',
        content: 'A3',
        modelInfo: { modelKey: 'm-a' },
      },
    ].map((r, i) => ({ ...r, seq: i + 1, sessionId: session._id, orgId }));
    const inserted = await ChatSessionMessage.insertMany(rows, {
      ordered: true,
    });
    await ChatSession.updateOne(
      { _id: session._id },
      { $set: { nextSeq: rows.length } },
    );
    return { session, ids: inserted.map((m) => String(m._id)) };
  };

  const branchOf = async (parent: string, question: string, who = me) => {
    const r = await call(
      createCompareBranchHandler(),
      who,
      { conversationId: parent },
      { userMessageId: question },
    );
    return r;
  };

  /** What addMessageStream leaves in a branch once the second model answered Q2. */
  const answerInBranch = async (branchId: string, citationId = oid()) => {
    await repo().appendMessages(branchId, String(orgId), [
      { messageType: 'user_query', content: 'Q2' },
      {
        messageType: 'tool_call',
        content: 'tool',
        tools: [{ toolName: 'search' }],
      } as never,
      {
        messageType: 'bot_response',
        content: 'A2-model-b',
        modelInfo: { modelKey: 'm-b', modelName: 'model-b' },
        citations: [{ citationId: String(citationId), relevanceScore: 0.5 }],
        parts: [{ type: 'text', text: 'x' }],
      } as never,
    ]);
    await ChatSession.updateOne(
      { _id: branchId },
      {
        $set: {
          status: 'Complete',
          modelInfo: { modelKey: 'm-b', modelName: 'model-b' },
        },
      },
    );
    return citationId;
  };

  it('creates a hidden branch holding only the thread before the question', async () => {
    const { session, ids } = await seedChat();
    const r = await branchOf(String(session._id), ids[2]!);
    expect(r.status).to.equal(200);
    const branchId = r.body.branchConversationId as string;
    const rows = await ChatSessionMessage.find({ sessionId: branchId })
      .sort({ seq: 1 })
      .lean();
    expect(rows.map((m) => m.content)).to.deep.equal(['Q1', 'A1']);
    const b = await ChatSession.findById(branchId).lean();
    expect(String(b!.compareOf)).to.equal(String(session._id));
    expect(String(b!.compareFromMessageId)).to.equal(ids[2]);
    expect(String(b!.userId)).to.equal(String(owner));
    expect(String(b!.orgId)).to.equal(String(orgId));
    expect(b!.isShared).to.equal(false);
    // The parent is untouched.
    expect(
      await ChatSessionMessage.countDocuments({ sessionId: session._id }),
    ).to.equal(7);
  });

  it('works for the first question of a new chat (empty prefix) and inherits the project', async () => {
    const projectId = oid();
    const { session, ids } = await seedChat({
      projectId,
      projectVisibility: 'project',
    });
    const r = await branchOf(String(session._id), ids[0]!);
    expect(r.status).to.equal(200);
    const b = await ChatSession.findById(r.body.branchConversationId).lean();
    expect(
      await ChatSessionMessage.countDocuments({ sessionId: b!._id }),
    ).to.equal(0);
    expect(String(b!.projectId)).to.equal(String(projectId));
    // A branch is never shared with the project, whatever the parent's visibility.
    expect(b!.projectVisibility).to.equal('private');
  });

  it("refuses what is not the caller's, not a question, or an agent chat", async () => {
    const { session, ids } = await seedChat();
    const strangerTry = await branchOf(String(session._id), ids[2]!, other);
    expect(strangerTry.error?.statusCode).to.equal(404);
    expect(
      (await branchOf(String(session._id), ids[1]!)).error?.statusCode,
    ).to.equal(404);
    expect(
      (await branchOf(String(session._id), String(oid()))).error?.statusCode,
    ).to.equal(404);
    expect((await branchOf(String(oid()), ids[2]!)).error?.statusCode).to.equal(
      404,
    );
    // A message of another conversation is not a question of this one.
    const second = await seedChat();
    expect(
      (await branchOf(String(session._id), second.ids[2]!)).error?.statusCode,
    ).to.equal(404);
    const agent = await seedChat({ sessionType: 'agent', agentKey: 'agent-1' });
    expect(
      (await branchOf(String(agent.session._id), agent.ids[0]!)).error
        ?.statusCode,
    ).to.equal(400);
    const ok = await branchOf(String(session._id), ids[2]!);
    const viaBranch = await branchOf(ok.body.branchConversationId, ids[2]!);
    expect(viaBranch.error?.statusCode).to.equal(404);
    expect(
      await ChatSession.countDocuments({ compareOf: session._id }),
    ).to.equal(1);
  });

  it('a new branch for the same question replaces the first', async () => {
    const { session, ids } = await seedChat();
    const first = await branchOf(String(session._id), ids[2]!);
    const second = await branchOf(String(session._id), ids[2]!);
    expect(second.status).to.equal(200);
    const left = await ChatSession.find({ compareOf: session._id }).lean();
    expect(left.map((s) => String(s._id))).to.deep.equal([
      second.body.branchConversationId,
    ]);
    expect(
      await ChatSessionMessage.countDocuments({
        sessionId: first.body.branchConversationId,
      }),
    ).to.equal(0);
  });

  it('lists branches for the owner only', async () => {
    const { session, ids } = await seedChat();
    expect(
      (
        await call(listCompareBranches(), me, {
          conversationId: String(session._id),
        })
      ).body,
    ).to.deep.equal({ branches: [] });
    const made = await branchOf(String(session._id), ids[2]!);
    const listed = await call(listCompareBranches(), me, {
      conversationId: String(session._id),
    });
    expect(listed.body.branches).to.deep.equal([
      {
        branchConversationId: made.body.branchConversationId,
        userMessageId: ids[2],
      },
    ]);
    expect(
      (
        await call(listCompareBranches(), other, {
          conversationId: String(session._id),
        })
      ).error?.statusCode,
    ).to.equal(404);
  });

  it('never shows a branch in a list, search, count, archive search or project listing, but opens it by id', async () => {
    const projectId = oid();
    const { session, ids } = await seedChat({
      projectId,
      projectVisibility: 'project',
      title: 'zebra planning',
    });
    const made = await branchOf(String(session._id), ids[2]!);
    const branchId = made.body.branchConversationId as string;
    const base = {
      orgId: String(orgId),
      userId: String(owner),
      scope: { kind: 'chat' },
      owned: true,
      shared: true,
      skip: 0,
      limit: 50,
      sort: { lastActivityAt: -1 },
    } as never;
    const page = await repo().listSessions(base);
    expect(page.rows.map((r) => r._id)).to.include(String(session._id));
    expect(page.rows.map((r) => r._id)).to.not.include(branchId);
    const searched = await repo().listSessions({
      ...(base as object),
      search: 'zebra',
      contentMatchIds: [branchId],
    } as never);
    expect(searched.rows.map((r) => r._id)).to.not.include(branchId);
    const summaries = await repo().listVisibleSummaries(
      String(orgId),
      String(owner),
      [branchId, String(session._id)],
      [],
    );
    expect(summaries.map((r) => r._id)).to.deep.equal([String(session._id)]);
    const byProject = await repo().listByProject({
      orgId: String(orgId),
      projectId: String(projectId),
      userId: String(owner),
      skip: 0,
      limit: 50,
    });
    expect(byProject.rows.map((r) => r._id)).to.deep.equal([
      String(session._id),
    ]);
    expect(byProject.total).to.equal(1);
    const counts = await repo().countByProject(String(orgId), String(owner), [
      String(projectId),
    ]);
    expect(counts.get(String(projectId))).to.equal(1);
    const summary = await repo().findVisibleSummary({
      ...(base as object),
      id: branchId,
    } as never);
    expect(summary?._id).to.equal(branchId);
    await repo().setArchived({ kind: 'chat' }, branchId, { by: String(owner) });
    const archived = await repo().searchArchived({
      orgId: String(orgId),
      userId: String(owner),
      escapedSearch: 'zebra',
      contentMatchIds: [branchId],
      deletedAgentKeys: null,
      skip: 0,
      limit: 10,
    });
    expect(archived.rows.map((r: any) => String(r._id))).to.not.include(
      branchId,
    );
    expect(
      (
        await repo().findOwned({ kind: 'chat' }, branchId, {
          orgId: String(orgId),
          userId: String(owner),
        })
      )?._id,
    ).to.equal(branchId);
    expect(
      await repo().findOwned({ kind: 'chat' }, branchId, {
        orgId: String(orgId),
        userId: String(stranger),
      }),
    ).to.equal(null);
  });

  it('choosing primary deletes the branches, and again does nothing', async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[2]!);
    const params = { conversationId: String(session._id) };
    expect(
      (await call(chooseCompare(), me, params, { choice: 'primary' })).body,
    ).to.deep.equal({ ok: true });
    expect(
      await ChatSession.countDocuments({ compareOf: session._id }),
    ).to.equal(0);
    expect(await ChatSession.findById(made.body.branchConversationId)).to.equal(
      null,
    );
    expect(
      (await call(chooseCompare(), me, params, { choice: 'primary' })).body,
    ).to.deep.equal({ ok: true });
    expect(
      (await call(chooseCompare(), other, params, { choice: 'primary' })).error
        ?.statusCode,
    ).to.equal(404);
    expect(
      await ChatSessionMessage.countDocuments({ sessionId: session._id }),
    ).to.equal(7);
  });

  it("choosing a branch makes its turn the conversation's turn", async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[2]!);
    const branchId = made.body.branchConversationId as string;
    const citationId = await answerInBranch(branchId);

    const r = await call(
      chooseCompare(),
      me,
      { conversationId: String(session._id) },
      { choice: branchId },
    );
    expect(r.body).to.deep.equal({ ok: true });

    const rows = await ChatSessionMessage.find({ sessionId: session._id })
      .sort({ seq: 1 })
      .lean();
    expect(rows.map((m) => m.content)).to.deep.equal([
      'Q1',
      'A1',
      'Q2',
      'tool',
      'A2-model-b',
    ]);
    expect(rows.map((m) => m.messageType)).to.deep.equal([
      'user_query',
      'bot_response',
      'user_query',
      'tool_call',
      'bot_response',
    ]);
    const seqs = rows.map((m) => m.seq);
    expect([...seqs].sort((a, b) => a - b)).to.deep.equal(seqs);
    expect(new Set(seqs).size).to.equal(seqs.length);
    const answer = rows[4]!;
    expect(answer.modelInfo?.modelKey).to.equal('m-b');
    expect(String(answer.citations![0]!.citationId)).to.equal(
      String(citationId),
    );
    expect(answer.parts).to.have.length(1);
    expect((rows[3] as any).tools[0].toolName).to.equal('search');
    // The parent keeps the id of the compared question's first row only if it is the earlier prefix.
    expect(String(rows[0]!._id)).to.equal(ids[0]);

    const after = await ChatSession.findById(session._id)
      .select('+nextSeq')
      .lean();
    expect(after!.status).to.equal('Complete');
    expect(after!.modelInfo?.modelKey).to.equal('m-b');
    expect(after!.title).to.equal('Quarterly numbers');
    expect(await ChatSession.findById(branchId)).to.equal(null);
    expect(
      await ChatSessionMessage.countDocuments({ sessionId: branchId }),
    ).to.equal(0);

    // A follow-up lands after the chosen turn.
    const [next] = await repo().appendMessages(
      String(session._id),
      String(orgId),
      [{ messageType: 'user_query', content: 'Q-next' }],
    );
    expect(next!.seq).to.be.greaterThan(Math.max(...seqs));

    // Repeating the call finds no branch left to choose from.
    expect(
      (
        await call(
          chooseCompare(),
          me,
          { conversationId: String(session._id) },
          { choice: branchId },
        )
      ).body,
    ).to.deep.equal({ ok: true, already: true });
    expect(
      await ChatSessionMessage.countDocuments({ sessionId: session._id }),
    ).to.equal(6);
  });

  it('follows the question into a first-turn choice', async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[0]!);
    const branchId = made.body.branchConversationId as string;
    await repo().appendMessages(branchId, String(orgId), [
      { messageType: 'user_query', content: 'Q1' },
      {
        messageType: 'bot_response',
        content: 'A1-model-b',
        modelInfo: { modelKey: 'm-b' },
      } as never,
    ]);
    expect(
      (
        await call(
          chooseCompare(),
          me,
          { conversationId: String(session._id) },
          { choice: branchId },
        )
      ).status,
    ).to.equal(200);
    const rows = await ChatSessionMessage.find({ sessionId: session._id })
      .sort({ seq: 1 })
      .lean();
    expect(rows.map((m) => m.content)).to.deep.equal(['Q1', 'A1-model-b']);
  });

  it('refuses a choice while either side is still running, but not while only waiting', async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[2]!);
    const branchId = made.body.branchConversationId as string;
    await answerInBranch(branchId);
    const params = { conversationId: String(session._id) };

    await ChatSession.updateOne(
      { _id: branchId },
      { $set: { status: 'Inprogress', lastActivityAt: Date.now() } },
    );
    expect(
      (await call(chooseCompare(), me, params, { choice: branchId })).error
        ?.statusCode,
    ).to.equal(409);
    await ChatSession.updateOne(
      { _id: branchId },
      { $set: { status: 'Complete' } },
    );

    await ChatSession.updateOne(
      { _id: session._id },
      { $set: { status: 'Inprogress', lastActivityAt: Date.now() } },
    );
    expect(
      (await call(chooseCompare(), me, params, { choice: branchId })).error
        ?.statusCode,
    ).to.equal(409);
    // The refusal leaves the branch run state as it was.
    expect((await ChatSession.findById(branchId).lean())!.status).to.equal(
      'Complete',
    );
    expect(
      await ChatSessionMessage.countDocuments({ sessionId: session._id }),
    ).to.equal(7);

    // A parent parked on a question card is not running.
    await ChatSession.updateOne(
      { _id: session._id },
      { $set: { status: 'Complete' } },
    );
    expect(
      (await call(chooseCompare(), me, params, { choice: branchId })).status,
    ).to.equal(200);
  });

  it("refuses a branch that has no answer yet, and a branch id of someone else's or another chat", async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[2]!);
    const params = { conversationId: String(session._id) };
    expect(
      (
        await call(chooseCompare(), me, params, {
          choice: made.body.branchConversationId,
        })
      ).error?.statusCode,
    ).to.equal(409);
    expect(
      (await call(chooseCompare(), me, params, { choice: String(oid()) })).error
        ?.statusCode,
    ).to.equal(404);
    const unrelated = await seedChat();
    const foreign = await branchOf(
      String(unrelated.session._id),
      unrelated.ids[2]!,
    );
    expect(
      (
        await call(chooseCompare(), me, params, {
          choice: foreign.body.branchConversationId,
        })
      ).error?.statusCode,
    ).to.equal(404);
    expect(
      (
        await call(chooseCompare(), other, params, {
          choice: made.body.branchConversationId,
        })
      ).error?.statusCode,
    ).to.equal(404);
  });

  it('two choices at once leave one correct result', async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[2]!);
    const branchId = made.body.branchConversationId as string;
    await answerInBranch(branchId);
    const params = { conversationId: String(session._id) };
    const results = await Promise.all([
      call(chooseCompare(), me, params, { choice: branchId }),
      call(chooseCompare(), me, params, { choice: branchId }),
    ]);
    expect(results.filter((r) => r.status === 200)).to.have.length(1);
    expect(
      results.filter(
        (r) => r.error && [404, 409].includes(r.error.statusCode ?? 0),
      ),
    ).to.have.length(1);
    const rows = await ChatSessionMessage.find({ sessionId: session._id })
      .sort({ seq: 1 })
      .lean();
    expect(rows.map((m) => m.content)).to.deep.equal([
      'Q1',
      'A1',
      'Q2',
      'tool',
      'A2-model-b',
    ]);
  });

  it('finishes a half-done choice when repeated', async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[2]!);
    const branchId = made.body.branchConversationId as string;
    await answerInBranch(branchId);
    // The rows were swapped but the branch survived (a crash before the last write).
    const stub = sinon
      .stub(repo(), 'discardBranches')
      .rejects(new Error('boom'));
    const params = { conversationId: String(session._id) };
    expect(
      (await call(chooseCompare(), me, params, { choice: branchId })).error
        ?.message,
    ).to.equal('boom');
    stub.restore();
    // Without a transaction the swap stayed; the repeat produces the same end state, not duplicates.
    expect(
      (await call(chooseCompare(), me, params, { choice: branchId })).status,
    ).to.equal(200);
    const rows = await ChatSessionMessage.find({ sessionId: session._id })
      .sort({ seq: 1 })
      .lean();
    expect(rows.map((m) => m.content)).to.deep.equal([
      'Q1',
      'A1',
      'Q2',
      'tool',
      'A2-model-b',
    ]);
    expect((await ChatSession.findById(session._id).lean())!.status).to.equal(
      'Complete',
    );
  });

  describe('artifacts of a branch', () => {
    const marker = (id: string) =>
      `::artifact[a.md](record:${id}){text/markdown|doc-${id}|${id}|document|1}`;
    const fakeArtifacts = (failed = false) => {
      const copied: { to: string; ids: string[] }[] = [];
      const removed: string[] = [];
      const removedOne: string[] = [];
      return {
        copied,
        removed,
        removedOne,
        artifacts: {
          copier: () => async (to: string, refs: { artifactId: string }[]) => {
            copied.push({ to, ids: refs.map((r) => r.artifactId) });
            return failed
              ? {
                  copies: [],
                  failed: refs.map((r) => ({
                    artifactId: r.artifactId,
                    reason: 'error' as const,
                  })),
                }
              : {
                  copies: refs.map((r) => ({
                    sourceArtifactId: r.artifactId,
                    sourceDocumentId: `doc-${r.artifactId}`,
                    artifactId: 'zzz9',
                    documentId: 'doc-zzz9',
                    versions: [{ from: 1, to: 1 }],
                  })),
                  failed: [],
                };
          },
          remove: (id: string) => {
            removed.push(id);
            return Promise.resolve();
          },
          removeOne: (id: string) => {
            removedOne.push(id);
            return Promise.resolve();
          },
        },
      };
    };
    const answerWithArtifact = async (branchId: string) => {
      await repo().appendMessages(branchId, String(orgId), [
        { messageType: 'user_query', content: 'Q2' },
        {
          messageType: 'bot_response',
          content: `made ${marker('art1')}`,
          modelInfo: { modelKey: 'm-b' },
        } as never,
      ]);
      await ChatSession.updateOne(
        { _id: branchId },
        { $set: { status: 'Complete' } },
      );
    };
    const setup = async (fake: ReturnType<typeof fakeArtifacts>) => {
      const { session, ids } = await seedChat();
      const made = await call(
        createCompareBranchHandler(fake.artifacts),
        me,
        { conversationId: String(session._id) },
        { userMessageId: ids[2]! },
      );
      const branchId = made.body.branchConversationId as string;
      await answerWithArtifact(branchId);
      return { session, branchId };
    };

    it('re-files the chosen turn artifacts under the parent, then drops the branch ones', async () => {
      const fake = fakeArtifacts();
      const { session, branchId } = await setup(fake);
      await call(
        chooseCompare(fake.artifacts),
        me,
        { conversationId: String(session._id) },
        { choice: branchId },
      );
      expect(fake.copied).to.deep.equal([
        { to: String(session._id), ids: ['art1'] },
      ]);
      const last = await ChatSessionMessage.findOne({
        sessionId: session._id,
      })
        .sort({ seq: -1 })
        .lean();
      expect(last!.content).to.contain('record:zzz9');
      expect(last!.content).to.not.contain('record:art1)');
      expect(fake.removed).to.deep.equal([branchId]);
    });

    it('keeps the branch artifacts when they could not be copied', async () => {
      const fake = fakeArtifacts(true);
      const { session, branchId } = await setup(fake);
      await call(
        chooseCompare(fake.artifacts),
        me,
        { conversationId: String(session._id) },
        { choice: branchId },
      );
      expect(fake.removed).to.deep.equal([]);
    });

    it('choosing primary deletes only the branch artifacts, never the parent', async () => {
      const fake = fakeArtifacts();
      const { session, branchId } = await setup(fake);
      await call(
        chooseCompare(fake.artifacts),
        me,
        { conversationId: String(session._id) },
        { choice: 'primary' },
      );
      expect(fake.copied).to.deep.equal([]);
      expect(fake.removed).to.deep.equal([branchId]);
    });

    it('sweeps the owner branches older than a day when a new branch is made', async () => {
      const fake = fakeArtifacts();
      const old = await seedChat();
      const oldBranch = (
        await call(
          createCompareBranchHandler(fake.artifacts),
          me,
          { conversationId: String(old.session._id) },
          { userMessageId: old.ids[2]! },
        )
      ).body.branchConversationId as string;
      await ChatSession.collection.updateOne(
        { _id: new mongoose.Types.ObjectId(oldBranch) },
        {
          $set: {
            createdAt: new Date(Date.now() - 25 * 3600 * 1000),
            lastActivityAt: Date.now() - 25 * 3600 * 1000,
          },
        },
      );
      const fresh = await seedChat();
      const freshBranch = (
        await call(
          createCompareBranchHandler(fake.artifacts),
          me,
          { conversationId: String(fresh.session._id) },
          { userMessageId: fresh.ids[2]! },
        )
      ).body.branchConversationId as string;
      expect(await ChatSession.findById(oldBranch)).to.equal(null);
      expect(
        await ChatSessionMessage.countDocuments({ sessionId: oldBranch }),
      ).to.equal(0);
      expect(await ChatSession.findById(freshBranch)).to.not.equal(null);
      expect(await ChatSession.findById(old.session._id)).to.not.equal(null);
      expect(fake.removed).to.deep.equal([oldBranch]);
    });

    it('deletes what the replaced turn made, but not what the kept thread also shows', async () => {
      const fake = fakeArtifacts();
      const { session, ids } = await seedChat();
      await ChatSessionMessage.updateOne(
        { _id: ids[1] },
        { $set: { content: `kept ${marker('pre')}` } },
      );
      await ChatSessionMessage.updateOne(
        { _id: ids[4] },
        { $set: { content: `${marker('old1')} ${marker('pre')}` } },
      );
      const made = await call(
        createCompareBranchHandler(fake.artifacts),
        me,
        { conversationId: String(session._id) },
        { userMessageId: ids[2]! },
      );
      const branchId = made.body.branchConversationId as string;
      await answerWithArtifact(branchId);
      await call(
        chooseCompare(fake.artifacts),
        me,
        { conversationId: String(session._id) },
        { choice: branchId },
      );
      expect(fake.removedOne).to.deep.equal(['old1']);
    });

    it('removes the branch artifacts when the parent is archived or deleted', async () => {
      const fake = fakeArtifacts();
      const { session, branchId } = await setup(fake);
      const res = await call(
        async (req, _res, next) =>
          purgeBranchArtifacts(fake.artifacts)(req, _res, next),
        me,
        { conversationId: String(session._id) },
      );
      expect(res.error).to.equal(undefined);
      expect(fake.removed).to.deep.equal([branchId]);
    });
  });

  it('a sweep keeps an old branch that was touched recently, and one still running', async () => {
    const a = await seedChat();
    const made = await branchOf(String(a.session._id), a.ids[2]!);
    await ChatSession.collection.updateOne(
      { _id: new mongoose.Types.ObjectId(made.body.branchConversationId) },
      { $set: { createdAt: new Date(Date.now() - 48 * 3600 * 1000) } },
    );
    const b = await seedChat();
    await branchOf(String(b.session._id), b.ids[2]!);
    expect(
      await ChatSession.findById(made.body.branchConversationId),
    ).to.not.equal(null);
  });

  it('a new branch keeps one that another tab is still answering, and replaces an idle one', async () => {
    const { session, ids } = await seedChat();
    const first = await branchOf(String(session._id), ids[2]!);
    await ChatSession.updateOne(
      { _id: first.body.branchConversationId },
      { $set: { status: 'Inprogress', lastActivityAt: Date.now() } },
    );
    const second = await branchOf(String(session._id), ids[2]!);
    // The live branch holds the only slot, so the newcomer is refused rather than the live one deleted.
    expect(second.error?.statusCode).to.equal(409);
    expect(
      await ChatSession.findById(first.body.branchConversationId),
    ).to.not.equal(null);
    await ChatSession.updateOne(
      { _id: first.body.branchConversationId },
      { $set: { lastActivityAt: Date.now() - 3600 * 1000 } },
    );
    const third = await branchOf(String(session._id), ids[2]!);
    expect(third.status).to.equal(200);
    expect(
      await ChatSession.findById(first.body.branchConversationId),
    ).to.equal(null);
  });

  it('choosing a branch again after it was consumed answers ok, but an unknown id next to real branches is a 404', async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[2]!);
    const branchId = made.body.branchConversationId as string;
    await answerInBranch(branchId);
    const params = { conversationId: String(session._id) };
    await call(chooseCompare(), me, params, { choice: branchId });
    const again = await call(chooseCompare(), me, params, { choice: branchId });
    expect(again.status).to.equal(200);
    expect(again.body).to.deep.equal({ ok: true, already: true });
    const q = await ChatSessionMessage.findOne({
      sessionId: session._id,
      messageType: 'user_query',
      content: 'Q2',
    }).lean();
    await branchOf(String(session._id), String(q!._id));
    const unknown = await call(chooseCompare(), me, params, {
      choice: String(oid()),
    });
    expect(unknown.error?.statusCode).to.equal(404);
  });

  it('a stopped branch keeps its saved partial answer and can be chosen', async () => {
    const { session, ids } = await seedChat();
    const made = await branchOf(String(session._id), ids[2]!);
    const branchId = made.body.branchConversationId as string;
    await repo().appendMessages(branchId, String(orgId), [
      { messageType: 'user_query', content: 'Q2' },
      {
        messageType: 'bot_response',
        content: 'half',
        status: 'stopped',
      } as never,
    ]);
    await ChatSession.updateOne(
      { _id: branchId },
      { $set: { status: 'Stopped' } },
    );
    const r = await call(
      chooseCompare(),
      me,
      { conversationId: String(session._id) },
      { choice: branchId },
    );
    expect(r.status).to.equal(200);
    const last = await ChatSessionMessage.findOne({ sessionId: session._id })
      .sort({ seq: -1 })
      .lean();
    expect(last!.content).to.equal('half');
  });

  it('deleting or archiving the parent removes its branches', async () => {
    const a = await seedChat();
    await branchOf(String(a.session._id), a.ids[2]!);
    await repo().softDelete(String(a.session._id), {
      orgId: String(orgId),
      userId: String(owner),
    });
    expect(
      await ChatSession.countDocuments({ compareOf: a.session._id }),
    ).to.equal(0);

    const b = await seedChat();
    const made = await branchOf(String(b.session._id), b.ids[2]!);
    await repo().setArchived({ kind: 'chat' }, String(b.session._id), {
      by: String(owner),
    });
    expect(
      await ChatSession.countDocuments({ compareOf: b.session._id }),
    ).to.equal(0);
    expect(
      await ChatSessionMessage.countDocuments({
        sessionId: made.body.branchConversationId,
      }),
    ).to.equal(0);
  });

  it('leaves a conversation with no branches untouched by the extra field', async () => {
    const { session } = await seedChat();
    const row = await ChatSession.findById(session._id).lean();
    expect(row).to.not.have.property('compareOf');
    expect(row).to.not.have.property('compareFromMessageId');
  });
});

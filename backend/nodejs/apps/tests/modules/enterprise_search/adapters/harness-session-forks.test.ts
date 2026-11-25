import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema';
import { AgentSessionForkModel } from '../../../../src/libs/repositories/agent-harness/session-forks/schema/session-forks.schema';
import { HarnessSessionForkStore } from '../../../../src/libs/repositories/agent-harness/session-forks/mongo';
import { forkRecordId } from '../../../../src/modules/agent-harness/domain/session-forks';
import { ForkAlreadyCommittedError } from '../../../../src/modules/agent-harness/ports/session-forks';
import { EMPTY_LEDGER } from '../../../../src/modules/agent-harness/domain/session-import';

const ORG = '507f1f77bcf86cd799439011';
const SOURCE = '507f1f77bcf86cd799439012';
const ACTOR = '507f191e810c19729de860ea';
const FORK = '507f1f77bcf86cd799439013';
const KEY = { orgId: ORG, sourceSessionId: SOURCE, actorId: ACTOR, requestKey: 'k-1' };

const result = {
  sessionId: FORK,
  fromSessionId: SOURCE,
  atSeq: 2,
  role: 'owner',
  counts: { messages: 2, stubs: 0 },
} as never;

const chain = (resolved: unknown, methods: string[]) => {
  const query: Record<string, sinon.SinonStub> = {};
  for (const method of methods) query[method] = sinon.stub().returns(query);
  query.lean = sinon.stub().resolves(resolved);
  return query;
};

describe('HarnessSessionForkStore', () => {
  afterEach(() => sinon.restore());

  describe('records', () => {
    it("finds a request's record by its key, from the primary", async () => {
      const query = chain({ forkSessionId: FORK }, ['read']);
      const find = sinon.stub(AgentSessionForkModel, 'findById').returns(query as never);
      expect(await new HarnessSessionForkStore().find(KEY)).to.deep.equal({
        forkSessionId: FORK,
        result: null,
      });
      expect(find.firstCall.args[0]).to.equal(forkRecordId(KEY));
      expect(query.read!.firstCall.args[0]).to.equal('primary');

      query.lean!.resolves(null);
      expect(await new HarnessSessionForkStore().find(KEY)).to.equal(null);
    });

    it('begins a request once: an upsert that keeps the first chosen session id', async () => {
      const lean = sinon.stub().resolves({ forkSessionId: 'first', result });
      const upsert = sinon
        .stub(AgentSessionForkModel, 'findOneAndUpdate')
        .returns({ lean } as never);
      expect(await new HarnessSessionForkStore().begin(KEY, FORK, 7)).to.deep.equal({
        forkSessionId: 'first',
        result,
      });
      const [filter, update, options] = upsert.firstCall.args as unknown as [
        unknown,
        { $setOnInsert: Record<string, unknown> },
        Record<string, unknown>,
      ];
      expect(filter).to.deep.equal({ _id: forkRecordId(KEY) });
      expect(update.$setOnInsert).to.deep.include({
        orgId: ORG,
        sourceSessionId: SOURCE,
        actorId: ACTOR,
        forkSessionId: FORK,
        result: null,
        createdAt: 7,
        completedAt: null,
      });
      expect(options).to.deep.include({
        upsert: true,
        returnDocument: 'after',
        writeConcern: { w: 'majority' },
      });
    });
  });

  describe('source', () => {
    const scope = { orgId: ORG, actorId: ACTOR, sessionId: SOURCE };

    it('reads nothing for malformed ids', async () => {
      const find = sinon.stub(ChatSession, 'findOne');
      expect(
        await new HarnessSessionForkStore().source({ ...scope, actorId: 'x' }),
      ).to.equal(null);
      expect(find.called).to.equal(false);
    });

    it('answers null for a session not in the org or of another kind', async () => {
      const session = chain(null, ['select', 'read']);
      const find = sinon.stub(ChatSession, 'findOne').returns(session as never);
      const messages = sinon.stub(ChatSessionMessage, 'find');
      expect(await new HarnessSessionForkStore().source(scope)).to.equal(null);
      expect(find.firstCall.args[0]).to.deep.equal({
        _id: SOURCE,
        orgId: ORG,
        isDeleted: false,
        sessionType: 'chat',
      });
      expect(messages.called).to.equal(false);
    });

    it('copies what a fork keeps of each message and drops citations whose document is gone', async () => {
      const session = chain(
        {
          userId: new Types.ObjectId(ACTOR),
          isShared: true,
          sharedWith: [{ userId: 'u2', accessLevel: 'read' }],
          title: 'Q3 plan',
          modelInfo: { modelKey: 'm' },
          agentKey: 'research-agent',
          harnessVersion: 'durable-v1',
        },
        ['select', 'read'],
      );
      const find = sinon.stub(ChatSession, 'findOne').returns(session as never);
      const stored = [
        {
          messageType: 'user_query',
          senderId: new Types.ObjectId(ACTOR),
          content: 'why?',
          status: null,
          citations: [],
        },
        {
          messageType: 'bot_response',
          content: 42,
          contentFormat: 'MARKDOWN',
          confidence: 'High',
          followUpQuestions: [{ question: 'next?' }],
          _id: 'not copied',
          citations: [
            {
              citationId: {
                _id: 'c1',
                content: 'quoted',
                chunkIndex: 3,
                citationType: 'vectordb|document',
                metadata: { _id: 'meta-id', recordId: 'r1' },
                createdAt: 1,
              },
              relevanceScore: 0.9,
              excerpt: 'ex',
              context: 'ctx',
            },
            { citationId: null, relevanceScore: 0.1 },
            { citationId: { metadata: 'not an object' } },
            'not an object',
          ],
          label: { not: 'a label' },
        },
      ];
      const messageQuery = chain(stored, ['select', 'sort', 'populate', 'read']);
      const findMessages = sinon
        .stub(ChatSessionMessage, 'find')
        .returns(messageQuery as never);

      const source = await new HarnessSessionForkStore().source({
        ...scope,
        agentKey: 'research-agent',
      });

      expect(find.firstCall.args[0]).to.deep.equal({
        _id: SOURCE,
        orgId: ORG,
        isDeleted: false,
        agentKey: 'research-agent',
        sessionType: 'agent',
      });
      const [messageFilter] = findMessages.firstCall.args as unknown as [
        Record<string, Types.ObjectId>,
      ];
      expect(String(messageFilter.sessionId)).to.equal(SOURCE);
      expect(String(messageFilter.orgId)).to.equal(ORG);
      expect(messageQuery.sort!.firstCall.args[0]).to.deep.equal({ seq: 1 });

      expect(source).to.not.equal(null);
      expect(source!.participants).to.deep.equal({
        ownerId: ACTOR,
        isShared: true,
        shares: [{ userId: 'u2', accessLevel: 'read' }],
      });
      expect(source!).to.deep.include({
        sessionType: 'agent',
        agentKey: 'research-agent',
        title: 'Q3 plan',
        modelInfo: { modelKey: 'm' },
        durable: true,
      });
      expect(session.select!.firstCall.args[0]).to.include('+harnessVersion');
      const [question, answer] = source!.messages;
      expect(question).to.deep.equal({
        messageType: 'user_query',
        senderId: ACTOR,
        content: 'why?',
        citations: [],
        fields: {},
        label: null,
      });
      expect(answer!.senderId).to.equal(null);
      expect(answer!.content).to.equal('');
      expect(answer!.fields).to.deep.equal({
        contentFormat: 'MARKDOWN',
        confidence: 'High',
        followUpQuestions: [{ question: 'next?' }],
      });
      expect(answer!.citations).to.deep.equal([
        {
          data: {
            content: 'quoted',
            chunkIndex: 3,
            citationType: 'vectordb|document',
            metadata: { recordId: 'r1' },
          },
          relevanceScore: 0.9,
          excerpt: 'ex',
          context: 'ctx',
        },
        {
          data: { content: '', chunkIndex: 0, citationType: '', metadata: {} },
        },
      ]);
    });

    it('answers a chat with no title or model as empty', async () => {
      sinon
        .stub(ChatSession, 'findOne')
        .returns(chain({ userId: ACTOR }, ['select', 'read']) as never);
      sinon
        .stub(ChatSessionMessage, 'find')
        .returns(chain([], ['select', 'sort', 'populate', 'read']) as never);
      expect(await new HarnessSessionForkStore().source(scope)).to.deep.include({
        sessionType: 'chat',
        agentKey: null,
        title: '',
        modelInfo: null,
        messages: [],
      });
    });
  });

  describe('commit', () => {
    const data = (content: string) => ({
      content,
      chunkIndex: 0,
      citationType: 't',
      metadata: {},
    });

    const commit = (overrides: Record<string, unknown> = {}) => ({
      key: KEY,
      forkSessionId: FORK,
      source: {
        sessionType: 'agent' as const,
        agentKey: 'research-agent',
        title: 'Q3 plan',
        modelInfo: null,
      },
      messages: [
        {
          messageType: 'user_query',
          content: 'q',
          citations: [],
          fields: {},
          foreignTurn: true,
        },
        {
          messageType: 'bot_response',
          content: 'a',
          citations: [
            { data: data('one'), relevanceScore: 0.5 },
            { data: data('two') },
          ],
          fields: { status: 'Complete' },
          foreignTurn: false,
        },
      ],
      result,
      now: 99,
      ...overrides,
    });

    it('refuses a second commit of the same request before writing anything', async () => {
      sinon
        .stub(AgentSessionForkModel, 'updateOne')
        .resolves({ matchedCount: 0 } as never);
      const create = sinon.stub(ChatSession, 'create');
      let thrown: unknown;
      try {
        await new HarnessSessionForkStore().commit(commit() as never, {} as never);
      } catch (error) {
        thrown = error;
      }
      expect(thrown).to.be.instanceOf(ForkAlreadyCommittedError);
      expect(create.called).to.equal(false);
    });

    it("writes a private session owned by the forker, new citations, and messages linked to them in order", async () => {
      const tx = { id: 'tx' };
      const mark = sinon
        .stub(AgentSessionForkModel, 'updateOne')
        .resolves({ matchedCount: 1 } as never);
      const create = sinon.stub(ChatSession, 'create').resolves([] as never);
      const cited = [{ _id: 'new-1' }, { _id: 'new-2' }];
      const citations = sinon.stub(Citation, 'insertMany').resolves(cited as never);
      const messages = sinon
        .stub(ChatSessionMessage, 'insertMany')
        .resolves([] as never);

      await new HarnessSessionForkStore().commit(commit() as never, tx as never);

      expect(mark.firstCall.args).to.deep.equal([
        { _id: forkRecordId(KEY), result: null },
        { $set: { result, completedAt: 99 } },
        { session: tx },
      ]);
      const [[session], sessionOptions] = create.firstCall.args as unknown as [
        [Record<string, unknown>],
        unknown,
      ];
      expect(String(session._id)).to.equal(FORK);
      expect(String(session.orgId)).to.equal(ORG);
      expect(String(session.userId)).to.equal(ACTOR);
      expect(String(session.initiator)).to.equal(ACTOR);
      expect(session).to.deep.include({
        title: 'Q3 plan',
        isShared: false,
        sharedWith: [],
        lastActivityAt: 99,
        status: 'Complete',
        nextSeq: 2,
        agentKey: 'research-agent',
        sessionType: 'agent',
        conversationSource: 'agent_chat',
      });
      expect(session).to.not.have.property('modelInfo');
      expect(sessionOptions).to.deep.equal({ session: tx });

      expect(citations.firstCall.args).to.deep.equal([
        [data('one'), data('two')],
        { session: tx, ordered: true },
      ]);
      const [rows, rowOptions] = messages.firstCall.args as unknown as [
        Array<Record<string, unknown>>,
        unknown,
      ];
      expect(rowOptions).to.deep.equal({ session: tx, ordered: true });
      expect(rows.map((row) => row.seq)).to.deep.equal([1, 2]);
      expect(rows[0]).to.deep.include({ content: 'q', foreignTurn: true, citations: [] });
      expect(rows[1]).to.not.have.property('foreignTurn');
      // FORKFIX: a copied turn is marked so the web app offers no run view for it; a stub is not.
      expect(rows[0]).to.not.have.property('copied');
      expect(rows[1]).to.include({ copied: true });
      expect(rows[1]).to.not.have.property('runId');
      expect(rows[1]).to.deep.include({
        status: 'Complete',
        citations: [
          { relevanceScore: 0.5, citationId: 'new-1' },
          { citationId: 'new-2' },
        ],
      });
    });

    it('writes a chat fork with its model and no citation insert when nothing is cited', async () => {
      sinon.stub(AgentSessionForkModel, 'updateOne').resolves({ matchedCount: 1 } as never);
      const create = sinon.stub(ChatSession, 'create').resolves([] as never);
      const citations = sinon.stub(Citation, 'insertMany');
      sinon.stub(ChatSessionMessage, 'insertMany').resolves([] as never);
      await new HarnessSessionForkStore().commit(
        commit({
          source: { sessionType: 'chat', agentKey: null, title: 't', modelInfo: { modelKey: 'm' } },
          messages: [
            { messageType: 'user_query', content: 'q', citations: [], fields: {}, foreignTurn: false },
          ],
        }) as never,
        {} as never,
      );
      const [[session]] = create.firstCall.args as unknown as [[Record<string, unknown>]];
      expect(session).to.deep.include({ sessionType: 'chat', modelInfo: { modelKey: 'm' } });
      expect(session).to.not.have.property('agentKey');
      expect(citations.called).to.equal(false);
    });

    it('SLASH2: a durable source forks into a durable copy with an empty ledger; a legacy one stays legacy', async () => {
      sinon.stub(AgentSessionForkModel, 'updateOne').resolves({ matchedCount: 1 } as never);
      const create = sinon.stub(ChatSession, 'create').resolves([] as never);
      sinon.stub(ChatSessionMessage, 'insertMany').resolves([] as never);
      const write = (durable: boolean) =>
        new HarnessSessionForkStore().commit(
          commit({
            source: { sessionType: 'chat', agentKey: null, title: 't', modelInfo: null, durable },
            messages: [
              { messageType: 'user_query', content: 'q', citations: [], fields: {}, foreignTurn: false },
            ],
          }) as never,
          {} as never,
        );
      await write(true);
      await write(false);
      const [[durable]] = create.firstCall.args as unknown as [[Record<string, unknown>]];
      const [[legacy]] = create.secondCall.args as unknown as [[Record<string, unknown>]];
      expect(durable).to.deep.include({ harnessVersion: 'durable-v1', harnessLedger: EMPTY_LEDGER });
      expect(legacy).to.not.have.property('harnessVersion');
      expect(legacy).to.not.have.property('harnessLedger');
    });

    it('fails rather than link a message to a citation that was not written', async () => {
      sinon.stub(AgentSessionForkModel, 'updateOne').resolves({ matchedCount: 1 } as never);
      sinon.stub(ChatSession, 'create').resolves([] as never);
      sinon.stub(Citation, 'insertMany').resolves([{ _id: 'only-one' }] as never);
      const messages = sinon.stub(ChatSessionMessage, 'insertMany');
      let thrown: unknown;
      try {
        await new HarnessSessionForkStore().commit(commit() as never, {} as never);
      } catch (error) {
        thrown = error;
      }
      expect((thrown as Error).message).to.equal('A copied citation was not written');
      expect(messages.called).to.equal(false);
    });
  });
});

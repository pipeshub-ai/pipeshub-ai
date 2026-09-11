import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { ChatSessionsMigration } from '../../../../../src/modules/configuration_manager/services/migrations/chat_sessions.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { Conversation } from '../../../../../src/modules/enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../../../src/modules/enterprise_search/schema/agent.conversation.schema';
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../../src/modules/enterprise_search/schema/chat.session.message.schema';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeKvStore = (flags: Record<string, string | null> = {}) => ({
  get: sinon
    .stub()
    .callsFake((path: string) => Promise.resolve(path in flags ? flags[path] : null)),
  set: sinon.stub().resolves(),
});

/** A chainable query mock: .sort()/.limit()/.select() return itself, .lean() resolves. */
function chain(result: any) {
  const obj: any = {};
  obj.sort = sinon.stub().returns(obj);
  obj.limit = sinon.stub().returns(obj);
  obj.select = sinon.stub().returns(obj);
  obj.lean = sinon.stub().resolves(result);
  return obj;
}

interface LegacyStubOptions {
  missingOrgIdCount?: number;
  /** Sequential batches returned by the isMigrated:{$ne:true} scan; [] ends the loop. */
  batches?: any[][];
  schemaDriftSample?: any[];
}

function stubLegacyModel(Model: any, opts: LegacyStubOptions = {}) {
  const missingOrgIdCount = opts.missingOrgIdCount ?? 0;
  const batches = opts.batches ?? [[]];
  const schemaDriftSample = opts.schemaDriftSample ?? [];

  let batchCall = 0;
  const find = sinon.stub(Model, 'find').callsFake((filter: any) => {
    if (filter && filter.isMigrated) {
      const batch = batches[batchCall] ?? [];
      batchCall++;
      return chain(batch);
    }
    return chain(schemaDriftSample);
  });

  const countDocuments = sinon
    .stub(Model, 'countDocuments')
    .resolves(missingOrgIdCount);

  const collectionUpdateOne = sinon
    .stub(Model.collection, 'updateOne')
    .resolves({});

  return { find, countDocuments, collectionUpdateOne };
}

function stubChatSession() {
  const collectionUpdateOne = sinon
    .stub(ChatSession.collection, 'updateOne')
    .resolves({});
  return { collectionUpdateOne };
}

function stubChatSessionMessage(
  opts: {
    perDocCount?: (sessionId: any) => number;
    /** Full-filter variant, for asserting the `_id: {$in: [...]}` scoping. */
    countByFilter?: (filter: any) => number;
  } = {},
) {
  const countDocuments = sinon
    .stub(ChatSessionMessage, 'countDocuments')
    .callsFake((filter: any) => {
      if (opts.countByFilter) {
        return Promise.resolve(opts.countByFilter(filter)) as any;
      }
      return Promise.resolve(
        opts.perDocCount ? opts.perDocCount(filter.sessionId) : 0,
      ) as any;
    });
  const bulkWrite = sinon.stub(ChatSessionMessage.collection, 'bulkWrite').resolves({});
  return { countDocuments, bulkWrite };
}

function makeMessage(overrides: Partial<any> = {}) {
  return {
    _id: new mongoose.Types.ObjectId(),
    messageType: 'user_query',
    content: 'hello',
    ...overrides,
  };
}

function makeLegacyDoc(overrides: Partial<any> = {}) {
  return {
    _id: new mongoose.Types.ObjectId(),
    orgId: new mongoose.Types.ObjectId(),
    userId: new mongoose.Types.ObjectId(),
    initiator: new mongoose.Types.ObjectId(),
    messages: [],
    ...overrides,
  };
}

describe('ChatSessionsMigration', () => {
  afterEach(() => {
    sinon.restore();
  });

  it('defers when the chat KB-filters migration has not completed', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({}); // chatKbFiltersMigration flag absent -> not 'true'
    const conversationFindStub = sinon.stub(Conversation, 'find');

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result).to.deep.equal({
      sessionsMigrated: 0,
      messagesMigrated: 0,
      errored: 0,
    });
    expect(conversationFindStub.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('treats a KB-filters flag read failure as not-done and defers', async () => {
    const logger = makeLogger();
    const kv = {
      get: sinon.stub().rejects(new Error('kv unavailable')),
      set: sinon.stub().resolves(),
    };
    const conversationFindStub = sinon.stub(Conversation, 'find');

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result).to.deep.equal({
      sessionsMigrated: 0,
      messagesMigrated: 0,
      errored: 0,
    });
    expect(conversationFindStub.called).to.equal(false);
    expect(logger.warn.called).to.equal(true);
  });

  it('skips both collections when both are already marked migrated', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        conversationsMigrated: true,
        agentConversationsMigrated: true,
      }),
    });
    const conversationFindStub = sinon.stub(Conversation, 'find');
    const agentFindStub = sinon.stub(AgentConversation, 'find');

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result).to.deep.equal({
      sessionsMigrated: 0,
      messagesMigrated: 0,
      errored: 0,
    });
    expect(conversationFindStub.called).to.equal(false);
    expect(agentFindStub.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('treats an unparseable chatSessions flag as not-yet-migrated for both collections', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: '{not valid json',
    });
    stubLegacyModel(Conversation);
    stubLegacyModel(AgentConversation);
    stubChatSession();
    stubChatSessionMessage();

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result).to.deep.equal({
      sessionsMigrated: 0,
      messagesMigrated: 0,
      errored: 0,
    });
    expect(logger.warn.calledWith(sinon.match(/Failed to read\/parse/))).to.equal(true);
    // Both collections attempted (empty -> zero errors); one flag write per
    // successfully-completed collection, cumulative on the object.
    expect(kv.set.callCount).to.equal(2);
    const written = JSON.parse(kv.set.lastCall.args[1]);
    expect(written.conversationsMigrated).to.equal(true);
    expect(written.agentConversationsMigrated).to.equal(true);
  });

  it('aborts a collection and reports errored when documents are missing orgId', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({ [configPaths.chatKbFiltersMigration]: 'true' });
    const legacyStubs = stubLegacyModel(Conversation, { missingOrgIdCount: 3 });
    stubLegacyModel(AgentConversation); // agent collection is empty/healthy
    stubChatSession();
    stubChatSessionMessage();

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result.errored).to.equal(3);
    expect(result.sessionsMigrated).to.equal(0);
    // Neither the schema-drift audit nor the batch scan may run once the
    // hard pre-flight check fails.
    expect(legacyStubs.find.called).to.equal(false);
    expect(logger.error.calledWith(sinon.match(/missing orgId/))).to.equal(true);
    const written = kv.set.called ? JSON.parse(kv.set.firstCall.args[1]) : {};
    expect(written.conversationsMigrated).to.not.equal(true);
  });

  it('migrates a document: seq = index + 1 and nextSeq = messages.length', async () => {
    const logger = makeLogger();
    // Agent collection pre-marked done so this test only exercises conversations.
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        agentConversationsMigrated: true,
      }),
    });

    const orgId = new mongoose.Types.ObjectId();
    const message0 = makeMessage({ content: 'first' });
    const message1 = makeMessage({ content: 'second' });
    const legacyDoc = makeLegacyDoc({ orgId, messages: [message0, message1] });

    const conversationStubs = stubLegacyModel(Conversation, {
      batches: [[legacyDoc]],
    });
    const agentFindStub = sinon.stub(AgentConversation, 'find');
    const sessionStubs = stubChatSession();
    const messageStubs = stubChatSessionMessage({ perDocCount: () => 2 });

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result.errored).to.equal(0);
    expect(result.sessionsMigrated).to.equal(1);
    expect(result.messagesMigrated).to.equal(2);

    // Message ops: seq derived from array index (1-based), sessionId/orgId stamped.
    expect(messageStubs.bulkWrite.calledOnce).to.equal(true);
    const ops = messageStubs.bulkWrite.firstCall.args[0];
    expect(ops).to.have.length(2);
    expect(ops[0].updateOne.update.$setOnInsert.seq).to.equal(1);
    expect(ops[0].updateOne.update.$setOnInsert.sessionId).to.equal(legacyDoc._id);
    expect(ops[0].updateOne.update.$setOnInsert.orgId).to.equal(orgId);
    expect(ops[0].updateOne.upsert).to.equal(true);
    expect(ops[1].updateOne.update.$setOnInsert.seq).to.equal(2);

    // Session upsert: nextSeq = message count, sessionType stamped, upsert-only.
    expect(sessionStubs.collectionUpdateOne.calledOnce).to.equal(true);
    const [sessionFilter, sessionUpdate, sessionOptions] =
      sessionStubs.collectionUpdateOne.firstCall.args;
    expect(sessionFilter).to.deep.equal({ _id: legacyDoc._id });
    expect(sessionUpdate.$setOnInsert.nextSeq).to.equal(2);
    expect(sessionUpdate.$setOnInsert.sessionType).to.equal('chat');
    expect(sessionOptions).to.deep.equal({ upsert: true });

    // Legacy flag write happens last, via $set (not $setOnInsert — it must take effect every run).
    expect(conversationStubs.collectionUpdateOne.calledOnce).to.equal(true);
    expect(conversationStubs.collectionUpdateOne.firstCall.args).to.deep.equal([
      { _id: legacyDoc._id },
      { $set: { isMigrated: true } },
    ]);

    // Zero errors -> completion flag written; agent's pre-existing true survives untouched.
    expect(agentFindStub.called).to.equal(false);
    expect(kv.set.calledOnce).to.equal(true);
    const written = JSON.parse(kv.set.firstCall.args[1]);
    expect(written.conversationsMigrated).to.equal(true);
    expect(written.agentConversationsMigrated).to.equal(true);
  });

  it('migrates a zero-message conversation with nextSeq: 0 and skips the empty bulkWrite', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        agentConversationsMigrated: true,
      }),
    });
    const legacyDoc = makeLegacyDoc({ messages: [] });

    stubLegacyModel(Conversation, { batches: [[legacyDoc]] });
    const sessionStubs = stubChatSession();
    const messageStubs = stubChatSessionMessage();

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result).to.include({ errored: 0, sessionsMigrated: 1, messagesMigrated: 0 });
    expect(messageStubs.bulkWrite.called).to.equal(false);
    const sessionUpdate = sessionStubs.collectionUpdateOne.firstCall.args[1];
    expect(sessionUpdate.$setOnInsert.nextSeq).to.equal(0);
  });

  it('marks a document errored (not migrated) when the stored message count does not match', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        agentConversationsMigrated: true,
      }),
    });
    const legacyDoc = makeLegacyDoc({ messages: [makeMessage(), makeMessage()] });

    const conversationStubs = stubLegacyModel(Conversation, {
      batches: [[legacyDoc]],
    });
    const sessionStubs = stubChatSession();
    // Simulate one of the two copied rows failing to land.
    stubChatSessionMessage({ perDocCount: () => 1 });

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result.errored).to.equal(1);
    expect(result.sessionsMigrated).to.equal(0);
    // Write order: on failure, neither the session nor the isMigrated flag is written.
    expect(sessionStubs.collectionUpdateOne.called).to.equal(false);
    expect(conversationStubs.collectionUpdateOne.called).to.equal(false);
    // At least one document still errored this boot -> completion flag withheld
    // even though the batch loop itself terminated (no unmigrated docs left to scan).
    expect(kv.set.called).to.equal(false);
  });

  it('terminates instead of rescanning a permanently-failing document forever', async () => {
    // Regression: the scan filter is {isMigrated: {$ne: true}} and a failed
    // document is deliberately left unflagged, so without an _id cursor it
    // sorts back to the front of the very next scan and the loop spins on it
    // for the rest of the boot. This stub models the real collection
    // (honouring _id: {$gt: cursor}) so an unbounded loop would hang here.
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        agentConversationsMigrated: true,
      }),
    });

    const failing = makeLegacyDoc({ messages: [makeMessage(), makeMessage()] });
    const healthy = makeLegacyDoc({ messages: [makeMessage()] });
    // _id order drives the cursor; make the failing document sort first.
    const docs = [failing, healthy].sort((a, b) =>
      a._id.toString() < b._id.toString() ? -1 : 1,
    );

    sinon.stub(Conversation, 'countDocuments').resolves(0 as any);
    const find = sinon.stub(Conversation, 'find').callsFake((filter: any) => {
      if (!filter?.isMigrated) {
        return chain([]) as any;
      }
      const after = filter._id?.$gt;
      const remaining = docs.filter(
        (d) => !d.isMigrated && (!after || d._id.toString() > after.toString()),
      );
      return chain(remaining.slice(0, 1)) as any;
    });
    // Only successfully migrated documents get flagged, mirroring production.
    sinon.stub(Conversation.collection, 'updateOne').callsFake((filter: any) => {
      const target = docs.find((d) => d._id.toString() === filter._id.toString());
      if (target) {
        target.isMigrated = true;
      }
      return Promise.resolve({}) as any;
    });
    stubChatSession();
    // One row lands per session: a match for the 1-message healthy document,
    // one short for the 2-message failing one.
    stubChatSessionMessage({ countByFilter: () => 1 });

    const result = await new ChatSessionsMigration(
      logger as any,
      kv as any,
      1,
    ).run();

    expect(result.errored).to.equal(1);
    expect(result.sessionsMigrated).to.equal(1);
    // Scans: failing doc, healthy doc, then the empty scan that ends the loop.
    const scanCalls = find.getCalls().filter((c) => c.args[0]?.isMigrated);
    expect(scanCalls).to.have.length(3);
    // Errored document -> completion flag withheld, retried on the next boot.
    expect(kv.set.called).to.equal(false);
  });

  it('still migrates a document whose session already accumulated newer live messages', async () => {
    // Crash-recovery case: a previous attempt wrote the messages and the
    // ChatSession but died before the isMigrated flag, so the session went
    // live and users appended to it. Counting every row for the sessionId
    // would now exceed messages.length on every retry and strand the
    // document; the count must be scoped to the ids being copied.
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        agentConversationsMigrated: true,
      }),
    });
    const legacyDoc = makeLegacyDoc({ messages: [makeMessage(), makeMessage()] });
    const legacyIds = legacyDoc.messages.map((m: any) => m._id.toString());

    const conversationStubs = stubLegacyModel(Conversation, {
      batches: [[legacyDoc]],
    });
    stubChatSession();
    const messageStubs = stubChatSessionMessage({
      countByFilter: (filter: any) => {
        // 5 rows exist for this session, only 2 of them are the copied ids.
        const requested = filter._id?.$in;
        if (!requested) {
          return 5;
        }
        return requested.filter((id: any) =>
          legacyIds.includes(id.toString()),
        ).length;
      },
    });

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result).to.deep.equal({
      sessionsMigrated: 1,
      messagesMigrated: 2,
      errored: 0,
    });
    // The verification query must be scoped by the copied message ids.
    const countFilter = messageStubs.countDocuments.firstCall.args[0] as any;
    expect(countFilter._id?.$in).to.have.length(2);
    expect(conversationStubs.collectionUpdateOne.calledOnce).to.equal(true);
  });

  it('refuses to copy a document with duplicate message _ids before any write happens', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        agentConversationsMigrated: true,
      }),
    });
    const duplicated = makeMessage();
    const legacyDoc = makeLegacyDoc({
      messages: [duplicated, { ...duplicated, content: 'same _id' }],
    });

    const conversationStubs = stubLegacyModel(Conversation, {
      batches: [[legacyDoc]],
    });
    const sessionStubs = stubChatSession();
    const messageStubs = stubChatSessionMessage();

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result.errored).to.equal(1);
    expect(result.sessionsMigrated).to.equal(0);
    // An _id-keyed upsert would silently collapse the two rows, so nothing
    // may be written at all.
    expect(messageStubs.bulkWrite.called).to.equal(false);
    expect(sessionStubs.collectionUpdateOne.called).to.equal(false);
    expect(conversationStubs.collectionUpdateOne.called).to.equal(false);
    expect(
      logger.error.calledWith(sinon.match(/Failed to migrate a conversations document/)),
    ).to.equal(true);
  });

  it('refuses to copy a document whose message is missing an _id', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        agentConversationsMigrated: true,
      }),
    });
    const legacyDoc = makeLegacyDoc({
      messages: [makeMessage(), { messageType: 'bot_response', content: 'no id' }],
    });

    stubLegacyModel(Conversation, { batches: [[legacyDoc]] });
    const sessionStubs = stubChatSession();
    const messageStubs = stubChatSessionMessage();

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result.errored).to.equal(1);
    expect(messageStubs.bulkWrite.called).to.equal(false);
    expect(sessionStubs.collectionUpdateOne.called).to.equal(false);
  });

  it('does not treat ordinary live traffic on chatSessions/chatSessionMessages as a migration failure', async () => {
    // Regression test: verification used to compare legacy counts against
    // ChatSession.countDocuments({sessionType}) / chatSessionMessages scoped
    // by that sessionType — both of which grow from ordinary live usage
    // while the migration is running, causing false-positive failures with
    // zero real errored documents. There is no such comparison left to stub;
    // a clean single-document migration must complete regardless of how
    // much unrelated live data exists in chatSessions/chatSessionMessages.
    const logger = makeLogger();
    const kv = makeKvStore({
      [configPaths.chatKbFiltersMigration]: 'true',
      [configPaths.chatSessionsMigration]: JSON.stringify({
        agentConversationsMigrated: true,
      }),
    });
    const legacyDoc = makeLegacyDoc({ messages: [makeMessage()] });

    stubLegacyModel(Conversation, { batches: [[legacyDoc]] });
    stubChatSession();
    stubChatSessionMessage({ perDocCount: () => 1 });
    // No stubs on ChatSession.countDocuments/find or
    // ChatSessionMessage.countDocuments({sessionId:{$in:...}}) — if the
    // implementation regressed to calling either, these would throw against
    // a disconnected Mongoose model and fail the test.

    const result = await new ChatSessionsMigration(logger as any, kv as any).run();

    expect(result).to.deep.equal({
      sessionsMigrated: 1,
      messagesMigrated: 1,
      errored: 0,
    });
    const written = JSON.parse(kv.set.firstCall.args[1]);
    expect(written.conversationsMigrated).to.equal(true);
  });

  it('logs sampled unknown fields when a legacy document has keys absent from the chatSessions schema', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({ [configPaths.chatKbFiltersMigration]: 'true' });
    const driftDoc = makeLegacyDoc({ legacyOnlyField: 'still here' });

    stubLegacyModel(Conversation, { schemaDriftSample: [driftDoc] });
    stubLegacyModel(AgentConversation);
    stubChatSession();
    stubChatSessionMessage();

    await new ChatSessionsMigration(logger as any, kv as any).run();

    const warnCall = logger.warn
      .getCalls()
      .find((c) => /fields not present on the chatSessions schema/.test(c.args[0]));
    expect(warnCall).to.exist;
    expect(warnCall!.args[1].unknownFields).to.include('legacyOnlyField');
  });

  it('reads CHAT_SESSIONS_MIGRATION_BATCH_SIZE-style explicit batch size and applies it to the scan', async () => {
    const logger = makeLogger();
    const kv = makeKvStore({ [configPaths.chatKbFiltersMigration]: 'true' });
    const legacyStubs = stubLegacyModel(Conversation);
    stubLegacyModel(AgentConversation);
    stubChatSession();
    stubChatSessionMessage();

    await new ChatSessionsMigration(logger as any, kv as any, 25).run();

    // First find() call is the schema-drift sample; the second is the batch scan.
    const scanCallIndex = legacyStubs.find
      .getCalls()
      .findIndex((c) => c.args[0]?.isMigrated);
    expect(scanCallIndex).to.be.greaterThan(-1);
    const scanChain = legacyStubs.find.returnValues[scanCallIndex];
    expect(scanChain.limit.calledWith(25)).to.equal(true);
  });
});

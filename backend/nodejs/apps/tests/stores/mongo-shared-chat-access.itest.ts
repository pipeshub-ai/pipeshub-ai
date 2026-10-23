/**
 * Write-access filters on shared chats against a real MongoDB. With the dotted
 * form ('sharedWith.userId' and 'sharedWith.accessLevel' as two keys) a reader
 * matched "write" whenever any OTHER entry was a writer; stubs cannot show that.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { ChatSession } from '../../src/modules/enterprise_search/schema/chat.session.schema';
import {
  archiveConversation,
  deleteConversationById,
} from '../../src/modules/enterprise_search/controller/es_controller';
import {
  assertNotReadOnlyViewer,
  buildAgentConversationFilter,
  ownerOrWriterFilter,
  validateAgentConversationAccess,
} from '../../src/modules/enterprise_search/utils/utils';

const oid = () => new mongoose.Types.ObjectId();
const ORG = oid(), OWNER = oid(), WRITER = oid(), READER = oid(), OTHER = oid();

describe('Shared chat write access against a real MongoDB', function () {
  this.timeout(30000);
  const uri = process.env.MONGO_IT_URI;
  let chatId: mongoose.Types.ObjectId;
  let agentChatId: mongoose.Types.ObjectId;

  before(async function () {
    if (!uri) this.skip();
    await mongoose.connect(uri, { dbName: `pipeshub_it_sharedchat_${Date.now()}`, serverSelectionTimeoutMS: 5000 });
    const sharedWith = [
      { userId: WRITER, accessLevel: 'write' },
      { userId: READER, accessLevel: 'read' },
    ];
    const base = { orgId: ORG, userId: OWNER, initiator: OWNER, isShared: true, isDeleted: false, sharedWith };
    chatId = (await ChatSession.collection.insertOne({ ...base, sessionType: 'chat', title: 'c' })).insertedId;
    agentChatId = (await ChatSession.collection.insertOne({ ...base, sessionType: 'agent', agentKey: 'ag', title: 'a' })).insertedId;
  });
  after(async () => {
    if (!uri) return;
    await mongoose.connection.dropDatabase();
    await mongoose.disconnect();
  });

  const canAdd = async (user: mongoose.Types.ObjectId) =>
    !!(await ChatSession.findOne({ _id: chatId, ...ownerOrWriterFilter(user) }));

  it('lets the owner and the writer add, and neither the reader nor a stranger', async () => {
    expect(await canAdd(OWNER)).to.equal(true);
    expect(await canAdd(WRITER)).to.equal(true);
    expect(await canAdd(READER), 'a reader is not admitted by the writer’s entry').to.equal(false);
    expect(await canAdd(OTHER)).to.equal(false);
  });

  it('tells a reader 403 and a stranger nothing', async () => {
    const base = { _id: chatId, orgId: ORG, isDeleted: false };
    let err: any;
    await assertNotReadOnlyViewer(base, READER).catch((e) => (err = e));
    expect(err?.statusCode).to.equal(403);
    await assertNotReadOnlyViewer(base, WRITER);
    await assertNotReadOnlyViewer(base, OTHER);
  });

  it('validateAgentConversationAccess(write) refuses a reader of an agent chat that also has a writer', async () => {
    const access = (u: mongoose.Types.ObjectId, level: 'read' | 'write') =>
      validateAgentConversationAccess(String(agentChatId), 'ag', String(u), String(ORG), level);
    expect(await access(WRITER, 'write')).to.not.equal(null);
    expect(await access(READER, 'write'), 'reader must not get write').to.equal(null);
    expect(await access(READER, 'read')).to.not.equal(null);
    expect(await access(OTHER, 'read')).to.equal(null);
  });

  it('the agent view/stop filter admits a writer to stop, a reader only to view', async () => {
    const find = (u: mongoose.Types.ObjectId, level: 'read' | 'write') =>
      ChatSession.findOne(
        buildAgentConversationFilter({ query: {} }, String(ORG), String(u), 'ag', String(agentChatId), undefined, undefined, level),
      );
    expect(await find(WRITER, 'write')).to.not.equal(null);
    expect(await find(READER, 'write'), 'a reader must not stop').to.equal(null);
    expect(await find(READER, 'read')).to.not.equal(null);
    expect(await find(OTHER, 'read')).to.equal(null);
  });

  it('archive and delete refuse a reader and leave the chat as it was', async () => {
    for (const handler of [archiveConversation, deleteConversationById({ aiBackend: 'http://ai.test' } as never)] as any[]) {
      let err: any;
      await handler(
        { params: { conversationId: String(chatId) }, user: { userId: String(READER), orgId: String(ORG) }, query: {}, body: {}, headers: {}, context: {} },
        { status: () => ({ json: () => undefined }), json: () => undefined },
        (e: any) => (err = e),
      );
      expect(err?.statusCode, handler.name).to.be.oneOf([403, 404]);
    }
    const row: any = await ChatSession.collection.findOne({ _id: chatId });
    expect(row.isDeleted).to.equal(false);
    expect(row.isArchived ?? false).to.equal(false);
  });
});

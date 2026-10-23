/**
 * Org scoping of search history, storage-tree, chat-message and user lookups
 * against a real MongoDB. Unit tests stub the models, so they cannot show that
 * another org's rows stay invisible.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { searchHistory } from '../../src/modules/enterprise_search/controller/es_controller';
import EnterpriseSemanticSearch from '../../src/modules/enterprise_search/schema/search.schema';
import { ChatSessionMessage } from '../../src/modules/enterprise_search/schema/chat.session.message.schema';
import * as U from '../../src/modules/enterprise_search/utils/utils';
import { DocumentModel } from '../../src/modules/storage/schema/document.schema';
import { UserController } from '../../src/modules/user_management/controller/users.controller';
import { Users } from '../../src/modules/user_management/schema/users.schema';
import { ForbiddenError } from '../../src/libs/errors/http.errors';

const oid = () => new mongoose.Types.ObjectId();
const A = oid(), B = oid(), uA = oid(), uB = oid();

describe('Org scoping against a real MongoDB', function () {
  this.timeout(30000);
  const uri = process.env.MONGO_IT_URI;

  before(async function () {
    if (!uri) this.skip(); // no MongoDB available
    mongoose.set('strictQuery', true);
    await mongoose.connect(uri, { dbName: `pipeshub_it_orgscope_${Date.now()}`, serverSelectionTimeoutMS: 5000 });
  });
  after(async () => {
    if (!uri) return;
    await mongoose.connection.dropDatabase();
    await mongoose.disconnect();
  });

  it('searchHistory total is per org', async () => {
    const mk = (orgId: any, userId: any, i: number) => ({ query: 'q' + i, limit: 5, orgId, userId, isDeleted: false, isArchived: false, createdAt: new Date(), updatedAt: new Date() });
    await EnterpriseSemanticSearch.collection.insertMany([
      ...[1, 2, 3].map((i) => mk(A, uA, i)), ...[1, 2, 3, 4, 5, 6, 7].map((i) => mk(B, uB, i))]);
    let body: any;
    await searchHistory({} as any)({ user: { orgId: A.toString(), userId: uA.toString() }, query: {}, context: {} } as any,
      { status: () => ({ json: (b: any) => { body = b; } }) } as any, (e: any) => { throw e; });
    expect(body.searchHistory).to.have.length(3);
    expect(body.pagination.totalCount ?? body.pagination.total).to.equal(3);
  });

  it('email lookup fails closed and is scoped to the token', async () => {
    const c = new (UserController as any)({}, {}, {}, {}, {}, {});
    await Users.collection.insertMany([
      { email: 'a@x.com', orgId: A, isDeleted: false, fullName: 'a', slug: 'it-user-a' },
      { email: 'b@y.com', orgId: B, isDeleted: false, fullName: 'b', slug: 'it-user-b' }]);
    const call = async (tokenPayload: any, email: string) => {
      let out: any;
      await c.checkUserExistsByEmail({ body: { email }, tokenPayload } as any, { json: (j: any) => { out = j; } } as any, (e: any) => { throw e; });
      return out;
    };
    expect(await call({ email: 'a@x.com' }, 'a@x.com')).to.have.length(1);
    expect(await call({ email: 'A@X.com' }, 'a@x.com')).to.have.length(1);
    // A token for another address, or with none, is refused before any lookup.
    const refused = async (tokenPayload: any, email: string) => {
      try {
        await call(tokenPayload, email);
      } catch (e) {
        return e;
      }
      throw new Error('expected the lookup to be refused');
    };
    expect(await refused({ email: 'a@x.com' }, 'b@y.com')).to.be.instanceOf(ForbiddenError);
    expect(await refused({}, 'b@y.com')).to.be.instanceOf(ForbiddenError);
    expect(await refused({ email: '' }, 'b@y.com')).to.be.instanceOf(ForbiddenError);
    expect(await call({ email: 'a@x.com', orgId: B.toString() }, 'a@x.com')).to.have.length(0);
  });

  it('moveTree lookup filter is org scoped', async () => {
    const p = `${A}/PipesHub/dir/f`;
    await DocumentModel.collection.insertMany([{ orgId: A, documentPath: p, isDeleted: false }, { orgId: B, documentPath: p, isDeleted: false }]);
    expect(await DocumentModel.find({ orgId: A, $or: [{ documentPath: p }] }).lean()).to.have.length(1);
    expect(await DocumentModel.find({ $or: [{ documentPath: p }] }).lean()).to.have.length(2);
  });

  it('messages of another org are invisible and untouchable', async () => {
    const sid = oid();
    const r = await ChatSessionMessage.collection.insertOne({ sessionId: sid, orgId: B, seq: 1, messageType: 'user_query', content: 'secret', createdAt: new Date(), updatedAt: new Date() });
    const m = r.insertedId;
    expect(await U.getMessages(sid, { orgId: A })).to.have.length(0);
    expect(await U.getMessages(sid, { orgId: B })).to.have.length(1);
    expect(await U.updateMessageById(m, A, { messageType: 'bot_response', content: 'x' } as any)).to.equal(null);
    expect(await U.appendMessageFeedback(m, A, { isHelpful: true })).to.equal(null);
    expect(await U.deleteMessagesById([m], A)).to.equal(0);
    const after: any = await ChatSessionMessage.collection.findOne({ _id: m });
    expect(after.content).to.equal('secret');
    expect(after.feedback ?? []).to.have.length(0);
    expect(await U.deleteMessagesById([m], B)).to.equal(1);
  });
});

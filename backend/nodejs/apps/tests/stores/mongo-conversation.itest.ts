/**
 * ConversationRepository against a real MongoDB, as a replica set and as a single server.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import { expect } from 'chai';
import mongoose from 'mongoose';
import sinon from 'sinon';
import { ChatSession } from '../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoUnitOfWork } from '../../src/libs/repositories/mongo';
import { MongoConversationRepository } from '../../src/libs/repositories/conversation/mongo';
import { describeConversationContract } from './contract/conversation.contract';
import {
  Fold,
  closureFromStored,
  isStoredClosure,
  storedClosure,
} from '../../src/modules/enterprise_search/services/conversation/privacy/closure';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('MongoConversationRepository against a real MongoDB', function () {
  this.timeout(60000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  const rsAvailable = rsFlag === 'true';
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
    // Collections must exist before a transaction touches them.
    for (const model of Object.values(mongoose.models)) await model.createCollection().catch(() => undefined);
    for (const model of Object.values(mongoose.models)) await model.syncIndexes();
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeConversationContract({
    repo: new MongoConversationRepository(),
    uow: new MongoUnitOfWork(rsAvailable),
    transactional: rsAvailable,
    readsEveryOrg: true,
    newId: () => new mongoose.Types.ObjectId().toHexString(),
  });

  it('clearing the share link removes the stored field', async () => {
    const repo = new MongoConversationRepository();
    const org = new mongoose.Types.ObjectId().toHexString();
    const user = new mongoose.Types.ObjectId().toHexString();
    const s = await repo.openSession(
      { orgId: org, userId: user, initiator: user, lastActivityAt: 1, status: 'Complete', sessionType: 'chat' },
      { messageType: 'user_query', content: 'q' },
    );
    const scope = { kind: 'chat' } as const;
    await ChatSession.updateOne({ _id: s._id }, { $set: { shareLink: 'https://x/chat/1', isShared: true } });

    const cleared = await repo.setSharing(scope, s._id, { isShared: false, sharedWith: [], clearShareLink: true });

    expect(cleared).to.not.have.property('shareLink');
    const stored = await ChatSession.findById(s._id).lean();
    expect(stored).to.not.have.property('shareLink');
    expect(stored!.isShared).to.equal(false);
  });

  it('reads a page of sessions\' messages with one query', async () => {
    const repo = new MongoConversationRepository();
    const org = new mongoose.Types.ObjectId().toHexString();
    const user = new mongoose.Types.ObjectId().toHexString();
    const ids: string[] = [];
    for (let i = 0; i < 5; i++) {
      const s = await repo.openSession(
        { orgId: org, userId: user, initiator: user, lastActivityAt: i, status: 'Complete', sessionType: 'chat' },
        { messageType: 'user_query', content: `q${i}` },
      );
      ids.push(s._id);
    }
    const find = sinon.spy(ChatSessionMessage, 'find');
    try {
      const grouped = await repo.listMessagesOfSessions(ids, { orgId: org, withCitations: true });
      expect(grouped.size).to.equal(5);
      expect(find.callCount).to.equal(1);
    } finally {
      find.restore();
    }
  });

  describe("the reader check's kept closures", () => {
    const closure = (org: string, session: string, rowId: string, ver: string, over: Record<string, unknown> = {}) => ({
      _id: `${rowId}@${ver}`,
      orgId: org,
      sessionId: session,
      rowId,
      ver,
      rev: 3,
      self: { askers: { 'u:x': { ids: ['rec-r'] } } },
      add: {},
      chain: [],
      folded: [],
      size: 1,
      spans: [],
      pub: { self: false, below: false, rows: [], chats: [] },
      shares: {},
      digest: 'd',
      at: new Date(),
      ...over,
    });

    it('are kept whole by key, replaced whole, read only by their org, and removed with a discarded chat', async () => {
      const repo = new MongoConversationRepository();
      const org = new mongoose.Types.ObjectId().toHexString();
      const other = new mongoose.Types.ObjectId().toHexString();
      const user = new mongoose.Types.ObjectId().toHexString();
      const s = await repo.openSession(
        { orgId: org, userId: user, initiator: user, lastActivityAt: 1, status: 'Complete', sessionType: 'chat' },
        { messageType: 'user_query', content: 'q' },
      );
      const row = new mongoose.Types.ObjectId().toHexString();
      const ver = new mongoose.Types.ObjectId().toHexString();
      await repo.saveAnswerClosures(org, [closure(org, s._id, row, ver)]);
      const kept = (await repo.listAnswerClosures(org, [`${row}@${ver}`, 'nobody@nothing'])).get(`${row}@${ver}`);
      expect(kept).to.include({ _id: `${row}@${ver}`, orgId: org, sessionId: s._id, rowId: row, ver });
      expect(kept!.self).to.deep.equal({ askers: { 'u:x': { ids: ['rec-r'] } } });
      // An empty fold is a value: stored as it was given.
      expect(kept!.add).to.deep.equal({});
      await repo.saveAnswerClosures(org, [closure(org, s._id, row, ver, { digest: 'e', self: {} })]);
      expect((await repo.listAnswerClosures(org, [`${row}@${ver}`])).get(`${row}@${ver}`)).to.include({ digest: 'e' });
      expect((await repo.listAnswerClosures(other, [`${row}@${ver}`])).size).to.equal(0);
      // A closure saved under another org's id is not written into this one.
      await repo.saveAnswerClosures(org, [closure(other, s._id, row, `${ver}x`)]);
      expect((await repo.listAnswerClosures(org, [`${row}@${ver}x`])).size).to.equal(0);
      await repo.discardSession(s._id, org);
      expect((await repo.listAnswerClosures(org, [`${row}@${ver}`])).size).to.equal(0);
    });

    it('a closure the reader check stores reads back whole, and the reader check takes it', async () => {
      const repo = new MongoConversationRepository();
      const org = new mongoose.Types.ObjectId().toHexString();
      const user = new mongoose.Types.ObjectId().toHexString();
      const s = await repo.openSession(
        { orgId: org, userId: user, initiator: user, lastActivityAt: 1, status: 'Complete', sessionType: 'chat' },
        { messageType: 'user_query', content: 'q' },
      );
      const row = new mongoose.Types.ObjectId().toHexString();
      const ver = new mongoose.Types.ObjectId().toHexString();
      const self = new Fold();
      self.part(`u:${user}`).ids.add('rec-r');
      const doc = storedClosure(
        org,
        { id: row, ver, sessionId: s._id, self, rest: new Fold(), folded: [], weight: 1, spans: new Set([s._id]), pub: { self: false, below: false, rows: [], chats: [] } },
        { [s._id]: [] },
        undefined,
        new Date(),
      );
      await repo.saveAnswerClosures(org, [doc as never]);
      const kept = (await repo.listAnswerClosures(org, [doc._id])).get(doc._id);
      // Every field the reader check reads back, as it was given: a field the schema drops makes every kept closure unusable.
      for (const field of ['rev', 'size', 'spans', 'folded', 'chain', 'pub', 'shares', 'digest'] as const) {
        expect(kept?.[field], field).to.deep.equal(doc[field]);
      }
      if (!isStoredClosure(kept)) throw new Error('the stored closure does not read back as one');
      const closure = closureFromStored(kept, new Map());
      expect(closure, 'taken by the reader check').to.not.equal(undefined);
      expect(closure?.weight).to.equal(1);
      expect([...(closure?.spans ?? [])]).to.deep.equal([s._id]);
      expect([...(closure?.self.part(`u:${user}`).ids ?? [])]).to.deep.equal(['rec-r']);
    });

    it('the share marks of a chat: answers shared on their current version or an archived one, read from their indexes', async () => {
      const repo = new MongoConversationRepository();
      const org = new mongoose.Types.ObjectId().toHexString();
      const user = new mongoose.Types.ObjectId().toHexString();
      const s = await repo.openSession(
        { orgId: org, userId: user, initiator: user, lastActivityAt: 1, status: 'Complete', sessionType: 'chat' },
        { messageType: 'user_query', content: 'q' },
      );
      const t = await repo.openSession(
        { orgId: org, userId: user, initiator: user, lastActivityAt: 2, status: 'Complete', sessionType: 'chat' },
        { messageType: 'user_query', content: 'q' },
      );
      const [now, archived, plain] = await repo.appendMessages(s._id, org, [
        { messageType: 'bot_response', content: 'a' },
        { messageType: 'bot_response', content: 'b' },
        { messageType: 'bot_response', content: 'c' },
      ] as never);
      const [elsewhere] = await repo.appendMessages(t._id, org, [{ messageType: 'bot_response', content: 'd' }] as never);
      const consent = (ver: string) => ({ by: user, at: new Date(), ver, scope: 'everyone' });
      await ChatSessionMessage.updateOne({ _id: now!._id }, { $set: { consent: consent(now!.labelVer as string) } });
      await ChatSessionMessage.updateOne(
        { _id: archived!._id },
        { $set: { labelHistory: [{ ver: 'v-old', consent: consent('v-old') }] } },
      );
      await ChatSessionMessage.updateOne({ _id: elsewhere!._id }, { $set: { consent: consent(elsewhere!.labelVer as string) } });
      const marks = await repo.listShareMarks(org, [s._id]);
      expect(marks.map((m) => m._id).sort()).to.deep.equal([now!._id, archived!._id].sort());
      const mark = marks.find((m) => m._id === archived!._id)!;
      expect(mark.labelHistory).to.deep.equal([{ ver: 'v-old', consent: { ...consent('v-old'), at: mark.labelHistory![0]!.consent && (mark.labelHistory![0]!.consent as { at: Date }).at } }]);
      expect(marks.every((m) => !('content' in m) && !('provenance' in m))).to.equal(true);
      expect(await repo.listShareMarks(new mongoose.Types.ObjectId().toHexString(), [s._id])).to.deep.equal([]);
      expect(plain).to.not.equal(undefined);
      const plan = await ChatSessionMessage.find({
        $or: [
          { orgId: new mongoose.Types.ObjectId(org), sessionId: { $in: [new mongoose.Types.ObjectId(s._id)] }, consent: { $type: 'object' } },
          { orgId: new mongoose.Types.ObjectId(org), sessionId: { $in: [new mongoose.Types.ObjectId(s._id)] }, 'labelHistory.consent': { $type: 'object' } },
        ],
      }).explain('queryPlanner');
      expect(JSON.stringify(plan)).to.not.include('COLLSCAN');
    });
  });
});

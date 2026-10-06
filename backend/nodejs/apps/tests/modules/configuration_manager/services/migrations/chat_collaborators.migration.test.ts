import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import {
  ChatCollaboratorsMigration,
  normalizeCollaborators,
} from '../../../../../src/modules/configuration_manager/services/migrations/chat_collaborators.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema';

const oid = () => new Types.ObjectId();
const at = new Date('2026-01-01T00:00:00Z');

describe('normalizeCollaborators (PH03-09)', () => {
  const owner = oid();

  it('maps legacy write to read and counts the downgrade', () => {
    const u = oid();
    const out = normalizeCollaborators([{ userId: u, accessLevel: 'write' }], owner, at);
    expect(out.sharedWith).to.deep.equal([
      { principalType: 'user', userId: u, accessLevel: 'read', addedBy: owner, addedAt: at },
    ]);
    expect(out.downgradedWrites).to.equal(1);
    expect(out.isShared).to.equal(true);
  });

  it('dedupes by userId into a single read row', () => {
    const u = oid();
    const out = normalizeCollaborators(
      [
        { userId: u, accessLevel: 'read' },
        { userId: u.toString(), accessLevel: 'write' },
      ],
      owner,
      at,
    );
    expect(out.sharedWith).to.have.length(1);
    expect(out.sharedWith[0]?.accessLevel).to.equal('read');
  });

  it('drops the owner row and stray _id, treats null accessLevel as read', () => {
    const u = oid();
    const out = normalizeCollaborators(
      [
        { _id: oid(), userId: owner, accessLevel: 'read' },
        { _id: oid(), userId: u, accessLevel: null },
      ],
      owner,
      at,
    );
    expect(out.sharedWith).to.have.length(1);
    expect(out.sharedWith[0]).to.not.have.property('_id');
    expect(out.sharedWith[0]?.accessLevel).to.equal('read');
    expect(out.downgradedWrites).to.equal(0);
  });

  it('drops user-shaped rows with no userId and null entries, counted', () => {
    const out = normalizeCollaborators([{ accessLevel: 'read' }, null, 'x'], owner, at);
    expect(out.sharedWith).to.deep.equal([]);
    expect(out.isShared).to.equal(false);
    expect(out.droppedMalformed).to.equal(3);
  });

  it('preserves team and unknown-principal rows untouched', () => {
    const team = { principalType: 'team', teamId: 'all_ab', accessLevel: 'write' };
    const unknown = { principalType: 'group', groupId: 'g', accessLevel: 'read' };
    const out = normalizeCollaborators([team, unknown], owner, at);
    expect(out.sharedWith).to.deep.equal([team, unknown]);
    expect(out.isShared).to.equal(true);
    expect(out.downgradedWrites).to.equal(0);
  });

  it('keeps the level of an already-normalized user row (never re-downgrades)', () => {
    const u = oid();
    const row = { principalType: 'user', userId: u, accessLevel: 'write', addedBy: owner, addedAt: at };
    expect(normalizeCollaborators([row], owner, at).sharedWith).to.deep.equal([row]);
  });

  it('is idempotent on its own output', () => {
    const rows = [
      { userId: oid(), accessLevel: 'write' },
      { principalType: 'team', teamId: 't', accessLevel: 'read' },
    ];
    const once = normalizeCollaborators(rows, owner, at);
    const twice = normalizeCollaborators(once.sharedWith, owner, at);
    expect(twice.sharedWith).to.deep.equal(once.sharedWith);
    expect(twice.downgradedWrites).to.equal(0);
  });
});

describe('ChatCollaboratorsMigration', () => {
  const logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
  const doneChatSessions = JSON.stringify({
    conversationsMigrated: true,
    agentConversationsMigrated: true,
  });
  let docs: any[];
  let kv: Record<string, string>;
  let kvStore: any;
  let bulkWrite: sinon.SinonStub;
  let raceOnce: boolean;

  /** Minimal in-memory stand-in for the chatSessions native collection. */
  const matches = (doc: any, f: any): boolean => {
    if (f._id && String(f._id) !== String(doc._id)) return false;
    if ('sharedWith' in f) {
      // Mongo semantics: `null` matches a missing or null field, an array matches only an equal array.
      const have = doc.sharedWith;
      const ok = f.sharedWith === null ? have === null || have === undefined : JSON.stringify(f.sharedWith) === JSON.stringify(have);
      if (!ok) return false;
    }
    return true;
  };

  beforeEach(() => {
    logger.info.resetHistory();
    logger.warn.resetHistory();
    logger.error.resetHistory();
    raceOnce = false;
    kv = { [configPaths.chatSessionsMigration]: doneChatSessions };
    kvStore = {
      get: sinon.stub().callsFake((p: string) => Promise.resolve(kv[p] ?? null)),
      set: sinon.stub().callsFake((p: string, v: string) => {
        kv[p] = v;
        return Promise.resolve();
      }),
    };
    docs = [];
    sinon.stub(ChatSession.collection, 'find').callsFake(((filter: any) => {
      const gt = filter._id?.$gt;
      let rows = docs
        .filter((d) => (d.sharedWith?.length ?? 0) > 0 || d.isShared === true)
        .filter((d) => !gt || String(d._id) > String(gt))
        .sort((a, b) => String(a._id).localeCompare(String(b._id)));
      const cursor: any = {
        sort: () => cursor,
        limit: (n: number) => {
          rows = rows.slice(0, n);
          return cursor;
        },
        toArray: () => Promise.resolve(JSON.parse(JSON.stringify(rows)).map((r: any, i: number) => ({
          ...r,
          _id: rows[i]._id,
          userId: rows[i].userId,
          sharedWith: rows[i].sharedWith,
          updatedAt: rows[i].updatedAt,
        }))),
      };
      return cursor;
    }) as any);
    bulkWrite = sinon.stub(ChatSession.collection, 'bulkWrite').callsFake(((ops: any[]) => {
      let matched = 0;
      for (const { updateOne } of ops) {
        const doc = docs.find((d) => matches(d, updateOne.filter));
        if (!doc || raceOnce) continue;
        matched += 1;
        Object.assign(doc, updateOne.update.$set);
        doc.aclVersion = (doc.aclVersion ?? 0) + updateOne.update.$inc.aclVersion;
      }
      raceOnce = false;
      return Promise.resolve({ matchedCount: matched });
    }) as any);
  });

  afterEach(() => sinon.restore());

  const owner = oid();
  const seed = () => {
    const [a, b, c] = [oid(), oid(), oid()];
    docs.push(
      { _id: oid(), userId: owner, isShared: true, updatedAt: at, sharedWith: [{ userId: a, accessLevel: 'write' }, { userId: b, accessLevel: 'read' }, { userId: a, accessLevel: 'read' }] },
      { _id: oid(), userId: owner, isShared: true, updatedAt: at, sharedWith: [{ principalType: 'team', teamId: 't1', accessLevel: 'read' }, { userId: c, accessLevel: 'write' }] },
      { _id: oid(), userId: owner, isShared: true, updatedAt: at, sharedWith: [] },
    );
    return { a, b, c };
  };

  it('defers without writing a flag until both chat_sessions_v1 collections are done', async () => {
    kv[configPaths.chatSessionsMigration] = JSON.stringify({ conversationsMigrated: true });
    seed();
    const res = await new ChatCollaboratorsMigration(logger as any, kvStore).run();
    expect(res.scanned).to.equal(0);
    expect(bulkWrite.called).to.equal(false);
    expect(kv[configPaths.chatCollaboratorsMigration]).to.equal(undefined);
  });

  it('normalizes, counts downgrades, preserves team rows, writes the counts flag', async () => {
    seed();
    const res = await new ChatCollaboratorsMigration(logger as any, kvStore, 2).run();
    expect(res).to.deep.equal({ scanned: 3, normalized: 3, downgradedWrites: 2, raced: 0, errored: 0 });
    expect(JSON.parse(kv[configPaths.chatCollaboratorsMigration] as string)).to.deep.equal(res);
    expect(docs[0].sharedWith.map((r: any) => r.accessLevel)).to.deep.equal(['read', 'read']);
    expect(docs[1].sharedWith.some((r: any) => r.principalType === 'team' && r.teamId === 't1')).to.equal(true);
    expect(docs[2].isShared).to.equal(false);
    expect(docs[0].aclVersion).to.equal(1);
  });

  it('second run changes nothing', async () => {
    seed();
    await new ChatCollaboratorsMigration(logger as any, kvStore).run();
    const snapshot = JSON.stringify(docs);
    delete kv[configPaths.chatCollaboratorsMigration]; // force a re-scan, not just the flag skip
    bulkWrite.resetHistory();
    const res = await new ChatCollaboratorsMigration(logger as any, kvStore).run();
    expect(res.normalized).to.equal(0);
    expect(res.downgradedWrites).to.equal(0);
    expect(bulkWrite.called).to.equal(false);
    expect(JSON.stringify(docs)).to.equal(snapshot);
  });

  it('completes when a shared row has no sharedWith field at all (gate finding)', async () => {
    docs.push(
      { _id: oid(), userId: owner, isShared: true, updatedAt: at },
      { _id: oid(), userId: owner, isShared: true, updatedAt: at, sharedWith: null },
    );
    const res = await new ChatCollaboratorsMigration(logger as any, kvStore).run();
    expect(res).to.include({ normalized: 2, raced: 0, errored: 0 });
    expect(docs.map((d) => d.isShared)).to.deep.equal([false, false]);
    expect(kv[configPaths.chatCollaboratorsMigration]).to.be.a('string');
  });

  it('skips on the completion flag', async () => {
    seed();
    kv[configPaths.chatCollaboratorsMigration] = '{}';
    const res = await new ChatCollaboratorsMigration(logger as any, kvStore).run();
    expect(res.scanned).to.equal(0);
  });

  it('counts a missed optimistic write as raced and withholds the flag', async () => {
    seed();
    raceOnce = true;
    const res = await new ChatCollaboratorsMigration(logger as any, kvStore, 10).run();
    expect(res.raced).to.equal(3);
    expect(kv[configPaths.chatCollaboratorsMigration]).to.equal(undefined);
  });

  it('counts a failing batch as errored and withholds the flag', async () => {
    seed();
    bulkWrite.rejects(new Error('boom'));
    const res = await new ChatCollaboratorsMigration(logger as any, kvStore, 10).run();
    expect(res.errored).to.equal(3);
    expect(kv[configPaths.chatCollaboratorsMigration]).to.equal(undefined);
  });
});

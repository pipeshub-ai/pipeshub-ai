/**
 * The write-state operations of the MongoDB document repository, judged by the queries they send (no database here: the same
 * behaviour against a real MongoDB is `document store contract (fingerprint)` in tests/stores/contract/document.contract.ts).
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { MongoDocumentRepository } from '../../../src/libs/repositories/document/mongo';
import { DocumentBusy, ServerClockUnavailable } from '../../../src/libs/repositories/document/types';
import { DocumentModel } from '../../../src/modules/storage/schema/document.schema';

interface Sent {
  op: string;
  filter?: Record<string, unknown>;
  update?: Record<string, unknown>;
  options?: Record<string, unknown>;
  select?: string;
  maxTimeMS?: number;
  lean?: boolean;
}

/** A query stand-in: records what was asked and resolves to `result`. */
function query(sent: Sent[], base: Sent, result: unknown) {
  const rec = base;
  sent.push(rec);
  const q = {
    select(s: string) { rec.select = s; return q; },
    maxTimeMS(n: number) { rec.maxTimeMS = n; return q; },
    lean() { rec.lean = true; return q; },
    then(res: (v: unknown) => unknown, rej: (e: unknown) => unknown) { return Promise.resolve(result).then(res, rej); },
  };
  return q;
}

describe('MongoDocumentRepository write state (queries)', () => {
  const org = '650000000000000000000001';
  const id = '650000000000000000000002';
  let sent: Sent[];
  let repo: MongoDocumentRepository;
  let results: Record<string, unknown[]>;
  const at = (i: number): Sent => sent[i] as Sent;
  const next = (op: string): unknown => (results[op] ?? []).shift();

  beforeEach(() => {
    sent = [];
    results = {};
    repo = new MongoDocumentRepository();
    sinon.stub(repo, 'serverClockMs').resolves(1_000_000);
    sinon.stub(DocumentModel, 'updateOne').callsFake(((filter: never, update: never, options: never) =>
      query(sent, { op: 'updateOne', filter, update, options }, { matchedCount: 1 })) as never);
    sinon.stub(DocumentModel, 'findOneAndUpdate').callsFake(((filter: never, update: never, options: never) =>
      query(sent, { op: 'findOneAndUpdate', filter, update, options }, next('findOneAndUpdate'))) as never);
    sinon.stub(DocumentModel, 'findOne').callsFake(((filter: never) => query(sent, { op: 'findOne', filter }, next('findOne'))) as never);
    sinon.stub(DocumentModel, 'exists').callsFake(((filter: never) => query(sent, { op: 'exists', filter }, next('exists'))) as never);
  });
  afterEach(() => sinon.restore());

  it('beginWrite prunes expired tokens only, caps at 64, adds the token and bumps in one write, and returns the post-update sequence', async () => {
    results.findOneAndUpdate = [{ writeSeq: 7 }];
    const out = await repo.beginWrite(org, id, 'tok', 300_000);
    expect(out).to.deep.equal({ writeSeq: 7 });
    const [prune, begin] = sent as [Sent, Sent];
    expect(prune.update).to.deep.equal({ $pull: { writers: { until: { $lt: 1_000_000 } } } });
    expect(begin.filter).to.deep.equal({ _id: id, orgId: org, 'writers.63': { $exists: false } });
    expect(begin.update).to.deep.equal({ $inc: { writeSeq: 1 }, $push: { writers: { t: 'tok', until: 1_300_000 } } });
    expect(begin.options).to.deep.include({ new: true, timestamps: false });
    expect(begin.select, 'select:false fields are not returned unless asked').to.equal('+writeSeq');
    expect(begin.maxTimeMS).to.equal(1000);
    expect(prune.options).to.deep.include({ timestamps: false });
  });

  it('beginWrite: a full document is busy, a missing one is null, and nothing is written in either case', async () => {
    results.findOneAndUpdate = [null, null];
    results.exists = [{ _id: id }, null];
    let busy: unknown;
    try { await repo.beginWrite(org, id, 't', 1000); } catch (e) { busy = e; }
    expect(busy).to.be.instanceOf(DocumentBusy);
    expect(await repo.beginWrite(org, id, 't', 1000)).to.equal(null);
  });

  it('renewWrite only moves a present token later ($max on the positional entry) and bumps nothing', async () => {
    await repo.renewWrite(org, id, 'tok', 60_000);
    expect(at(0).filter).to.deep.equal({ _id: id, orgId: org, 'writers.t': 'tok' });
    expect(at(0).update).to.deep.equal({ $max: { 'writers.$.until': 1_060_000 } });
    expect(at(0).options).to.deep.include({ timestamps: false });
  });

  it('endWrite removes this token only and bumps, without touching updatedAt', async () => {
    await repo.endWrite(org, id, 'tok');
    expect(at(0).filter).to.deep.equal({ _id: id, orgId: org });
    expect(at(0).update).to.deep.equal({ $pull: { writers: { t: 'tok' } }, $inc: { writeSeq: 1 } });
    expect(at(0).options).to.deep.include({ timestamps: false });
    expect(at(0).maxTimeMS).to.equal(1000);
  });

  it('bumpSeq returns the post-update sequence', async () => {
    results.findOneAndUpdate = [{ writeSeq: 3 }, null];
    expect(await repo.bumpSeq(org, id)).to.deep.equal({ writeSeq: 3 });
    expect(at(0).select).to.equal('+writeSeq');
    expect(await repo.bumpSeq(org, id)).to.equal(null);
  });

  it('adoptDirectUpload matches only an unfinished upload, clears the flag, stores the size and bumps; updatedAt stays', async () => {
    results.findOneAndUpdate = [{}, null];
    expect(await repo.adoptDirectUpload(org, id, 5)).to.equal(true);
    expect(await repo.adoptDirectUpload(org, id, 5)).to.equal(false);
    expect(at(0).filter).to.deep.equal({ _id: id, orgId: org, awaitingDirectUpload: true });
    expect(at(0).update).to.deep.equal({ $unset: { awaitingDirectUpload: '' }, $set: { sizeInBytes: 5 }, $inc: { writeSeq: 1 } });
    expect(at(0).options).to.deep.include({ timestamps: false });
  });

  it('correctSize is compare-and-set on the old size', async () => {
    await repo.correctSize(org, id, 5, 7);
    expect(at(0).filter).to.deep.equal({ _id: id, orgId: org, sizeInBytes: 5 });
    expect(at(0).update).to.deep.equal({ $set: { sizeInBytes: 7 } });
  });

  it('markAwaitingDirectUpload sets the patch and bumps in one write and returns the post-update sequence, not on the row', async () => {
    const doc = { writeSeq: 4, toJSON: () => ({ _id: id, orgId: org, writeSeq: 4, awaitingDirectUpload: true }) };
    results.findOneAndUpdate = [doc];
    const out = await repo.markAwaitingDirectUpload(org, id, { documentPath: 'p', awaitingDirectUpload: true, s3: { url: 'u' } });
    expect(out?.writeSeq).to.equal(4);
    expect(out?.row).to.not.have.property('writeSeq');
    expect(at(0).update).to.deep.equal({ $set: { documentPath: 'p', awaitingDirectUpload: true, s3: { url: 'u' } }, $inc: { writeSeq: 1 } });
    expect(at(0).options).to.deep.include({ new: true });
    expect(at(0).select).to.equal('+writeSeq');
  });

  it('getWithFingerprintState reads the clock first, selects the state by name, and returns the row without it', async () => {
    const doc = { writeSeq: 2, writers: [{ t: 'a', until: 9 }], toJSON: () => ({ _id: id, writeSeq: 2, writers: [{ t: 'a', until: 9 }] }) };
    results.findOne = [doc];
    const out = await repo.getWithFingerprintState(org, id);
    expect(out?.writeSeq).to.equal(2);
    expect(out?.writers).to.deep.equal([{ t: 'a', until: 9 }]);
    expect(out?.serverNow).to.equal(1_000_000);
    expect(out?.row).to.not.have.property('writeSeq');
    expect(out?.row).to.not.have.property('writers');
    expect(at(0).select).to.equal('+writeSeq +writers');
  });

  it('peekWriteState is a lean projection of the two fields; an absent state reads as zero and no writers', async () => {
    results.findOne = [{ _id: id }];
    expect(await repo.peekWriteState(org, id)).to.deep.equal({ writeSeq: 0, writers: [] });
    expect(at(0).select).to.equal('writeSeq writers');
    expect(at(0).lean).to.equal(true);
  });
});

describe('MongoDocumentRepository.serverClockMs', () => {
  const conn = DocumentModel.db as unknown as Record<string, unknown>;
  const original = Object.getOwnPropertyDescriptor(conn, 'db');
  afterEach(() => {
    delete conn.db;
    if (original) Object.defineProperty(conn, 'db', original);
  });
  // `db` is set when the connection opens; there is none here, so stand one in
  const withAdmin = (command: sinon.SinonStub) => {
    Object.defineProperty(conn, 'db', { configurable: true, get: () => ({ admin: () => ({ command }) }) });
    return { restore: () => delete conn.db };
  };

  it('reads localTime from hello, then from isMaster where hello is unknown, and keeps the offset for a few seconds', async () => {
    const t = new Date(Date.now() + 10_000);
    const command = sinon.stub();
    command.withArgs({ hello: 1 }).rejects(new Error('no such command'));
    command.withArgs({ isMaster: 1 }).resolves({ localTime: t });
    withAdmin(command);
    const repo = new MongoDocumentRepository();
    const a = await repo.serverClockMs();
    expect(Math.abs(a - t.getTime())).to.be.lessThan(100);
    const calls = command.callCount;
    await repo.serverClockMs();
    expect(command.callCount, 'cached offset').to.equal(calls);
  });

  it('fails closed when the reply has no finite local time, or the call fails', async () => {
    for (const reply of [{}, { localTime: 'soon' }]) {
      const command = sinon.stub().resolves(reply);
      const stub = withAdmin(command);
      let err: unknown;
      try { await new MongoDocumentRepository().serverClockMs(); } catch (e) { err = e; }
      expect(err, JSON.stringify(reply)).to.be.instanceOf(ServerClockUnavailable);
      stub.restore();
    }
    withAdmin(sinon.stub().rejects(new Error('down')));
    let err: unknown;
    try { await new MongoDocumentRepository().serverClockMs(); } catch (e) { err = e; }
    expect(err).to.be.instanceOf(ServerClockUnavailable);
  });
});

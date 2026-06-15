import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { DocumentOrgIdBackfillMigration } from '../../../../../src/modules/configuration_manager/services/migrations/document_orgid_backfill.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { DocumentModel } from '../../../../../src/modules/storage/schema/document.schema';
import { Org } from '../../../../../src/modules/user_management/schema/org.schema';

const makeLogger = () => ({
  info: sinon.stub(),
  warn: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
});

const makeKvStore = (flagValue: string | null = null) => ({
  get: sinon.stub().resolves(flagValue),
  set: sinon.stub().resolves(),
});

const fakeOrgId = new mongoose.Types.ObjectId();

describe('DocumentOrgIdBackfillMigration', () => {
  afterEach(() => sinon.restore());

  // -------------------------------------------------------------------------
  // Flag-based skip
  // -------------------------------------------------------------------------
  describe('migration flag', () => {
    it('skips immediately when KV flag is already "true"', async () => {
      const logger = makeLogger();
      const kv = makeKvStore('true');
      const m = new DocumentOrgIdBackfillMigration(logger as any, kv as any);

      const result = await m.run();

      expect(result).to.deep.equal({ updated: 0, skipped: true });
      expect(kv.get.calledOnceWith(configPaths.documentOrgIdBackfillMigration)).to.be.true;
      expect(kv.set.called).to.be.false;
      expect(logger.info.calledWith(
        'Document orgId backfill migration already completed; skipping',
      )).to.be.true;
    });

    it('proceeds (and warns) when kvStore.get throws', async () => {
      const logger = makeLogger();
      const kv = {
        get: sinon.stub().rejects(new Error('redis timeout')),
        set: sinon.stub().resolves(),
      };

      sinon.stub(DocumentModel, 'countDocuments').resolves(0);
      sinon.stub(DocumentModel, 'updateMany').resolves({ modifiedCount: 0, matchedCount: 0 } as any);

      const m = new DocumentOrgIdBackfillMigration(logger as any, kv as any);
      const result = await m.run();

      expect(logger.warn.called).to.be.true;
      // After the flag-read error it proceeds to the countDocuments check; since
      // countDocuments returns 0 the migration is marked done and skipped.
      expect(result.skipped).to.be.true;
    });
  });

  // -------------------------------------------------------------------------
  // No documents missing orgId
  // -------------------------------------------------------------------------
  describe('no documents need backfilling', () => {
    it('marks migration done and returns skipped when countDocuments is 0', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      sinon.stub(DocumentModel, 'countDocuments').resolves(0);

      const m = new DocumentOrgIdBackfillMigration(logger as any, kv as any);
      const result = await m.run();

      expect(result).to.deep.equal({ updated: 0, skipped: true });
      expect(kv.set.calledOnceWith(
        configPaths.documentOrgIdBackfillMigration,
        'true',
      )).to.be.true;
      expect(logger.info.calledWith(
        'All documents already have orgId — document orgId backfill migration not needed',
      )).to.be.true;
    });
  });

  // -------------------------------------------------------------------------
  // No org found
  // -------------------------------------------------------------------------
  describe('no organisation in the database', () => {
    it('returns skipped without setting flag when no org exists', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      sinon.stub(DocumentModel, 'countDocuments').resolves(3);

      // Simulate a Mongoose lean query chain returning null
      const chain: any = { sort: sinon.stub(), lean: sinon.stub() };
      chain.sort.returns(chain);
      chain.lean.returns(Promise.resolve(null));
      sinon.stub(Org, 'findOne').returns(chain as any);

      const m = new DocumentOrgIdBackfillMigration(logger as any, kv as any);
      const result = await m.run();

      expect(result).to.deep.equal({ updated: 0, skipped: true });
      expect(kv.set.called).to.be.false;
      expect(logger.warn.called).to.be.true;
    });
  });

  // -------------------------------------------------------------------------
  // Successful backfill
  // -------------------------------------------------------------------------
  describe('successful backfill', () => {
    it('runs updateMany and marks migration done', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      sinon.stub(DocumentModel, 'countDocuments').resolves(4);

      const chain: any = { sort: sinon.stub(), lean: sinon.stub() };
      chain.sort.returns(chain);
      chain.lean.returns(Promise.resolve({ _id: fakeOrgId }));
      sinon.stub(Org, 'findOne').returns(chain as any);

      sinon.stub(DocumentModel, 'updateMany').resolves({
        modifiedCount: 4,
        matchedCount: 4,
      } as any);

      const m = new DocumentOrgIdBackfillMigration(logger as any, kv as any);
      const result = await m.run();

      expect(result).to.deep.equal({ updated: 4, skipped: false });
      expect(kv.set.calledOnceWith(
        configPaths.documentOrgIdBackfillMigration,
        'true',
      )).to.be.true;

      const updateManyStub = DocumentModel.updateMany as sinon.SinonStub;
      expect(updateManyStub.calledOnce).to.be.true;
      const [filter, update] = updateManyStub.firstCall.args;
      expect(filter).to.deep.equal({
        $or: [{ orgId: { $exists: false } }, { orgId: null }],
      });
      expect(update).to.deep.equal({ $set: { orgId: fakeOrgId } });
    });

    it('logs orgId used for backfill', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null);
      sinon.stub(DocumentModel, 'countDocuments').resolves(2);

      const chain: any = { sort: sinon.stub(), lean: sinon.stub() };
      chain.sort.returns(chain);
      chain.lean.returns(Promise.resolve({ _id: fakeOrgId }));
      sinon.stub(Org, 'findOne').returns(chain as any);
      sinon.stub(DocumentModel, 'updateMany').resolves({ modifiedCount: 2, matchedCount: 2 } as any);

      const m = new DocumentOrgIdBackfillMigration(logger as any, kv as any);
      await m.run();

      // Info logged with the orgId
      const infoMessages: string[] = logger.info.args.map((a: any[]) => a[0]);
      expect(infoMessages.some((msg) => msg.includes('backfill complete'))).to.be.true;
    });
  });

  // -------------------------------------------------------------------------
  // markDone failure is swallowed
  // -------------------------------------------------------------------------
  describe('markDone failure', () => {
    it('logs an error but does not re-throw when kvStore.set fails after successful updateMany', async () => {
      const logger = makeLogger();
      const kv = {
        get: sinon.stub().resolves(null),
        set: sinon.stub().rejects(new Error('etcd write error')),
      };
      sinon.stub(DocumentModel, 'countDocuments').resolves(1);

      const chain: any = { sort: sinon.stub(), lean: sinon.stub() };
      chain.sort.returns(chain);
      chain.lean.returns(Promise.resolve({ _id: fakeOrgId }));
      sinon.stub(Org, 'findOne').returns(chain as any);
      sinon.stub(DocumentModel, 'updateMany').resolves({ modifiedCount: 1, matchedCount: 1 } as any);

      const m = new DocumentOrgIdBackfillMigration(logger as any, kv as any);
      // Should NOT throw
      const result = await m.run();

      expect(result).to.deep.equal({ updated: 1, skipped: false });
      expect(logger.error.called).to.be.true;
    });
  });
});

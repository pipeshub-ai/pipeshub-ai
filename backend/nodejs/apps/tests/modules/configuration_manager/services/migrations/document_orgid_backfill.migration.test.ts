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
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeKvStore = (existingFlag: string | null = null) => ({
  get: sinon.stub().callsFake((path: string) => {
    if (path === configPaths.documentOrgIdMigration) {
      return Promise.resolve(existingFlag);
    }
    return Promise.resolve(null);
  }),
  set: sinon.stub().resolves(),
});

const missingOrgIdFilter = {
  $or: [{ orgId: { $exists: false } }, { orgId: null }],
};

describe('DocumentOrgIdBackfillMigration', () => {
  const orgId = new mongoose.Types.ObjectId();

  afterEach(() => {
    sinon.restore();
  });

  it('skips when migration flag is already set', async () => {
    const kv = makeKvStore('true');
    const countStub = sinon.stub(DocumentModel, 'countDocuments');

    const result = await new DocumentOrgIdBackfillMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result).to.deep.equal({ updated: 0, errored: 0 });
    expect(countStub.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('marks done immediately when no documents are missing orgId', async () => {
    const kv = makeKvStore(null);
    sinon.stub(DocumentModel, 'countDocuments').resolves(0);
    const findOneStub = sinon.stub(Org, 'findOne');

    const result = await new DocumentOrgIdBackfillMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result).to.deep.equal({ updated: 0, errored: 0 });
    expect(kv.set.calledWith(configPaths.documentOrgIdMigration, 'true')).to.equal(true);
    expect(findOneStub.called).to.equal(false);
  });

  it('backfills documents with the single org id and writes flag', async () => {
    const kv = makeKvStore(null);
    sinon.stub(DocumentModel, 'countDocuments').resolves(5);
    sinon.stub(Org, 'findOne').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves({ _id: orgId }),
      }),
    } as any);
    const updateManyStub = sinon
      .stub(DocumentModel, 'updateMany')
      .resolves({ modifiedCount: 5 } as any);

    const result = await new DocumentOrgIdBackfillMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result).to.deep.equal({ updated: 5, errored: 0 });
    expect(updateManyStub.calledOnce).to.equal(true);
    expect(updateManyStub.firstCall.args[0]).to.deep.equal(missingOrgIdFilter);
    expect(updateManyStub.firstCall.args[1]).to.deep.equal({
      $set: { orgId },
    });
    expect(kv.set.calledWith(configPaths.documentOrgIdMigration, 'true')).to.equal(true);
  });

  it('returns errored=1 and skips flag when no org is found', async () => {
    const kv = makeKvStore(null);
    sinon.stub(DocumentModel, 'countDocuments').resolves(3);
    sinon.stub(Org, 'findOne').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves(null),
      }),
    } as any);
    const updateManyStub = sinon.stub(DocumentModel, 'updateMany');

    const result = await new DocumentOrgIdBackfillMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result).to.deep.equal({ updated: 0, errored: 1 });
    expect(updateManyStub.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('continues when reading the migration flag fails', async () => {
    const kv = {
      get: sinon.stub().rejects(new Error('kv unavailable')),
      set: sinon.stub().resolves(),
    };
    sinon.stub(DocumentModel, 'countDocuments').resolves(2);
    sinon.stub(Org, 'findOne').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves({ _id: orgId }),
      }),
    } as any);
    sinon.stub(DocumentModel, 'updateMany').resolves({ modifiedCount: 2 } as any);

    const result = await new DocumentOrgIdBackfillMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result).to.deep.equal({ updated: 2, errored: 0 });
    expect(kv.set.calledWith(configPaths.documentOrgIdMigration, 'true')).to.equal(true);
  });

  it('does not write flag when updateMany fails', async () => {
    const kv = makeKvStore(null);
    sinon.stub(DocumentModel, 'countDocuments').resolves(4);
    sinon.stub(Org, 'findOne').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves({ _id: orgId }),
      }),
    } as any);
    sinon.stub(DocumentModel, 'updateMany').rejects(new Error('db write failed'));

    const result = await new DocumentOrgIdBackfillMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(1);
    expect(result.updated).to.equal(0);
    expect(kv.set.called).to.equal(false);
  });

  it('warns when writing flag fails after successful backfill', async () => {
    const logger = makeLogger();
    const kv = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().rejects(new Error('set failed')),
    };
    sinon.stub(DocumentModel, 'countDocuments').resolves(1);
    sinon.stub(Org, 'findOne').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves({ _id: orgId }),
      }),
    } as any);
    sinon.stub(DocumentModel, 'updateMany').resolves({ modifiedCount: 1 } as any);

    const result = await new DocumentOrgIdBackfillMigration(
      logger as any,
      kv as any,
    ).run();

    expect(result).to.deep.equal({ updated: 1, errored: 0 });
    expect(logger.warn.called).to.equal(true);
  });

  it('uses modifiedCount ?? 0 when modifiedCount is undefined', async () => {
    const kv = makeKvStore(null);
    sinon.stub(DocumentModel, 'countDocuments').resolves(3);
    sinon.stub(Org, 'findOne').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves({ _id: orgId }),
      }),
    } as any);
    sinon.stub(DocumentModel, 'updateMany').resolves({} as any);

    const result = await new DocumentOrgIdBackfillMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result).to.deep.equal({ updated: 0, errored: 0 });
    expect(kv.set.calledWith(configPaths.documentOrgIdMigration, 'true')).to.equal(true);
  });
});

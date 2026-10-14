import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  DUPLICATE_GROUP_NAMES_MESSAGE,
  UserGroupNameIndexMigration,
} from '../../../../../src/modules/configuration_manager/services/migrations/user_group_name_index.migration';
import {
  ACTIVE_GROUP_NAME_INDEX,
  UserGroups,
} from '../../../../../src/modules/user_management/schema/userGroup.schema';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeCollection = (listIndexes: () => Promise<Array<{ name: string }>>) => ({
  listIndexes: sinon.stub().callsFake(() => ({ toArray: listIndexes })),
  createIndex: sinon.stub().resolves(ACTIVE_GROUP_NAME_INDEX.options.name),
});

describe('UserGroupNameIndexMigration', () => {
  afterEach(() => {
    sinon.restore();
  });

  it('builds the partial unique index on orgId and name when there are no duplicates', async () => {
    const collection = makeCollection(() => Promise.resolve([{ name: '_id_' }, { name: 'slug_1' }]));
    sinon.replace(UserGroups, 'collection', collection as any);
    const aggregate = sinon.stub(UserGroups, 'aggregate').resolves([] as any);

    const result = await new UserGroupNameIndexMigration(makeLogger() as any).run();

    expect(result).to.deep.equal({ status: 'created', duplicateSets: 0, errored: 0 });
    expect(aggregate.firstCall.args[0][0]).to.deep.equal({ $match: { isDeleted: false } });
    expect(collection.createIndex.calledOnceWith(
      { orgId: 1, name: 1 },
      {
        name: 'orgId_1_name_1_active_unique',
        unique: true,
        partialFilterExpression: { isDeleted: false },
      },
    )).to.equal(true);
  });

  it('reports existing duplicates, changes nothing, and skips the index', async () => {
    const collection = makeCollection(() => Promise.resolve([{ name: '_id_' }]));
    sinon.replace(UserGroups, 'collection', collection as any);
    const orgId = new mongoose.Types.ObjectId();
    const ids = [new mongoose.Types.ObjectId(), new mongoose.Types.ObjectId()];
    sinon.stub(UserGroups, 'aggregate').resolves([
      { _id: { orgId, name: 'Engineering' }, groupIds: ids },
    ] as any);
    const writes = [
      sinon.stub(UserGroups, 'updateOne'),
      sinon.stub(UserGroups, 'updateMany'),
      sinon.stub(UserGroups, 'deleteOne'),
      sinon.stub(UserGroups, 'deleteMany'),
    ];
    const logger = makeLogger();

    const result = await new UserGroupNameIndexMigration(logger as any).run();

    expect(result).to.deep.equal({ status: 'skipped_duplicates', duplicateSets: 1, errored: 0 });
    expect(collection.createIndex.called).to.equal(false);
    expect(writes.every((w) => !w.called)).to.equal(true);
    expect(logger.error.calledOnce).to.equal(true);
    const [message, meta] = logger.error.firstCall.args;
    expect(message).to.equal(DUPLICATE_GROUP_NAMES_MESSAGE);
    expect(message).to.include('Nothing was renamed or deleted');
    expect(message).to.not.match(/team/i);
    expect(meta.duplicates).to.deep.equal([
      { orgId: String(orgId), name: 'Engineering', groupIds: ids.map(String) },
    ]);
  });

  it('does nothing when the index already exists', async () => {
    const collection = makeCollection(() =>
      Promise.resolve([{ name: '_id_' }, { name: ACTIVE_GROUP_NAME_INDEX.options.name }]),
    );
    sinon.replace(UserGroups, 'collection', collection as any);
    const aggregate = sinon.stub(UserGroups, 'aggregate');

    const result = await new UserGroupNameIndexMigration(makeLogger() as any).run();

    expect(result.status).to.equal('already_present');
    expect(aggregate.called).to.equal(false);
    expect(collection.createIndex.called).to.equal(false);
  });

  it('creates the index on a fresh install where the collection does not exist yet', async () => {
    const collection = makeCollection(() =>
      Promise.reject(Object.assign(new Error('ns does not exist'), { code: 26 })),
    );
    sinon.replace(UserGroups, 'collection', collection as any);
    sinon.stub(UserGroups, 'aggregate').resolves([] as any);

    const result = await new UserGroupNameIndexMigration(makeLogger() as any).run();

    expect(result.status).to.equal('created');
    expect(collection.createIndex.calledOnce).to.equal(true);
  });

  it('lists the groups when a duplicate appears between the check and the index build', async () => {
    const collection = makeCollection(() => Promise.resolve([{ name: '_id_' }]));
    collection.createIndex.rejects(Object.assign(new Error('E11000 duplicate key error'), { code: 11000 }));
    sinon.replace(UserGroups, 'collection', collection as any);
    const orgId = new mongoose.Types.ObjectId();
    const ids = [new mongoose.Types.ObjectId(), new mongoose.Types.ObjectId()];
    const aggregate = sinon.stub(UserGroups, 'aggregate');
    aggregate.onFirstCall().resolves([] as any);
    aggregate.onSecondCall().resolves([{ _id: { orgId, name: 'Ops' }, groupIds: ids }] as any);
    const logger = makeLogger();

    const result = await new UserGroupNameIndexMigration(logger as any).run();

    expect(result).to.deep.equal({ status: 'skipped_duplicates', duplicateSets: 1, errored: 0 });
    expect(logger.error.calledOnce).to.equal(true);
    expect(logger.error.firstCall.args[0]).to.equal(DUPLICATE_GROUP_NAMES_MESSAGE);
    expect(logger.error.firstCall.args[1].duplicates).to.deep.equal([
      { orgId: String(orgId), name: 'Ops', groupIds: ids.map(String) },
    ]);
  });

  it('reports other failures without throwing', async () => {
    const collection = makeCollection(() => Promise.reject(new Error('Mongo down')));
    sinon.replace(UserGroups, 'collection', collection as any);

    const result = await new UserGroupNameIndexMigration(makeLogger() as any).run();

    expect(result).to.deep.equal({ status: 'failed', duplicateSets: 0, errored: 1 });
  });
});

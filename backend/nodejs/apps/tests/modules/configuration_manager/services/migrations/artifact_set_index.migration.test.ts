import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ArtifactSetIndexMigration } from '../../../../../src/modules/configuration_manager/services/migrations/artifact_set_index.migration';
import {
  ARTIFACT_SET_INDEX,
  DocumentModel,
} from '../../../../../src/modules/storage/schema/document.schema';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const OLD = {
  name: 'orgId_1_artifactSet.virtualRecordId_1_artifactSet.rev_1',
  key: { orgId: 1, 'artifactSet.virtualRecordId': 1, 'artifactSet.rev': 1 },
  unique: true,
};
const NEW = {
  name: 'orgId_1_artifactSet.virtualRecordId_1_artifactSet.rev_1_artifactSet.writeId_1',
  key: ARTIFACT_SET_INDEX.keys,
  unique: true,
};

const makeCollection = (indexes: () => Promise<unknown[]>) => ({
  indexes: sinon.stub().callsFake(indexes),
  createIndex: sinon.stub().resolves(NEW.name),
  dropIndex: sinon.stub().resolves(),
});

describe('ArtifactSetIndexMigration', () => {
  afterEach(() => {
    sinon.restore();
  });

  it('builds the index of one document per stored write before it drops the one per revision', async () => {
    const collection = makeCollection(() => Promise.resolve([{ name: '_id_', key: { _id: 1 } }, OLD]));
    sinon.replace(DocumentModel, 'collection', collection as any);

    const result = await new ArtifactSetIndexMigration(makeLogger() as any).run();

    expect(result).to.deep.equal({ status: 'replaced' });
    expect(collection.createIndex.calledOnceWith(ARTIFACT_SET_INDEX.keys, ARTIFACT_SET_INDEX.options)).to.equal(true);
    expect(collection.dropIndex.calledOnceWith(OLD.name)).to.equal(true);
    expect(collection.createIndex.calledBefore(collection.dropIndex)).to.equal(true);
  });

  it('only drops the old index when the schema already built the new one', async () => {
    const collection = makeCollection(() => Promise.resolve([OLD, NEW]));
    sinon.replace(DocumentModel, 'collection', collection as any);

    expect(await new ArtifactSetIndexMigration(makeLogger() as any).run()).to.deep.equal({ status: 'replaced' });
    expect(collection.createIndex.called).to.equal(false);
    expect(collection.dropIndex.calledOnceWith(OLD.name)).to.equal(true);
  });

  it('does nothing once the old index is gone', async () => {
    const collection = makeCollection(() => Promise.resolve([NEW]));
    sinon.replace(DocumentModel, 'collection', collection as any);

    expect(await new ArtifactSetIndexMigration(makeLogger() as any).run()).to.deep.equal({ status: 'already_done' });
    expect(collection.createIndex.called || collection.dropIndex.called).to.equal(false);
  });

  it('leaves a non-unique index on the same key alone', async () => {
    const collection = makeCollection(() => Promise.resolve([{ ...OLD, unique: undefined }, NEW]));
    sinon.replace(DocumentModel, 'collection', collection as any);

    expect(await new ArtifactSetIndexMigration(makeLogger() as any).run()).to.deep.equal({ status: 'already_done' });
    expect(collection.dropIndex.called).to.equal(false);
  });

  it('has nothing to do on a database without the collection', async () => {
    const collection = makeCollection(() => Promise.reject(Object.assign(new Error('ns not found'), { code: 26 })));
    sinon.replace(DocumentModel, 'collection', collection as any);

    expect(await new ArtifactSetIndexMigration(makeLogger() as any).run()).to.deep.equal({ status: 'no_collection' });
  });

  it('counts an index another process dropped first as done', async () => {
    const collection = makeCollection(() => Promise.resolve([OLD, NEW]));
    collection.dropIndex.rejects(Object.assign(new Error('index not found'), { code: 27 }));
    sinon.replace(DocumentModel, 'collection', collection as any);

    expect(await new ArtifactSetIndexMigration(makeLogger() as any).run()).to.deep.equal({ status: 'replaced' });
  });

  it('never throws: a failure is logged and the next boot tries again', async () => {
    const collection = makeCollection(() => Promise.resolve([OLD]));
    collection.createIndex.rejects(new Error('disk full'));
    sinon.replace(DocumentModel, 'collection', collection as any);
    const logger = makeLogger();

    expect(await new ArtifactSetIndexMigration(logger as any).run()).to.deep.equal({ status: 'failed' });
    expect(collection.dropIndex.called).to.equal(false);
    expect(logger.error.calledOnce).to.equal(true);
  });
});

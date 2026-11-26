/**
 * CPD-2 on a MongoDB replica set: the `CatalogStore` contract over the release
 * rows and `cpCatalogItems` (CPD2-40..CPD2-48), the migration's indexes
 * (CPD2-49), and writes through the transaction's session. Run with
 * MONGO_IT_URI (a replica-set URI) as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import type { Db } from 'mongodb';
import { CompositeCatalogStore } from '../../../src/modules/control-plane/adapters/composite-catalog-store';
import { CATALOG_MIGRATION } from '../../../src/libs/repositories/control-plane/migrations/mongo';
import { CatalogItemModel } from '../../../src/libs/repositories/control-plane/catalog/schema/catalog.schema';
import { MongoOrgCatalogStore } from '../../../src/libs/repositories/control-plane/catalog/mongo';
import { ReleaseCatalogStore } from '../../../src/modules/control-plane/adapters/release-catalog-store';
import { catalogItem, immediateTx } from './support/catalog-fixtures';
import { ORG_A } from './support/cp-objects';
import {
  describeCatalogStoreContract,
  RELEASE_FIXTURES,
} from './support/catalog-store-contract';

const uri = (): string => {
  const value = process.env.MONGO_IT_URI;
  if (!value)
    throw new Error(
      'MONGO_IT_URI (a replica-set URI) must be set for the control-plane Mongo tests',
    );
  return value;
};

const db = (): Db => mongoose.connection.db!;
const indexNames = async (): Promise<string[]> =>
  (await db().collection(CatalogItemModel.collection.collectionName).indexes())
    .map((index) => index.name!)
    .sort();

describe('control-plane catalog on a MongoDB replica set (CPD-2)', function () {
  this.timeout(60_000);

  before(async () => {
    await mongoose.connect(uri(), {
      dbName: `cp_catalog_it_${Date.now()}_${process.pid}`,
    });
    await CATALOG_MIGRATION.up(db());
  });

  after(async () => {
    if (mongoose.connection.readyState === mongoose.ConnectionStates.connected)
      await mongoose.connection.dropDatabase();
    await mongoose.disconnect();
  });

  describeCatalogStoreContract(
    'release + mongo replica set',
    async (release) => {
      await CatalogItemModel.deleteMany({});
      return new CompositeCatalogStore(
        new ReleaseCatalogStore(release),
        new MongoOrgCatalogStore(),
      );
    },
  );

  it('CPD2-49: the migration builds the 02-lld indexes, idempotently, and down keeps the rows', async () => {
    expect(await indexNames()).to.deep.equal([
      '_id_',
      'cp_catalog_identity',
      'cp_catalog_order',
    ]);
    expect((await CATALOG_MIGRATION.up(db())).created).to.deep.equal([]);
    const store = new MongoOrgCatalogStore();
    const item = catalogItem();
    await store.insert(immediateTx(ORG_A), item);
    expect((await CATALOG_MIGRATION.down(db())).dropped).to.have.length(2);
    expect(await indexNames()).to.deep.equal(['_id_']);
    expect(await store.get(ORG_A, item.ref)).to.deep.equal(item);
    expect((await CATALOG_MIGRATION.up(db())).created).to.have.length(2);
    expect(await indexNames()).to.deep.equal([
      '_id_',
      'cp_catalog_identity',
      'cp_catalog_order',
    ]);
  });

  it("CPD2-49: the identity index refuses a second row of one version, whatever the ref's spelling", async () => {
    await CatalogItemModel.deleteMany({});
    const item = catalogItem();
    await CatalogItemModel.create({
      _id: `org-a|other-id`,
      orgId: ORG_A,
      ref: item.ref,
      kind: item.kind,
      publisher: item.publisher,
      name: item.name,
      version: item.version,
      sortName: 'x',
      createdAt: 1,
      deprecated: false,
      body: '{}',
    });
    const store = new CompositeCatalogStore(
      new ReleaseCatalogStore(RELEASE_FIXTURES),
      new MongoOrgCatalogStore(),
    );
    try {
      await store.insert(immediateTx(ORG_A), item);
      expect.fail('expected ItemExists');
    } catch (error) {
      expect((error as Error).name).to.equal('ItemExists');
    }
  });

  it("writes go through the transaction's session: an aborted transaction stores nothing", async () => {
    await CatalogItemModel.deleteMany({});
    const store = new CompositeCatalogStore(
      new ReleaseCatalogStore(RELEASE_FIXTURES),
      new MongoOrgCatalogStore(),
    );
    const item = catalogItem();
    const session = await mongoose.startSession();
    try {
      session.startTransaction();
      await store.insert(immediateTx(ORG_A, session), item);
      await session.abortTransaction();
    } finally {
      await session.endSession();
    }
    expect(await store.get(ORG_A, item.ref)).to.equal(null);
  });
});

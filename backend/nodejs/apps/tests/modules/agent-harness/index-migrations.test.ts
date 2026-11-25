/**
 * The harness's index migrations over a small in-memory stand-in for the
 * driver's `Db` (collections and their indexes by name): up is idempotent by
 * name and builds only what is missing, down drops in reverse and keeps every
 * document, and a failed build names the migration and index. The same
 * migrations against a replica set: harness-migrations.mongo.itest.ts.
 */
import { expect } from 'chai';
import {
  indexMigration,
  IndexDefinition,
} from '../../../src/libs/utils/mongo-index-migration';
import {
  migrateHarnessIndexes,
  revertHarnessIndexes,
  HARNESS_MIGRATIONS,
} from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  ERASURE_STORE_MIGRATION,
  migrateErasureStore,
  revertErasureStore,
} from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  migrateWorkspaceStore,
  revertWorkspaceStore,
  WORKSPACE_STORE_MIGRATION,
} from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  LEGACY_DISPATCH_OUTBOX,
  migrateHarnessStore,
} from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';

interface FakeIndex {
  name: string;
  key: Record<string, unknown>;
  options?: Record<string, unknown>;
}

class FakeDb {
  readonly collections = new Map<string, { indexes: FakeIndex[]; count: number }>();
  readonly log: string[] = [];
  failCreate: Error | null = null;
  failDrop: (Error & { code?: number }) | null = null;

  seed(name: string, indexes: FakeIndex[] = [], count = 0): this {
    this.collections.set(name, { indexes: [{ name: '_id_', key: { _id: 1 } }, ...indexes], count });
    return this;
  }

  listCollections(filter: { name: string }) {
    return {
      toArray: async () => (this.collections.has(filter.name) ? [{ name: filter.name }] : []),
    };
  }

  collection(name: string) {
    return {
      indexes: async () => [...(this.collections.get(name)?.indexes ?? [])],
      createIndex: async (key: Record<string, unknown>, options: Record<string, unknown> & { name: string }) => {
        if (this.failCreate !== null) throw this.failCreate;
        if (!this.collections.has(name)) this.seed(name);
        this.collections.get(name)!.indexes.push({ name: options.name, key, options });
        this.log.push(`create ${name}.${options.name}`);
        return options.name;
      },
      dropIndex: async (index: string) => {
        if (this.failDrop !== null) throw this.failDrop;
        const entry = this.collections.get(name)!;
        entry.indexes = entry.indexes.filter((i) => i.name !== index);
        this.log.push(`drop ${name}.${index}`);
      },
      estimatedDocumentCount: async () => this.collections.get(name)?.count ?? 0,
    };
  }
}

const A: IndexDefinition = { collection: 'things', name: 'byOrg', key: { orgId: 1 } };
const B: IndexDefinition = {
  collection: 'things',
  name: 'uniqueKey',
  key: { orgId: 1, key: -1 },
  unique: true,
  partialFilterExpression: { key: { $exists: true } },
  expireAfterSeconds: 60,
};
const C: IndexDefinition = { collection: 'others', name: 'byAge', key: { createdAt: 1 } };

describe('agent harness index migrations', () => {
  it('up builds only the missing indexes, with their options, and is idempotent by name', async () => {
    const db = new FakeDb().seed('things', [{ name: 'byOrg', key: { orgId: 1 } }]);
    const migration = indexMigration('m1', [A, B, C]);
    expect(await migration.up(db as never)).to.deep.equal({
      created: ['things.uniqueKey', 'others.byAge'],
      dropped: [],
    });
    const built = db.collections.get('things')!.indexes.find((i) => i.name === 'uniqueKey')!;
    expect(built.options).to.deep.equal({
      name: 'uniqueKey',
      unique: true,
      partialFilterExpression: { key: { $exists: true } },
      expireAfterSeconds: 60,
    });
    expect(db.collections.get('others')!.indexes.find((i) => i.name === 'byAge')!.options).to.deep.equal({
      name: 'byAge',
    });
    expect(await migration.up(db as never)).to.deep.equal({ created: [], dropped: [] });
  });

  it('up drops a retired index where an earlier build left it, before it builds; neither up nor down brings it back', async () => {
    const db = new FakeDb().seed('things', [{ name: 'oldUnique', key: { orgId: 1 } }]);
    const migration = indexMigration('m1', [A], { retired: [{ collection: 'things', name: 'oldUnique' }] });
    expect(migration.retired).to.deep.equal([{ collection: 'things', name: 'oldUnique' }]);
    expect(await migration.up(db as never)).to.deep.equal({ created: ['things.byOrg'], dropped: ['things.oldUnique'] });
    expect(db.collections.get('things')!.indexes.map((i) => i.name)).to.not.include('oldUnique');
    expect(db.collections.get('things')!.indexes.map((i) => i.name)).to.include('byOrg');
    expect(await migration.up(db as never)).to.deep.equal({ created: [], dropped: [] });
    expect(await migration.down(db as never)).to.deep.equal({ created: [], dropped: ['things.byOrg'] });
    expect(db.collections.get('things')!.indexes.map((i) => i.name)).to.not.include.members(['byOrg', 'oldUnique']);
    expect(indexMigration('m2', [A]).retired).to.deep.equal([]);
    // On a fresh database there is nothing to drop.
    expect(await migration.up(new FakeDb() as never)).to.deep.equal({ created: ['things.byOrg'], dropped: [] });
  });

  it('a failed build names the migration and the index, and keeps the cause', async () => {
    const db = new FakeDb();
    const cause = new Error('IndexOptionsConflict');
    db.failCreate = cause;
    let thrown: (Error & { cause?: unknown }) | undefined;
    try {
      await indexMigration('m1', [A]).up(db as never);
    } catch (error) {
      thrown = error as Error;
    }
    expect(thrown?.message).to.equal('Migration m1 could not build things.byOrg: IndexOptionsConflict');
    expect(thrown?.cause).to.equal(cause);
  });

  it('down drops its indexes in reverse, keeps the others, and skips one already gone', async () => {
    const db = new FakeDb();
    const migration = indexMigration('m1', [A, B, C]);
    await migration.up(db as never);
    db.seed('untouched', [{ name: 'keep', key: { a: 1 } }]);
    db.collections.get('others')!.indexes = db.collections.get('others')!.indexes.filter((i) => i.name !== 'byAge');
    db.log.length = 0;
    expect(await migration.down(db as never)).to.deep.equal({
      created: [],
      dropped: ['things.uniqueKey', 'things.byOrg'],
    });
    expect(db.log).to.deep.equal(['drop things.uniqueKey', 'drop things.byOrg']);
    expect(db.collections.get('untouched')!.indexes.map((i) => i.name)).to.deep.equal(['_id_', 'keep']);
  });

  it("down tolerates another replica's drop (IndexNotFound) and rethrows anything else", async () => {
    const db = new FakeDb();
    const migration = indexMigration('m1', [A]);
    await migration.up(db as never);
    db.failDrop = Object.assign(new Error('index not found'), { code: 27 });
    expect(await migration.down(db as never)).to.deep.equal({ created: [], dropped: [] });
    db.failDrop = Object.assign(new Error('not primary'), { code: 10107 });
    let thrown: unknown;
    try {
      await migration.down(db as never);
    } catch (error) {
      thrown = error;
    }
    expect((thrown as Error).message).to.equal('not primary');
  });

  it('the harness runner applies migrations in order and reverts them in reverse', async () => {
    const db = new FakeDb();
    const first = indexMigration('first', [A]);
    const second = indexMigration('second', [C]);
    expect(await migrateHarnessIndexes(db as never, [first, second])).to.deep.equal({
      created: ['things.byOrg', 'others.byAge'],
      dropped: [],
    });
    expect(await revertHarnessIndexes(db as never, [first, second])).to.deep.equal({
      created: [],
      dropped: ['others.byAge', 'things.byOrg'],
    });
  });

  it('the erasure and workspace stores build and drop exactly their declared indexes', async () => {
    for (const [migration, up, down] of [
      [ERASURE_STORE_MIGRATION, migrateErasureStore, revertErasureStore],
      [WORKSPACE_STORE_MIGRATION, migrateWorkspaceStore, revertWorkspaceStore],
    ] as const) {
      const db = new FakeDb();
      const labels = migration.indexes.map((i) => `${i.collection}.${i.name}`);
      expect((await up(db as never)).created, migration.id).to.deep.equal(labels);
      expect((await down(db as never)).dropped, migration.id).to.deep.equal([...labels].reverse());
    }
  });

  describe('startup migration (migrateHarnessStore)', () => {
    const runs = AgentRunModel.collection.collectionName;

    it('drops the legacy run idempotency index, counts orphaned v1 dispatch rows, and runs every migration', async () => {
      const db = new FakeDb()
        .seed(runs, [
          { name: 'legacy_idem', key: { orgId: 1, sessionId: 1, idempotencyKey: 1 } },
          { name: 'other_order', key: { sessionId: 1, orgId: 1, idempotencyKey: 1 } },
        ])
        .seed(LEGACY_DISPATCH_OUTBOX, [], 7);
      const result = await migrateHarnessStore(db as never);
      expect(result.droppedIndexes).to.deep.equal(['legacy_idem']);
      expect(result.orphanedDispatchRows).to.equal(7);
      expect(db.collections.get(runs)!.indexes.map((i) => i.name)).to.include('other_order');
      const declared = HARNESS_MIGRATIONS.flatMap((m) => m.indexes.map((i) => `${i.collection}.${i.name}`));
      expect(result.createdIndexes).to.deep.equal(declared);
    });

    it('on a fresh database drops nothing and counts no orphans', async () => {
      const result = await migrateHarnessStore(new FakeDb() as never);
      expect(result.droppedIndexes).to.deep.equal([]);
      expect(result.orphanedDispatchRows).to.equal(0);
    });
  });
});

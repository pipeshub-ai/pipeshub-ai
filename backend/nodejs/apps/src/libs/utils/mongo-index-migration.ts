import type { Db, Document } from 'mongodb';

/**
 * One index a migration owns. Written out literally rather than read from the
 * schema, so a later schema edit cannot change what an already-shipped `up`
 * builds or `down` drops; C-15 checks the two agree.
 */
export interface IndexDefinition {
  collection: string;
  /** For an index the schema leaves unnamed: Mongo's default name, so `up` matches what `autoIndex` built. */
  name: string;
  key: Record<string, 1 | -1>;
  unique?: boolean;
  partialFilterExpression?: Document;
  expireAfterSeconds?: number;
}

export interface IndexMigrationResult {
  created: string[];
  dropped: string[];
}

/** An index an earlier build of a migration made and a later one replaced. */
export interface RetiredIndex {
  collection: string;
  name: string;
}

export interface HarnessMigration {
  readonly id: string;
  readonly indexes: readonly IndexDefinition[];
  /** Dropped by `up` wherever an earlier build left them; never rebuilt, by `up` or `down`. */
  readonly retired: readonly RetiredIndex[];
  up(db: Db): Promise<IndexMigrationResult>;
  down(db: Db): Promise<IndexMigrationResult>;
}

export const indexLabel = (index: IndexDefinition): string =>
  `${index.collection}.${index.name}`;

const exists = async (db: Db, name: string): Promise<boolean> =>
  (await db.listCollections({ name }, { nameOnly: true }).toArray()).length > 0;

const present = async (db: Db, index: RetiredIndex): Promise<boolean> =>
  (await exists(db, index.collection)) &&
  (await db.collection(index.collection).indexes()).some(
    (found) => found.name === index.name,
  );

/**
 * Up builds each missing index and is idempotent by name; down drops them and
 * keeps every document, so a rollback loses no data and the next up rebuilds
 * over the same documents. Builds run one at a time: on MongoDB 4.2+ each is a
 * hybrid build that holds an exclusive lock only at its start and end.
 */
export const indexMigration = (
  id: string,
  indexes: readonly IndexDefinition[],
  options: { retired?: readonly RetiredIndex[] } = {},
): HarnessMigration => ({
  id,
  indexes,
  retired: options.retired ?? [],
  async up(db) {
    const created: string[] = [];
    const dropped: string[] = [];
    // First, so a retired unique index never refuses what its replacement allows.
    for (const index of options.retired ?? []) {
      if (!(await present(db, index))) continue;
      await db.collection(index.collection).dropIndex(index.name);
      dropped.push(`${index.collection}.${index.name}`);
    }
    for (const index of indexes) {
      if (await present(db, index)) continue;
      try {
        await db.collection(index.collection).createIndex(index.key, {
          name: index.name,
          ...(index.unique === true ? { unique: true } : {}),
          ...(index.partialFilterExpression !== undefined
            ? { partialFilterExpression: index.partialFilterExpression }
            : {}),
          ...(index.expireAfterSeconds !== undefined
            ? { expireAfterSeconds: index.expireAfterSeconds }
            : {}),
        });
      } catch (error) {
        throw Object.assign(
          new Error(
            `Migration ${id} could not build ${indexLabel(index)}: ${
              error instanceof Error ? error.message : String(error)
            }`,
          ),
          { cause: error },
        );
      }
      created.push(indexLabel(index));
    }
    return { created, dropped };
  },
  async down(db) {
    const dropped: string[] = [];
    for (const index of [...indexes].reverse()) {
      if (!(await present(db, index))) continue;
      try {
        await db.collection(index.collection).dropIndex(index.name);
      } catch (error) {
        // IndexNotFound: another replica's down dropped it first.
        if ((error as { code?: number }).code !== 27) throw error;
        continue;
      }
      dropped.push(indexLabel(index));
    }
    return { created: [], dropped };
  },
});

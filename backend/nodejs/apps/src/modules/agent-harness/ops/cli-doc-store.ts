import type { Db } from 'mongodb';
import { connectMongo } from '../../../libs/repositories/mongo';
import { initDocStore } from '../../../libs/repositories/doc-store';
import { docStore } from '../../../libs/repositories/store-select';

/** The document store an operator CLI works on, open; `db` is MongoDB's, null on PostgreSQL. */
export interface CliDocStore {
  postgres: boolean;
  db: Db | null;
  close(): Promise<void>;
}

/** `--mongo-uri` and `--db` name the store only on MongoDB: a PostgreSQL install's CLI connects as the application. */
export const needsMongoOptions = (): boolean => docStore() !== 'postgres';

/**
 * Opens the store `DOC_STORE` selects: on PostgreSQL as the application does (PG_NODE_URL through the pooler, and
 * PG_JOBS_URL for the reads across orgs a migration or backfill makes); on MongoDB the database the options name.
 */
export async function openCliDocStore(
  cli: string,
  mongo: { uri?: string | undefined; db?: string | undefined },
): Promise<CliDocStore> {
  if (!needsMongoOptions()) {
    await initDocStore(cli);
    return { postgres: true, db: null, close: () => Promise.resolve() };
  }
  if (!mongo.uri || !mongo.db) throw new Error('missing --mongo-uri, --db');
  const { db, close } = await connectMongo(mongo.uri, mongo.db);
  return { postgres: false, db, close };
}

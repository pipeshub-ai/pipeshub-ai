import type { Db } from 'mongodb';
import { mongoDb } from '../../../../libs/repositories/mongo';
import { docStore } from '../../../../libs/repositories/store-select';
import { CONTROL_PLANE_MIGRATIONS } from '../../../control-plane';

/**
 * Builds the control plane's Mongo indexes (nothing on PostgreSQL, whose schema the migrator owns). The admin policy routes serve
 * with the harness off too, so this runs on every install; a failed build
 * leaves the store working on its `_id` lookups and is logged.
 */
export async function preparePolicyStore(
  logger: {
    info(message: string, meta?: object): void;
    error(message: string, meta?: object): void;
  },
  db: () => Db | undefined = mongoDb,
): Promise<void> {
  if (docStore() === 'postgres') {
    // The tables and indexes are the harness branch of backend/db (revision harness_0006), built by the migrator.
    logger.info('Control plane store ready: PostgreSQL schema', {
      store: 'postgres',
    });
    return;
  }
  try {
    const database = db();
    if (database === undefined) throw new Error('No database connection');
    const created: string[] = [];
    for (const migration of CONTROL_PLANE_MIGRATIONS) {
      created.push(...(await migration.up(database)).created);
    }
    if (created.length > 0) {
      logger.info('Control plane built indexes', { indexes: created });
    }
  } catch (error) {
    logger.error(
      'Control plane index build failed; policy history reads scan',
      { error: error instanceof Error ? error.message : String(error) },
    );
  }
}

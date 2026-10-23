import { Logger } from '../../../../libs/services/logger.service';
import {
  DelegationEvent,
  DELEGATION_EVENT_RETENTION_DAYS,
} from '../../../oauth_provider/schema/delegation-event.schema';

const MONGO_NAMESPACE_NOT_FOUND = 26;

/** The TTL index this migration owns, by the key it is built on. */
export const DELEGATION_TTL_KEY = { createdAt: 1 } as const;

export type DelegationTtlStatus =
  | 'already_correct'
  | 'updated'
  | 'no_collection'
  | 'no_index'
  | 'failed';

export interface DelegationTtlResult {
  status: DelegationTtlStatus;
  previousSeconds?: number;
  currentSeconds: number;
}

/**
 * Brings the delegation log's TTL in line with the configured retention.
 *
 * Changing the constant alone is not enough, and the way it fails is quiet.
 * Mongoose builds indexes from the schema at boot (`autoIndex: true`), but
 * MongoDB refuses a `createIndex` that differs from an existing index only in
 * `expireAfterSeconds` — it answers `IndexOptionsConflict` and leaves the old
 * value in place. Mongoose discards that error during model init, so the
 * service starts normally and the log quietly keeps the retention it already
 * had. An instance that has ever run with the previous value would never move.
 *
 * `collMod` changes the option in place instead. Documents past the new
 * retention become eligible immediately and the TTL monitor removes them on
 * its next pass, so this does delete rows on a shortening — which is the
 * point, and is why it is written down here rather than left as a surprise.
 *
 * Runs every boot. After the first time it reads one index and stops.
 */
export class DelegationLogRetentionMigration {
  constructor(private readonly logger: Logger) {}

  async run(): Promise<DelegationTtlResult> {
    const wanted = DELEGATION_EVENT_RETENTION_DAYS * 24 * 60 * 60;

    try {
      const indexes = await DelegationEvent.collection.indexes();
      const ttl = indexes.find(
        (i) =>
          i.expireAfterSeconds !== undefined &&
          JSON.stringify(i.key) === JSON.stringify(DELEGATION_TTL_KEY),
      );

      if (!ttl) {
        // Nothing to reconcile: a fresh database gets the right value from
        // the schema when the index is first built.
        return { status: 'no_index', currentSeconds: wanted };
      }

      const current = Number(ttl.expireAfterSeconds);
      if (current === wanted) {
        return { status: 'already_correct', currentSeconds: wanted };
      }

      await DelegationEvent.db.db?.command({
        collMod: DelegationEvent.collection.collectionName,
        index: {
          keyPattern: DELEGATION_TTL_KEY,
          expireAfterSeconds: wanted,
        },
      });

      this.logger.info('Delegation log retention changed', {
        previousDays: Math.round(current / 86400),
        currentDays: DELEGATION_EVENT_RETENTION_DAYS,
        note:
          current > wanted
            ? 'records past the new retention will be removed by the TTL monitor'
            : 'records will now be kept longer',
      });

      return { status: 'updated', previousSeconds: current, currentSeconds: wanted };
    } catch (error) {
      if ((error as { code?: unknown } | null)?.code === MONGO_NAMESPACE_NOT_FOUND) {
        // No delegation has ever been recorded here.
        return { status: 'no_collection', currentSeconds: wanted };
      }

      // Not fatal. The log keeps its old retention, which is wrong but safe,
      // and the service still starts.
      this.logger.error('Could not reconcile the delegation log retention', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return { status: 'failed', currentSeconds: wanted };
    }
  }
}

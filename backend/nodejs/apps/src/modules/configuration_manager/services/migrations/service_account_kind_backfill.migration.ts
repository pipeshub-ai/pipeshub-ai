import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { configPaths } from '../../paths/paths';
import { Users } from '../../../user_management/schema/users.schema';
import {
  EntitiesEventProducer,
  EventType,
  SyncAction,
  UserAddedEvent,
} from '../../../user_management/services/entity_events.service';

const MIGRATION_FLAG_DONE = 'true';

/** How many accounts are republished before the next page is read. */
const BATCH_SIZE = 100;

export interface ServiceAccountKindBackfillResult {
  accountsFound: number;
  accountsRepublished: number;
  errored: number;
  skipped: boolean;
}

/**
 * Tells the permission graph which of its user nodes are service accounts.
 *
 * `kind` is new on the graph's user node. Nodes written before it existed do
 * not carry it, and the services that read the graph cannot work it out for
 * themselves — they have no access to this collection, and deriving it from
 * the reserved address would be a second copy of a rule that lives here.
 *
 * So this republishes `userAdded` for every service account, which is the
 * same event their creation sends and which the consumer already treats as an
 * upsert. The node keeps its key, its edges and its group membership; only
 * `kind` is added. Replaying a creation event is safe for exactly that
 * reason, and is what the restore path already relies on.
 *
 * Deleted accounts are left out. Their nodes are inactive, nothing reads
 * them, and republishing would make them look live again.
 *
 * Idempotent twice over: a KV flag stops it after a clean pass, and the
 * events themselves are upserts, so a partial run simply finishes next boot.
 */
export class ServiceAccountKindBackfillMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
    private readonly eventService: EntitiesEventProducer,
  ) {}

  async run(): Promise<ServiceAccountKindBackfillResult> {
    const empty = { accountsFound: 0, accountsRepublished: 0, errored: 0 };

    try {
      const flag = await this.kvStore.get<string>(
        configPaths.serviceAccountKindBackfill,
      );
      if (flag === MIGRATION_FLAG_DONE) {
        return { ...empty, skipped: true };
      }
    } catch (error) {
      // Not being able to read the flag is a reason to run again, not a
      // reason to stop: the work is an upsert either way.
      this.logger.warn('Could not read the service-account kind flag', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }

    let accountsFound = 0;
    let accountsRepublished = 0;
    let errored = 0;

    try {
      await this.eventService.start();

      const cursor = Users.find({ kind: 'service', isDeleted: false })
        .select('_id orgId fullName email')
        .batchSize(BATCH_SIZE)
        .lean()
        .cursor();

      for await (const account of cursor) {
        accountsFound += 1;
        const payload: UserAddedEvent = {
          orgId: String(account.orgId),
          userId: String(account._id),
          fullName: account.fullName,
          email: account.email,
          syncAction: SyncAction.Immediate,
          kind: 'service',
        };

        try {
          await this.eventService.publishEvent({
            eventType: EventType.NewUserEvent,
            timestamp: Date.now(),
            payload,
          });
          accountsRepublished += 1;
        } catch (error) {
          // One account failing must not cost the rest. The flag stays unset,
          // so the next boot republishes everything, including this one.
          errored += 1;
          this.logger.warn('Could not republish a service account', {
            serviceAccountId: String(account._id),
            error: error instanceof Error ? error.message : 'Unknown error',
          });
        }
      }
    } catch (error) {
      this.logger.error('Service-account kind backfill failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return { accountsFound, accountsRepublished, errored, skipped: false };
    }

    if (errored === 0) {
      try {
        await this.kvStore.set<string>(
          configPaths.serviceAccountKindBackfill,
          MIGRATION_FLAG_DONE,
        );
      } catch (error) {
        // The pass was clean; only the flag did not stick. Next boot repeats
        // work that changes nothing.
        this.logger.warn('Could not record the service-account kind flag', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }

    return { accountsFound, accountsRepublished, errored, skipped: false };
  }
}

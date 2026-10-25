import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../../libs/encryptor/encryptor';
import { configPaths } from '../../paths/paths';
import { Users } from '../../../user_management/schema/users.schema';

/** How many accounts are read from Mongo before the next page. */
const BATCH_SIZE = 100;

/**
 * Upper bound on one staged document. The store holds it as a single value,
 * so the whole list has to fit in one write — 10,000 ids is a few hundred
 * kilobytes once encrypted, comfortably inside etcd's default 1.5 MB limit,
 * and far beyond any plausible number of machine identities. Past it nothing
 * is staged at all, so the backfill stays visibly unfinished instead of
 * silently writing half a list.
 */
const MAX_STAGED_ACCOUNTS = 10_000;

export const SERVICE_ACCOUNT_KIND_STATE_VERSION = 1;

/**
 * The shared document at `configPaths.serviceAccountKindBackfill`. Written
 * here with the ids to stamp, and overwritten by the connector service with
 * `done` once it has stamped them.
 */
export interface ServiceAccountKindState {
  version: number;
  /** Set by the connector service, never here. See the class comment. */
  done?: boolean;
  /** The `userId` of every live service account, awaiting its `kind`. */
  userIds?: string[];
  stagedAt?: number;
}

export interface ServiceAccountKindBackfillResult {
  accountsFound: number;
  accountsStaged: number;
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
 * So this hands the list over rather than doing the write: it stages the
 * `userId` of every live service account in the shared store, and the
 * connector service stamps `kind` on those nodes once its schema is in place
 * (`app/migrations/service_account_kind_migration.py`). That service writes
 * `done` back to the same key, and until it does, this stages again on every
 * boot.
 *
 * Handing it over is what makes the flag honest. An earlier version
 * republished `userAdded` for each account and recorded itself finished once
 * the events were queued. During a rolling upgrade the previous replica still
 * owns the `entity-events` partition, and its handler drops a field it has
 * never heard of: it would consume those events, commit the offsets, and the
 * flag would stop every later boot from trying again. Only the process that
 * writes `kind` knows whether `kind` was written, so only it records that.
 *
 * Replaying `userAdded` was wrong for a second reason. That event means "this
 * user exists", and its handler acts accordingly — it forces `isActive`, remakes
 * the organization edge, adopts external collaborators, ensures a knowledge
 * base and adds the account to the All team. A list of ids asks for none of
 * that; the connector service sets `kind` and nothing else, so an account
 * deleted between the read here and the write there stays deleted.
 *
 * Deleted accounts are left out: their nodes are inactive and nothing reads
 * them.
 */
export class ServiceAccountKindBackfillMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
    /**
     * The connector service's ConfigurationService encrypts every key outside
     * an exact-match exclusion list, and `/migrations/...` keys are not on it.
     * These must match its SECRET_KEY-derived key for it to read what we stage.
     * Same contract as `ChatKbFiltersMigration`, in the other direction.
     */
    private readonly encryptionAlgorithm: string,
    private readonly encryptionSecretKey: string,
  ) {}

  async run(): Promise<ServiceAccountKindBackfillResult> {
    const state = await this.readState();
    if (state?.done === true) {
      return { accountsFound: 0, accountsStaged: 0, skipped: true };
    }

    const userIds: string[] = [];
    const cursor = Users.find({ kind: 'service', isDeleted: false })
      .select('_id')
      .batchSize(BATCH_SIZE)
      .lean()
      .cursor();

    for await (const account of cursor) {
      userIds.push(String(account._id));
      if (userIds.length > MAX_STAGED_ACCOUNTS) {
        this.logger.error(
          'More service accounts than one staged document can hold; nothing staged',
          { limit: MAX_STAGED_ACCOUNTS },
        );
        return {
          accountsFound: userIds.length,
          accountsStaged: 0,
          skipped: false,
        };
      }
    }

    // An empty list is staged too, rather than nothing. The connector service
    // cannot otherwise tell "this deployment has no service accounts" from
    // "the list has not been written yet", and would wait for a list that is
    // never coming.
    //
    // Staging the same list again would only churn the key and its cache
    // invalidation; the connector service has not answered either way yet.
    if (this.sameList(state?.userIds, userIds)) {
      return {
        accountsFound: userIds.length,
        accountsStaged: 0,
        skipped: true,
      };
    }

    await this.writeState({
      version: SERVICE_ACCOUNT_KIND_STATE_VERSION,
      userIds,
      stagedAt: Date.now(),
    });

    return {
      accountsFound: userIds.length,
      accountsStaged: userIds.length,
      skipped: false,
    };
  }

  private sameList(stored: string[] | undefined, found: string[]): boolean {
    if (stored?.length !== found.length) return false;
    const have = new Set(stored);
    return found.every((id) => have.has(id));
  }

  private async readState(): Promise<ServiceAccountKindState | null> {
    try {
      const raw = await this.kvStore.get<string>(
        configPaths.serviceAccountKindBackfill,
      );
      if (raw === null || raw === '') return null;
      const ciphertext = JSON.parse(raw) as string;
      const plaintext = EncryptionService.getInstance(
        this.encryptionAlgorithm,
        this.encryptionSecretKey,
      ).decrypt(ciphertext);
      return JSON.parse(plaintext) as ServiceAccountKindState;
    } catch (error) {
      // Not being able to read the state is a reason to stage again, not a
      // reason to stop: the connector service's write is an idempotent stamp.
      this.logger.warn('Could not read the service-account kind state', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return null;
    }
  }

  private async writeState(state: ServiceAccountKindState): Promise<void> {
    const ciphertext = EncryptionService.getInstance(
      this.encryptionAlgorithm,
      this.encryptionSecretKey,
    ).encrypt(JSON.stringify(state));
    // The connector service's store JSON-decodes whatever it reads before
    // decrypting it, so the ciphertext goes in quoted, as it writes its own.
    await this.kvStore.set<string>(
      configPaths.serviceAccountKindBackfill,
      JSON.stringify(ciphertext),
    );
  }
}

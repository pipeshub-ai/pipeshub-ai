import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { getIdentityPort } from '../../../../libs/repositories/identity';
import { getOAuthAppRepository } from '../../../../libs/repositories/oauth-app';
import { configPaths } from '../../paths/paths';

const MIGRATION_FLAG_DONE = 'true';

/**
 * One-time repair: dynamically registered clients and the first-party device app were stored in
 * the install organisation with its oldest user as their creator. They now name no creator
 * (`NO_APP_CREATOR`), and a dynamic client is stored where `dynamicClientOrg` says. Runs on both
 * document stores, through the OAuth app repository.
 *
 * Only apps created before the bound `markBound` records, before this version's first process
 * accepts a request: the repair runs after start-up, while metadata clients are already stored as
 * the person who consented, and those must keep that person. Once only (a flag) on top of that.
 */
export class InstanceAppOwnerMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
    private readonly dynamicClientOrg: () => Promise<string | null>,
  ) {}

  /**
   * Called before the process accepts requests. The first process of this version records the
   * time; every later one leaves it (compare-and-set from absent), so no app stored by this
   * version on any process is older than the bound.
   */
  static async markBound(kvStore: KeyValueStoreService): Promise<void> {
    await kvStore.compareAndSet<string>(
      configPaths.instanceAppOwnerBound,
      null,
      new Date().toISOString(),
    );
  }

  async run(): Promise<{ skipped: boolean; updated: number }> {
    if (
      (await this.kvStore.get<string>(
        configPaths.instanceAppOwnerMigration,
      )) === MIGRATION_FLAG_DONE
    ) {
      return { skipped: true, updated: 0 };
    }
    const installOrg = await getIdentityPort().oldestOrgId();
    if (installOrg === null) {
      // A new instance has no apps yet; it starts with the fixed code.
      await this.kvStore.set(
        configPaths.instanceAppOwnerMigration,
        MIGRATION_FLAG_DONE,
      );
      return { skipped: false, updated: 0 };
    }
    const bound = await this.kvStore.get<string>(
      configPaths.instanceAppOwnerBound,
    );
    if (bound === null) {
      // Without the bound an app consented to on this version could not be told apart: next start.
      this.logger.warn('Instance OAuth app repair waits for its bound');
      return { skipped: true, updated: 0 };
    }
    const target = await this.dynamicClientOrg();
    if (target === null) {
      throw new Error('No organisation to store dynamic clients in');
    }
    const updated = await getOAuthAppRepository().disownInstanceApps(
      installOrg,
      target,
      new Date(bound),
    );
    // Written only after the apps changed: a failed run is retried at the next start.
    await this.kvStore.set(
      configPaths.instanceAppOwnerMigration,
      MIGRATION_FLAG_DONE,
    );
    this.logger.info('Instance OAuth apps no longer name a borrowed creator', {
      updated,
    });
    return { skipped: false, updated };
  }
}

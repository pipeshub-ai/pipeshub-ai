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
 * Once only (a flag in the key-value store): after it, a metadata document client is stored as
 * the person who consented to it, and a later run would take that away.
 */
export class InstanceAppOwnerMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
    private readonly dynamicClientOrg: () => Promise<string | null>,
  ) {}

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
    const target = await this.dynamicClientOrg();
    if (target === null) {
      throw new Error('No organisation to store dynamic clients in');
    }
    const updated = await getOAuthAppRepository().disownInstanceApps(
      installOrg,
      target,
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

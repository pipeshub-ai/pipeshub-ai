import { Logger } from '../../../../libs/services/logger.service';
import { getIdentityPort } from '../../../../libs/repositories/identity';
import { getOAuthAppRepository } from '../../../../libs/repositories/oauth-app';

/**
 * Repair: dynamically registered clients and the first-party device app that an older release
 * stored in the install organisation name its oldest user as their creator. They now name no
 * creator (`NO_APP_CREATOR`), and a dynamic client is stored where `dynamicClientOrg` says. Runs on
 * both document stores, through the OAuth app repository.
 *
 * Only apps without `ownerFormat`, which every app this code stores has: a metadata client consented
 * to on this release keeps its person, whenever this runs. So it runs at every start and needs no
 * flag; an app an older process stores during a rolling upgrade is repaired at the next start.
 */
export class InstanceAppOwnerMigration {
  constructor(
    private readonly logger: Logger,
    private readonly dynamicClientOrg: () => Promise<string | null>,
  ) {}

  async run(): Promise<{ updated: number }> {
    const installOrg = await getIdentityPort().oldestOrgId();
    // A new instance has no apps yet.
    if (installOrg === null) return { updated: 0 };
    const target = await this.dynamicClientOrg();
    if (target === null) {
      throw new Error('No organisation to store dynamic clients in');
    }
    const updated = await getOAuthAppRepository().disownInstanceApps(
      installOrg,
      target,
    );
    if (updated > 0) {
      this.logger.info(
        'Instance OAuth apps no longer name a borrowed creator',
        {
          updated,
        },
      );
    }
    return { updated };
  }
}

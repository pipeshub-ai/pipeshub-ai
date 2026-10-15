import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { CommunityAdminLimitMigration } from './community_admin_limit.migration';

export type EditionMigration = (
  logger: Logger,
  kvStore: KeyValueStoreService,
) => Promise<void>;

// Migrations that apply to one edition only, exported through config.ts so the
// Enterprise Edition can swap in its own list (the admin limit is Community-only).
export const editionMigrations: EditionMigration[] = [
  async (logger, kvStore) => {
    await new CommunityAdminLimitMigration(logger, kvStore).run();
  },
];

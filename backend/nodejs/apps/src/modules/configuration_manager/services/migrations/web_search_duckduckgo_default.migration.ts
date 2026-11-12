import { randomUUID } from 'crypto';
import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../../libs/encryptor/encryptor';
import { getOrgRepository } from '../../../../libs/repositories/org';
import { configPaths } from '../../paths/paths';
import { loadConfigurationManagerConfig } from '../../config/config';
import { canSearch, WebSearchConfig } from '../../types/web-search.types';

const MIGRATION_FLAG_DONE = 'true';
const DUCKDUCKGO_PROVIDER = 'duckduckgo';
const CAS_MAX_ATTEMPTS = 5;

export interface WebSearchDuckDuckGoDefaultResult {
  seeded: boolean;
  errored: number;
}

/**
 * One-time step for the release that stopped treating DuckDuckGo as the
 * implicit default web search provider. Until then an organisation that never
 * chose a provider searched with DuckDuckGo; without this, its web search
 * would switch off on upgrade. It stores DuckDuckGo as the default, the same
 * way an admin choosing it does, and never touches an organisation that has
 * already picked a provider. A fresh installation (no organisation yet) is not
 * seeded: it starts with web search not set up, as intended.
 */
export class WebSearchDuckDuckGoDefaultMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
  ) {}

  async run(): Promise<WebSearchDuckDuckGoDefaultResult> {
    const result: WebSearchDuckDuckGoDefaultResult = {
      seeded: false,
      errored: 0,
    };
    try {
      const flag = await this.kvStore.get<string>(
        configPaths.webSearchDuckDuckGoDefaultMigration,
      );
      if (flag === MIGRATION_FLAG_DONE) {
        this.logger.info(
          'Web search DuckDuckGo default already handled; skipping',
        );
        return result;
      }
    } catch (error) {
      this.logger.warn(
        'Failed to read the web search default flag; proceeding with idempotent run',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    try {
      if ((await getOrgRepository().count()) > 0) {
        result.seeded = await this.seedDefault();
      }
      await this.kvStore.set<string>(
        configPaths.webSearchDuckDuckGoDefaultMigration,
        MIGRATION_FLAG_DONE,
      );
    } catch (error) {
      // No flag, so the next boot tries again; nothing was overwritten.
      result.errored += 1;
      this.logger.error('Web search DuckDuckGo default migration failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
    return result;
  }

  /** True when DuckDuckGo was stored as the default, false when a provider was already chosen. */
  private async seedDefault(): Promise<boolean> {
    const config = loadConfigurationManagerConfig();
    const encryption = EncryptionService.getInstance(
      config.algorithm,
      config.secretKey,
    );

    for (let attempt = 1; attempt <= CAS_MAX_ATTEMPTS; attempt++) {
      const encrypted = await this.kvStore.get<string>(configPaths.webSearch);
      // A value that cannot be read throws here and is left as it is: seeding
      // over it would replace every stored provider.
      const stored: WebSearchConfig =
        encrypted !== null && encrypted !== ''
          ? (JSON.parse(encryption.decrypt(encrypted)) as WebSearchConfig)
          : { providers: [] };
      const providers = Array.isArray(stored.providers) ? stored.providers : [];

      if (providers.some((p) => p.isDefault && canSearch(p.provider))) {
        return false;
      }

      for (const provider of providers) {
        provider.isDefault = false;
      }
      const existing = providers.find(
        (p) => p.provider === DUCKDUCKGO_PROVIDER,
      );
      if (existing) {
        existing.isDefault = true;
      } else {
        providers.push({
          provider: DUCKDUCKGO_PROVIDER,
          configuration: {},
          providerKey: randomUUID(),
          isDefault: true,
        });
      }

      const saved = await this.kvStore.compareAndSet<string>(
        configPaths.webSearch,
        encrypted ?? null,
        encryption.encrypt(JSON.stringify({ ...stored, providers })),
      );
      if (saved) {
        return true;
      }
      await new Promise((resolve) => setTimeout(resolve, 50 * attempt));
    }
    throw new Error(
      'Web search settings kept changing; will retry on next boot',
    );
  }
}

import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import {
  ConfigurationManagerConfig,
  loadConfigurationManagerConfig,
} from '../config/config';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { configPaths } from '../paths/paths';
import { v4 as uuidv4 } from 'uuid';
import { CrawlingSchedulerService } from '../../crawling_manager/services/crawling_service';
import { AppConfig } from '../../tokens_manager/config/config';
import { ScheduledJobsBackfillMigration } from './migrations/scheduled_jobs_backfill.migration';
import { Org } from '../../user_management/schema/org.schema';

export interface MigrationDependencies {
  scheduler: CrawlingSchedulerService;
  appConfig: AppConfig;
}

@injectable()
export class MigrationService {
  private keyValueStoreService: KeyValueStoreService;
  private logger: Logger;
  private configManagerConfig: ConfigurationManagerConfig;

  constructor(
    @inject('Logger') logger: Logger,
    @inject('KeyValueStoreService') keyValueStoreService: KeyValueStoreService,
  ) {
    this.logger = logger;
    this.keyValueStoreService = keyValueStoreService;
    this.configManagerConfig = loadConfigurationManagerConfig();
  }

  async runMigration(deps: MigrationDependencies): Promise<void> {
    this.logger.info('Running migration...');
    // await this.aiModelsMigration();  NO LONGER NEEDED
    await this.connectorSyncScheduleMigration(deps.scheduler, deps.appConfig);
    this.logger.info('✅ Migration completed');
  }

  async connectorSyncScheduleMigration(
    scheduler: CrawlingSchedulerService,
    appConfig: AppConfig,
  ): Promise<void> {
    this.logger.info('Migrating connector sync schedules');
    try {
      // On a completely fresh installation no organisation has been created yet,
      // so there can be no connectors to schedule. Mark the migration done
      // immediately rather than trying to reach the (possibly still booting)
      // Python connector service — this makes cold-start faster and avoids
      // unnecessary retry noise in the logs.
      const orgCount = await Org.countDocuments();
      if (orgCount === 0) {
        this.logger.info(
          'No organisation found — fresh setup detected. ' +
            'Marking connector sync schedule migration as done without backfill.',
        );
        await this.keyValueStoreService.set(
          configPaths.connectorSyncScheduledJobsMigration,
          'true',
        );
        return;
      }

      const result = await new ScheduledJobsBackfillMigration(
        this.logger,
        this.keyValueStoreService,
        scheduler,
        appConfig,
      ).run();

      if (result.errored > 0) {
        this.logger.warn(
          '⚠️  Connector sync schedule migration finished with errors — will retry on next boot',
          result,
        );
      } else {
        this.logger.info('✅ Connector sync schedules migrated', result);
      }
    } catch (error) {
      this.logger.error('Connector sync schedule migration failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async aiModelsMigration(): Promise<void> {
    this.logger.info('Migrating ai models configurations');
    const encryptedAIConfig = await this.keyValueStoreService.get<string>(
      configPaths.aiModels,
    );
    if (!encryptedAIConfig) {
      this.logger.info('No ai models configurations found');
      return;
    }
    const aiModels = JSON.parse(
      EncryptionService.getInstance(
        this.configManagerConfig.algorithm,
        this.configManagerConfig.secretKey,
      ).decrypt(encryptedAIConfig),
    );
    if (!aiModels) {
      this.logger.info('No ai models configurations found');
      return;
    }

    //  migrate llm configs
    const llmConfigs = aiModels['llm'];
    let isDefault = true;
    for (const llmConfig of llmConfigs) {
      if (!llmConfig.modelKey) {
        const modelKey = uuidv4();
        llmConfig.modelKey = modelKey;
        llmConfig.isDefault = isDefault;
        llmConfig.isMultiModel = false;
        isDefault = false;
      }
    }

    const embeddingConfigs = aiModels['embedding'];
    isDefault = true;
    for (const embeddingConfig of embeddingConfigs) {
      if (!embeddingConfig.modelKey) {
        const modelKey = uuidv4();
        embeddingConfig.modelKey = modelKey;
        embeddingConfig.isDefault = isDefault;
        embeddingConfig.isMultiModel = false;
        isDefault = false;
      }
    }

    aiModels['llm'] = llmConfigs;
    aiModels['embedding'] = embeddingConfigs;

    const encryptedAiModels = EncryptionService.getInstance(
      this.configManagerConfig.algorithm,
      this.configManagerConfig.secretKey,
    ).encrypt(JSON.stringify(aiModels));
    await this.keyValueStoreService.set(configPaths.aiModels, encryptedAiModels);

    this.logger.info('✅ Ai models configurations migrated');
  }
}

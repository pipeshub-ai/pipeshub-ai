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

  async runMigration(): Promise<void> {
    this.logger.info('Running migration...');
    await this.aiModelsMigration();
    this.logger.info('✅ Migration completed');
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

import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { loadConfigurationManagerConfig } from '../config/config';
import { configPaths } from '../paths/paths';
import {
  AI_MODEL_TYPES,
  AIModelType,
} from '../constants/constants';
import {
  AIModelConfiguration,
  AIModelsConfig,
} from '../types/ai-models.types';

const MAX_CAS_RETRIES = 5;

/** Thrown when every compare-and-set attempt lost to a concurrent writer. */
export class AiModelsConfigConflictError extends Error {
  constructor() {
    super(
      'Failed to update AI models configuration due to persistent concurrent modification. Please try again.',
    );
    this.name = 'AiModelsConfigConflictError';
  }
}

/**
 * Thrown by a mutator to abandon the write. The repository does not retry
 * and does not persist the in-memory copy.
 */
export class AiModelsWriteAborted extends Error {
  constructor() {
    super('AI models write aborted');
    this.name = 'AiModelsWriteAborted';
  }
}

export function emptyAiModelsConfig(): AIModelsConfig {
  const config: AIModelsConfig = { modelRoles: {} };
  for (const modelType of AI_MODEL_TYPES) {
    config[modelType] = [];
  }
  return config;
}

/** Fill missing buckets without dropping unknown keys (legacy prompt fields). */
export function ensureAiModelBuckets(config: AIModelsConfig): AIModelsConfig {
  for (const modelType of AI_MODEL_TYPES) {
    if (!Array.isArray(config[modelType])) {
      config[modelType] = [];
    }
  }
  if (!config.modelRoles || typeof config.modelRoles !== 'object' || Array.isArray(config.modelRoles)) {
    config.modelRoles = {};
  }
  return config;
}

export function findModelEntry(
  config: AIModelsConfig,
  modelKey: string,
): { modelType: AIModelType; index: number; model: AIModelConfiguration } | null {
  for (const modelType of AI_MODEL_TYPES) {
    const bucket = config[modelType];
    if (!Array.isArray(bucket)) continue;
    const index = bucket.findIndex(
      (entry) => entry && typeof entry === 'object' && entry.modelKey === modelKey,
    );
    const model = bucket[index];
    if (index >= 0 && model) {
      return { modelType, index, model };
    }
  }
  return null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Encrypted read/write of `/services/aiModels`.
 *
 * Writes use compare-and-set. A mutator that loses the race is reapplied to
 * the latest document, so two adds cannot drop each other.
 */
export class AiModelsConfigRepository {
  constructor(private readonly keyValueStore: KeyValueStoreService) {}

  private cipher(): EncryptionService {
    const configManagerConfig = loadConfigurationManagerConfig();
    return EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    );
  }

  /** Parsed document, or null when nothing has been saved. Buckets are not filled in. */
  async read(): Promise<AIModelsConfig | null> {
    const ciphertext = await this.keyValueStore.get<string>(configPaths.aiModels);
    if (!ciphertext) return null;
    return JSON.parse(this.cipher().decrypt(ciphertext)) as AIModelsConfig;
  }

  /**
   * Replace the whole document. Retries when the stored ciphertext changed
   * between the read and the write; the replacement itself is not merged.
   */
  async replace(next: AIModelsConfig): Promise<void> {
    for (let attempt = 0; attempt < MAX_CAS_RETRIES; attempt++) {
      const ciphertext = await this.keyValueStore.get<string>(configPaths.aiModels);
      const encrypted = this.cipher().encrypt(JSON.stringify(next));
      const wrote = await this.keyValueStore.compareAndSet<string>(
        configPaths.aiModels,
        ciphertext,
        encrypted,
      );
      if (wrote) return;
      await sleep(50 * (attempt + 1));
    }
    throw new AiModelsConfigConflictError();
  }

  /**
   * Apply `mutate` to a bucket-normalized copy of the latest document and
   * persist it. `mutate` may throw {@link AiModelsWriteAborted} to skip the write.
   */
  async mutate(mutate: (config: AIModelsConfig) => void): Promise<AIModelsConfig> {
    for (let attempt = 0; attempt < MAX_CAS_RETRIES; attempt++) {
      const ciphertext = await this.keyValueStore.get<string>(configPaths.aiModels);
      const config = ciphertext
        ? (JSON.parse(this.cipher().decrypt(ciphertext)) as AIModelsConfig)
        : emptyAiModelsConfig();
      ensureAiModelBuckets(config);
      mutate(config);
      const encrypted = this.cipher().encrypt(JSON.stringify(config));
      const wrote = await this.keyValueStore.compareAndSet<string>(
        configPaths.aiModels,
        ciphertext,
        encrypted,
      );
      if (wrote) return config;
      await sleep(50 * (attempt + 1));
    }
    throw new AiModelsConfigConflictError();
  }
}

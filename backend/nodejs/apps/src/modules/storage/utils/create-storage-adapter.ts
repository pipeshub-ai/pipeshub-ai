import { Mutex } from 'async-mutex';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { InternalServerError } from '../../../libs/errors/http.errors';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { storageTypes } from '../../configuration_manager/constants/constants';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { StorageServiceAdapter } from '../adapter/base-storage.adapter';
import {
  AzureBlobStorageConfig,
  LocalStorageConfig,
  S3StorageConfig,
} from '../config/storage.config';
import { storageEtcdPaths } from '../constants/constants';
import { StorageService } from '../storage.service';
import { StorageVendor } from '../types/storage.service.types';
import { getStorageVendor } from './utils';

export interface InitializedStorage {
  adapter: StorageServiceAdapter;
  storageVendor: StorageVendor;
}

const initMutex = new Mutex();
let cached: InitializedStorage | null = null;
let watchStarted = false;

function parseVendorConfig(
  parsed: {
    storageType?: string;
    s3?: string;
    azureBlob?: string;
    local?: string;
  },
  storageVendor: StorageVendor,
): S3StorageConfig | AzureBlobStorageConfig | LocalStorageConfig {
  const cmConfig = loadConfigurationManagerConfig();
  const encryption = EncryptionService.getInstance(cmConfig.algorithm, cmConfig.secretKey);

  if (storageVendor === StorageVendor.S3) {
    if (!parsed.s3) {
      throw new InternalServerError('S3 storage is not configured');
    }
    return JSON.parse(encryption.decrypt(parsed.s3)) as S3StorageConfig;
  }
  if (storageVendor === StorageVendor.AzureBlob) {
    if (!parsed.azureBlob) {
      throw new InternalServerError('Azure Blob storage is not configured');
    }
    return JSON.parse(encryption.decrypt(parsed.azureBlob)) as AzureBlobStorageConfig;
  }
  return JSON.parse(parsed.local || '{}') as LocalStorageConfig;
}

async function ensureWatch(kvStore: KeyValueStoreService): Promise<void> {
  if (watchStarted) {
    return;
  }
  watchStarted = true;
  try {
    await kvStore.watchKey(storageEtcdPaths, () => {
      cached = null;
    });
  } catch {
    watchStarted = true;
  }
}

/**
 * Builds the workspace StorageService once and reuses it for later uploads/reads.
 * Invalidates when `/services/storage` changes.
 */
export async function getInitializedStorageAdapter(
  kvStore: KeyValueStoreService,
  defaultConfig: DefaultStorageConfig,
): Promise<InitializedStorage> {
  await ensureWatch(kvStore);
  if (cached) {
    return cached;
  }

  return initMutex.runExclusive(async () => {
    if (cached) {
      return cached;
    }

    const raw = (await kvStore.get<string>(storageEtcdPaths)) || '{}';
    const parsed = JSON.parse(raw) as {
      storageType?: string;
      s3?: string;
      azureBlob?: string;
      local?: string;
    };
    const storageVendor = getStorageVendor(parsed.storageType || storageTypes.LOCAL);
    const vendorConfig = parseVendorConfig(parsed, storageVendor);
    const storageService = new StorageService(kvStore, vendorConfig, defaultConfig);
    await storageService.initialize();
    const adapter = storageService.getAdapter();
    if (!adapter) {
      throw new InternalServerError('Storage service adapter not found');
    }

    cached = { adapter, storageVendor };
    return cached;
  });
}

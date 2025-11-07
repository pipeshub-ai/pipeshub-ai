import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { loadConfigurationManagerConfig } from '../config/config';
import { configPaths } from '../paths/paths';
import { PLATFORM_FEATURE_FLAGS } from '../constants/constants';
import { KB_UPLOAD_LIMITS } from '../../knowledge_base/constants/kb.constants';
import { Logger } from '../../../libs/services/logger.service';

const logger = Logger.getInstance({
  service: 'PlatformSettingsUtil',
});

export type PlatformSettings = {
  fileUploadMaxSizeBytes: number;
  featureFlags: Record<string, boolean>;
};

/**
 * Shared utility to fetch and parse platform settings from the key-value store.
 * Handles decryption, parsing, and applying defaults.
 */
export async function getPlatformSettingsFromStore(
  keyValueStoreService: KeyValueStoreService,
): Promise<PlatformSettings> {
  const configManagerConfig = loadConfigurationManagerConfig();
  const encrypted = await keyValueStoreService.get<string>(configPaths.platform.settings);

  const defaults: PlatformSettings = {
    fileUploadMaxSizeBytes: KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes,
    featureFlags: {} as Record<string, boolean>,
  };

  type PlatformSettingsStored = Partial<{
    fileUploadMaxSizeBytes: number;
    featureFlags: Record<string, boolean>;
  }>;

  let stored: PlatformSettingsStored | null = null;
  if (encrypted) {
    try {
      const decrypted = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).decrypt(encrypted);
      stored = JSON.parse(decrypted);
    } catch (e) {
      logger.warn('Failed to decrypt/parse platform settings; using defaults', { error: e });
    }
  }

  const base = stored && typeof stored === 'object' ? stored : {};

  const settings: PlatformSettings = {
    fileUploadMaxSizeBytes:
      typeof base.fileUploadMaxSizeBytes === 'number' && base.fileUploadMaxSizeBytes > 0
        ? base.fileUploadMaxSizeBytes
        : defaults.fileUploadMaxSizeBytes,
    featureFlags: (() => {
      const current: Record<string, boolean> =
        base.featureFlags && typeof base.featureFlags === 'object' ? base.featureFlags : {};
      // Ensure all known flags are present with a default
      for (const def of PLATFORM_FEATURE_FLAGS) {
        if (typeof current[def.key] === 'undefined') {
          current[def.key] = !!def.defaultEnabled;
        }
      }
      return current;
    })(),
  };

  return settings;
}


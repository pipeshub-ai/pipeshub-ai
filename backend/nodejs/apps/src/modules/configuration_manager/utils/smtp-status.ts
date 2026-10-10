import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { TtlCache } from '../../../libs/utils/ttl-cache';
import { loadConfigurationManagerConfig } from '../config/config';
import { configPaths } from '../paths/paths';

const SMTP_STATUS_TTL_MS = 60_000;
const SMTP_STATUS_KEY = 'smtp';

const statusCache = new TtlCache<boolean>(SMTP_STATUS_TTL_MS, 1);

/** Loads, decrypts, and parses the stored SMTP config. Returns `null` when none is set. */
export const getParsedSmtpConfig = async (
  keyValueStoreService: KeyValueStoreService,
): Promise<Record<string, unknown> | null> => {
  const configManagerConfig = loadConfigurationManagerConfig();
  const encryptedSmtpConfig = await keyValueStoreService.get<string>(
    configPaths.smtp,
  );
  if (!encryptedSmtpConfig) {
    return null;
  }
  return JSON.parse(
    EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    ).decrypt(encryptedSmtpConfig),
  ) as Record<string, unknown>;
};

/** Same gate `smtpConfigCheck` enforces before invite emails. Cached 60 s; errors propagate uncached. */
export const isSmtpConfigured = async (
  keyValueStoreService: KeyValueStoreService,
): Promise<boolean> => {
  const cached = statusCache.get(SMTP_STATUS_KEY);
  if (cached !== undefined) {
    return cached;
  }
  const smtpConfig = await getParsedSmtpConfig(keyValueStoreService);
  const configured = Boolean(
    smtpConfig?.host && smtpConfig?.port && smtpConfig?.fromEmail,
  );
  statusCache.set(SMTP_STATUS_KEY, configured);
  return configured;
};

/** Call after the stored SMTP config changes so this instance sees it at once. */
export const invalidateSmtpStatusCache = (): void => {
  statusCache.delete(SMTP_STATUS_KEY);
};

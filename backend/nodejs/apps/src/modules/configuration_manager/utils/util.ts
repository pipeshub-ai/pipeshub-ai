import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { loadConfigurationManagerConfig } from '../config/config';
import { configPaths } from '../paths/paths';
import { PLATFORM_FEATURE_FLAGS } from '../constants/constants';
import { KB_UPLOAD_LIMITS } from '../../knowledge_base/constants/kb.constants';
import { Logger } from '../../../libs/services/logger.service';
import {
  applyFlagChanges,
  effectiveFlags,
  FEATURE_FLAGS_VERSION,
  FlagChanges,
  PlatformNotices,
  readFlagState,
} from './flag-overrides';
import { GENERATION_KEY, nextGeneration } from './artifactRollout';

const logger = Logger.getInstance({
  service: 'PlatformSettingsUtil',
});

/** Harness rollout settings that are not booleans, so they cannot be Labs flags. */
export type AgentHarnessPlatformSettings = {
  /** Model families allowed on the durable harness; null allows every family. */
  modelFamilies: string[] | null;
};

export type PlatformSettings = {
  fileUploadMaxSizeBytes: number;
  /** Every listed flag's value: the admin's override, else the release default. */
  featureFlags: Record<string, boolean>;
  /** UX1: only the flags an admin changed; the rest follow their release default. */
  featureFlagOverrides: Record<string, boolean>;
  notices: PlatformNotices;
  agentHarness: AgentHarnessPlatformSettings;
};

const parseAgentHarnessSettings = (
  value: unknown,
): AgentHarnessPlatformSettings => {
  const families =
    value && typeof value === 'object'
      ? (value as { modelFamilies?: unknown }).modelFamilies
      : undefined;
  return {
    modelFamilies:
      Array.isArray(families) && families.every((f) => typeof f === 'string')
        ? families
        : null,
  };
};

/** A stored settings value decrypted; `{}` when none was saved or it cannot be read. */
function decodeStoredPlatformSettings(
  encrypted: string | null,
): Record<string, unknown> {
  if (!encrypted) return {};
  const configManagerConfig = loadConfigurationManagerConfig();
  try {
    const decrypted = EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    ).decrypt(encrypted);
    const stored: unknown = JSON.parse(decrypted);
    return stored !== null &&
      typeof stored === 'object' &&
      !Array.isArray(stored)
      ? (stored as Record<string, unknown>)
      : {};
  } catch (e) {
    logger.warn('Failed to decrypt/parse platform settings; using defaults', {
      error: e,
    });
    return {};
  }
}

/** The settings document as stored (decrypted); `{}` when none was saved or it cannot be read. */
export async function readStoredPlatformSettings(
  keyValueStoreService: Pick<KeyValueStoreService, 'get'>,
): Promise<Record<string, unknown>> {
  return decodeStoredPlatformSettings(
    await keyValueStoreService.get<string>(configPaths.platform.settings),
  );
}

/** The settings as every reader sees them: each flag's effective value; reading never writes (UX1). */
export function platformSettingsOf(
  stored: Record<string, unknown>,
): PlatformSettings {
  const state = readFlagState(stored, PLATFORM_FEATURE_FLAGS);
  const size = stored['fileUploadMaxSizeBytes'];
  return {
    fileUploadMaxSizeBytes:
      typeof size === 'number' && size > 0
        ? size
        : KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes,
    featureFlags: effectiveFlags(state.overrides, PLATFORM_FEATURE_FLAGS),
    featureFlagOverrides: state.overrides,
    notices: state.notices,
    agentHarness: parseAgentHarnessSettings(stored['agentHarness']),
  };
}

/**
 * Shared utility to fetch and parse platform settings from the key-value store.
 * Handles decryption, parsing, and applying defaults.
 */
export async function getPlatformSettingsFromStore(
  keyValueStoreService: Pick<KeyValueStoreService, 'get'>,
): Promise<PlatformSettings> {
  return platformSettingsOf(
    await readStoredPlatformSettings(keyValueStoreService),
  );
}

/** The AI models config as stored (decrypted), or null when none has been saved. */
export async function readStoredAiModelsConfig(
  keyValueStoreService: Pick<KeyValueStoreService, 'get'>,
): Promise<Record<string, unknown> | null> {
  const configManagerConfig = loadConfigurationManagerConfig();
  const encryptedAIConfig = await keyValueStoreService.get<string>(
    configPaths.aiModels,
  );
  if (!encryptedAIConfig) {
    return null;
  }
  return JSON.parse(
    EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    ).decrypt(encryptedAIConfig),
  );
}

/** What the admin saw before a save (UX2): the value of each setting it sends, as its page showed it. */
export interface PlatformSettingsExpected {
  featureFlags?: Record<string, boolean>;
  fileUploadMaxSizeBytes?: number;
}

export interface PlatformSettingsSave extends FlagChanges {
  fileUploadMaxSizeBytes?: number;
  agentHarness?: AgentHarnessPlatformSettings;
  expected?: PlatformSettingsExpected;
}

/** The reason code of a save that would overwrite someone else's newer choice (UX2 LC-2). */
export const LABS_CHANGED = 'labs_changed';
/** Compare-and-set rounds lost to other writers before the save gives up with `labs_changed`. */
export const MAX_SETTINGS_SAVE_ATTEMPTS = 5;

/**
 * The settings this save would decide on a value someone else changed since
 * the admin read it: what was expected is no longer stored, and what is
 * stored is not what the admin sends. A setting already at the sent value
 * has nothing to decide; a setting sent without an expected value is the
 * request of an older page and is applied as before.
 */
export function staleChoices(
  current: PlatformSettings,
  save: PlatformSettingsSave,
): string[] {
  const stale: string[] = [];
  const expected = save.expected ?? {};
  for (const [key, value] of Object.entries(save.featureFlags ?? {})) {
    const seen = expected.featureFlags?.[key];
    const now = current.featureFlags[key];
    if (seen !== undefined && now !== seen && now !== value) stale.push(key);
  }
  const size = save.fileUploadMaxSizeBytes;
  const seenSize = expected.fileUploadMaxSizeBytes;
  if (
    size !== undefined &&
    seenSize !== undefined &&
    current.fileUploadMaxSizeBytes !== seenSize &&
    current.fileUploadMaxSizeBytes !== size
  ) {
    stale.push('fileUploadMaxSizeBytes');
  }
  return stale.sort();
}

export class PlatformSettingsChanged extends Error {
  constructor(readonly stale: string[]) {
    super(LABS_CHANGED);
    this.name = 'PlatformSettingsChanged';
  }
}

/**
 * One admin save of the platform settings, as a compare-and-set on the
 * stored document (UX2, UX1 OI-4): each round reads the document, merges
 * this save into it and writes only if nobody wrote in between, so two
 * admins saving different settings both land. A save that would decide a
 * setting someone else changed since the admin read it is refused with
 * `PlatformSettingsChanged` before anything is written; so is one that loses
 * every round. Settings the save does not send keep what is stored.
 */
export async function savePlatformSettings(
  keyValueStoreService: KeyValueStoreService,
  save: PlatformSettingsSave,
  now: () => Date = () => new Date(),
): Promise<PlatformSettings> {
  const key = configPaths.platform.settings;
  for (let attempt = 1; attempt <= MAX_SETTINGS_SAVE_ATTEMPTS; attempt += 1) {
    const raw = await keyValueStoreService.get<string>(key);
    const stored = decodeStoredPlatformSettings(raw);
    const current = platformSettingsOf(stored);
    const stale = staleChoices(current, save);
    if (stale.length > 0) throw new PlatformSettingsChanged(stale);
    const flags = applyFlagChanges(
      readFlagState(stored, PLATFORM_FEATURE_FLAGS),
      { featureFlags: save.featureFlags, notices: save.notices },
      PLATFORM_FEATURE_FLAGS,
    );
    const next: Record<string, unknown> = {
      ...stored,
      fileUploadMaxSizeBytes:
        save.fileUploadMaxSizeBytes ?? current.fileUploadMaxSizeBytes,
      featureFlagsVersion: FEATURE_FLAGS_VERSION,
      featureFlags: flags.overrides,
      notices: flags.notices,
      agentHarness: save.agentHarness ?? current.agentHarness,
      updatedAt: now().toISOString(),
    };
    // Server-side only, never taken from the request: the artifact-read generation follows the effective flags.
    next[GENERATION_KEY] = nextGeneration(
      stored[GENERATION_KEY],
      current.featureFlags,
      platformSettingsOf(next).featureFlags,
    );
    const configManagerConfig = loadConfigurationManagerConfig();
    const encrypted = EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    ).encrypt(JSON.stringify(next));
    if (await keyValueStoreService.compareAndSet<string>(key, raw, encrypted)) {
      return platformSettingsOf(next);
    }
    logger.info('platform_settings.save_raced', {
      attempt,
      maxAttempts: MAX_SETTINGS_SAVE_ATTEMPTS,
    });
  }
  throw new PlatformSettingsChanged([]);
}

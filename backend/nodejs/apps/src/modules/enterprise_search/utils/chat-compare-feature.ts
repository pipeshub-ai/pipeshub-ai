import { NextFunction, Request, Response } from 'express';
import { ForbiddenError } from '../../../libs/errors/http.errors';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { docStore } from '../../../libs/repositories/store-select';
import { Logger } from '../../../libs/services/logger.service';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { getPlatformSettingsFromStore } from '../../configuration_manager/utils/util';

const logger = Logger.getInstance({ service: 'ChatCompareFeature' });

export const CHAT_COMPARE_DISABLED_MESSAGE =
  'Compare models is turned off for this workspace. An admin can turn it on in Workspace > Labs.';

/** Max models answering one question: the conversation itself + (MAX_COMPARE_MODELS - 1) hidden branches. */
export const MAX_COMPARE_MODELS = 2;

/** Short enough that a Labs toggle applies almost at once. */
const CACHE_TTL_MS = 5_000;

let cached: { value: boolean; at: number } | null = null;

/**
 * Whether the ENABLE_CHAT_COMPARE Labs flag is on. Unlike Projects this fails
 * closed: an unreadable settings store leaves the (beta) feature off.
 */
export const ChatCompareFeature = {
  settingsStore(): KeyValueStoreService {
    return KeyValueStoreService.getInstance(loadConfigurationManagerConfig());
  },

  async isEnabled(): Promise<boolean> {
    // The PostgreSQL document store has no columns for branches yet.
    if (docStore() === 'postgres') return false;
    if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.value;
    try {
      const store = ChatCompareFeature.settingsStore();
      if (!store.isConnected()) return false;
      const { featureFlags } = await getPlatformSettingsFromStore(store);
      const value = featureFlags.ENABLE_CHAT_COMPARE === true;
      cached = { value, at: Date.now() };
      return value;
    } catch (error) {
      logger.warn('Could not read the Compare models flag; leaving it off', {
        error: error instanceof Error ? error.message : String(error),
      });
      return false;
    }
  },

  /** Throws ForbiddenError while the flag is off. */
  async assertEnabled(): Promise<void> {
    if (!(await ChatCompareFeature.isEnabled())) {
      throw new ForbiddenError(CHAT_COMPARE_DISABLED_MESSAGE);
    }
  },

  resetCache(): void {
    cached = null;
  },
};

export async function requireChatCompareEnabled(
  _req: Request,
  _res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    await ChatCompareFeature.assertEnabled();
    next();
  } catch (error) {
    next(error);
  }
}

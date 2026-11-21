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

/** Enough for every workspace one process serves; past it the oldest answer goes first. */
const CACHE_MAX_KEYS = 10_000;

const cached = new Map<string, { value: boolean; at: number }>();

function remember(cacheKey: string, value: boolean): void {
  // Deleting first moves the key to the newest end of the Map's insertion order.
  cached.delete(cacheKey);
  if (cached.size >= CACHE_MAX_KEYS) {
    const now = Date.now();
    for (const [key, entry] of cached) {
      if (now - entry.at >= CACHE_TTL_MS) cached.delete(key);
    }
    if (cached.size >= CACHE_MAX_KEYS) {
      const oldest = cached.keys().next().value;
      if (oldest !== undefined) cached.delete(oldest);
    }
  }
  cached.set(cacheKey, { value, at: Date.now() });
}

/**
 * The cache slot this request's answer goes in, and the store to read it
 * from on a miss; null while no store can be read yet. The default reads the
 * one settings document. A build that keeps settings per workspace supplies
 * its own at boot, so each workspace's Labs switch decides for that workspace;
 * the store is resolved only on a miss because finding it can cost reads.
 */
export type ChatCompareSettingsSource = () => {
  cacheKey: string;
  store: () => Promise<Pick<KeyValueStoreService, 'get'>>;
} | null;

const readSettingsDocument: ChatCompareSettingsSource = () => {
  const store = ChatCompareFeature.settingsStore();
  return store.isConnected()
    ? { cacheKey: '', store: () => Promise.resolve(store) }
    : null;
};

let resolveSource: ChatCompareSettingsSource = readSettingsDocument;

/** Replaces where the Compare models flag is read from; no argument restores the default. */
export function setChatCompareSettingsSource(
  fn: ChatCompareSettingsSource = readSettingsDocument,
): void {
  resolveSource = fn;
  cached.clear();
}

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
    try {
      const source = resolveSource();
      if (!source) return false;
      const hit = cached.get(source.cacheKey);
      if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.value;
      const { featureFlags } = await getPlatformSettingsFromStore(
        await source.store(),
      );
      const value = featureFlags.ENABLE_CHAT_COMPARE === true;
      remember(source.cacheKey, value);
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
    cached.clear();
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

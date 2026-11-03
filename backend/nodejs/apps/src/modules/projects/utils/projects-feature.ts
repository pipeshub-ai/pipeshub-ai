import { NextFunction, Request, Response } from 'express';
import { ForbiddenError } from '../../../libs/errors/http.errors';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { getPlatformSettingsFromStore } from '../../configuration_manager/utils/util';

const logger = Logger.getInstance({ service: 'ProjectsFeature' });

export const PROJECTS_DISABLED_MESSAGE =
  'Projects are turned off for this workspace. An admin can turn them on in Workspace > Labs.';

/** Long enough to spare a settings read per request, short enough that a Labs toggle applies almost at once. */
const CACHE_TTL_MS = 5_000;

const cached = new Map<string, { value: boolean; at: number }>();

/**
 * The cache slot this request's answer goes in, and the store to read it
 * from on a miss; null while no store can be read yet. The default reads the
 * one settings document. A build that keeps settings per workspace supplies
 * its own at boot, so each workspace's Labs switch decides for that workspace;
 * the store is resolved only on a miss because finding it can cost reads.
 */
export type ProjectsSettingsSource = () => {
  cacheKey: string;
  store: () => Promise<KeyValueStoreService>;
} | null;

const readSettingsDocument: ProjectsSettingsSource = () => {
  const store = ProjectsFeature.settingsStore();
  return store.isConnected()
    ? { cacheKey: '', store: () => Promise.resolve(store) }
    : null;
};

let resolveSource: ProjectsSettingsSource = readSettingsDocument;

/** Replaces where the Projects flag is read from; no argument restores the default. */
export function setProjectsSettingsSource(
  fn: ProjectsSettingsSource = readSettingsDocument,
): void {
  resolveSource = fn;
  cached.clear();
}

/**
 * Whether the ENABLE_PROJECTS Labs flag is on. Turning it off used to hide
 * only the UI while the API kept creating projects and running project chats.
 *
 * Reads that cannot happen (store not connected yet) or fail leave Projects
 * on: the flag hides a feature, it is not an access boundary, and failing
 * closed would break every project for a settings-store hiccup.
 */
export const ProjectsFeature = {
  settingsStore(): KeyValueStoreService {
    return KeyValueStoreService.getInstance(loadConfigurationManagerConfig());
  },

  async isEnabled(): Promise<boolean> {
    try {
      const source = resolveSource();
      if (!source) {
        return true;
      }
      const hit = cached.get(source.cacheKey);
      if (hit && Date.now() - hit.at < CACHE_TTL_MS) {
        return hit.value;
      }
      const { featureFlags } = await getPlatformSettingsFromStore(
        await source.store(),
      );
      const value = featureFlags.ENABLE_PROJECTS === true;
      cached.set(source.cacheKey, { value, at: Date.now() });
      return value;
    } catch (error) {
      logger.warn('Could not read the Projects flag; leaving Projects on', {
        error: error instanceof Error ? error.message : String(error),
      });
      return true;
    }
  },

  /** Throws ForbiddenError while the flag is off. */
  async assertEnabled(): Promise<void> {
    if (!(await ProjectsFeature.isEnabled())) {
      throw new ForbiddenError(PROJECTS_DISABLED_MESSAGE);
    }
  },

  /** Drops the cached flag value, so a test sees the flag it just set. */
  resetCache(): void {
    cached.clear();
  },
};

export async function requireProjectsEnabled(
  _req: Request,
  _res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    await ProjectsFeature.assertEnabled();
    next();
  } catch (error) {
    next(error);
  }
}

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

let cached: { value: boolean; at: number } | null = null;

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
    if (cached && Date.now() - cached.at < CACHE_TTL_MS) {
      return cached.value;
    }
    try {
      const store = ProjectsFeature.settingsStore();
      if (!store.isConnected()) {
        return true;
      }
      const { featureFlags } = await getPlatformSettingsFromStore(store);
      const value = featureFlags.ENABLE_PROJECTS === true;
      cached = { value, at: Date.now() };
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
    cached = null;
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

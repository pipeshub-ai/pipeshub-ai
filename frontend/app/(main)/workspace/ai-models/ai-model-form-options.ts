import type { TFunction } from 'i18next';
import type { DiscoveredModel } from './types';

/** Model settings an edition adds to the add/edit model form. Editions plug in via config.ts. */
export interface AiModelFormOptions {
  /**
   * Configuration keys each picked model sets for itself when several are added at once.
   * They must be public settings (`publicConfigKeys`, and the backend's
   * `EXTRA_AI_PUBLIC_CONFIG_KEYS`): batch add ignores any other per-model key.
   */
  perModelConfigKeys: readonly string[];
  /** Configuration keys that reads return as stored, because they are settings, not secrets. */
  publicConfigKeys: readonly string[];
  /** Extra details shown for a model in the provider's model list. */
  discoveredModelBadges(t: TFunction, model: DiscoveredModel): string[];
}

export const aiModelFormOptions: AiModelFormOptions = {
  perModelConfigKeys: [],
  publicConfigKeys: [],
  discoveredModelBadges: () => [],
};

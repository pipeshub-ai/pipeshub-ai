import { PLATFORM_FEATURE_FLAGS } from '../../../configuration_manager/constants/constants';
import { EDITION_PLATFORM_FEATURE_FLAGS } from '../../../../edition';
import {
  platformSettingsOf,
  readStoredAiModelsConfig,
  readStoredPlatformSettings,
} from '../../../configuration_manager/utils/util';
import {
  configuredModels,
  type ConfiguredModels,
} from '../../domain/configured-llms';
import {
  labsStages,
  type LabsRegistry,
  type RolloutStages,
} from '../../domain/labs-stages';
import type {
  ConfiguredLlmSource,
  LabsStagesSource,
} from '../../ports/pin-sources';
import type { OrgSettings } from '../../ports/org-settings';

/** The org's `aiModels`: its LLMs, and every kind's model keys for the family rule. */
export class AiModelsConfiguredLlms implements ConfiguredLlmSource {
  constructor(private readonly settings: OrgSettings) {}

  async read(orgId: string): Promise<ConfiguredModels> {
    return configuredModels(
      await readStoredAiModelsConfig(this.settings.of(orgId)),
    );
  }
}

/** The Labs registry as the configuration manager defines and reads it. */
export const PLATFORM_LABS_REGISTRY: LabsRegistry = {
  releaseDefault: (key) =>
    [...PLATFORM_FEATURE_FLAGS, ...EDITION_PLATFORM_FEATURE_FLAGS].find(
      (flag) => flag.key === key,
    )?.defaultEnabled === true,
  effective: (stored) => platformSettingsOf(stored).featureFlags,
};

/** The org's platform settings document: its Labs switches. */
export class PlatformLabsStages implements LabsStagesSource {
  constructor(private readonly settings: OrgSettings) {}

  async read(orgId: string): Promise<RolloutStages> {
    return labsStages(
      await readStoredPlatformSettings(this.settings.of(orgId)),
      PLATFORM_LABS_REGISTRY,
    );
  }
}

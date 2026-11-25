import type { ConfiguredModels } from '../domain/configured-llms';
import type { RolloutStages } from '../domain/labs-stages';

/** The org's configured models, read once per snapshot load. Throws when they cannot be read. */
export interface ConfiguredLlmSource {
  read(orgId: string): Promise<ConfiguredModels>;
}

/** The org's Labs stages, read once per snapshot load. Throws when the settings store cannot be reached. */
export interface LabsStagesSource {
  read(orgId: string): Promise<RolloutStages>;
}

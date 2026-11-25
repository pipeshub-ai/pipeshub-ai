import { readStoredAiModelsConfig } from '../../../configuration_manager/utils/util';
import { familyOf } from '../../../control-plane';
import { configuredFamilies } from '../../domain/configured-llms';
import { RunInputSnapshot } from '../../domain/execution';
import { ModelFamilyResolver } from '../../ports/harness-rollout';
import type { OrgSettings } from '../../ports/org-settings';

/**
 * The family is the provider of the org's configured model, looked up by the
 * server-side model key; a browser-supplied provider is never trusted. With
 * no key the query service uses the default LLM, so that is what is resolved
 * (`configuredFamilies`, the rule a pin's snapshot is read by too).
 */
export class AiModelsFamilyResolver implements ModelFamilyResolver {
  constructor(private readonly settings: OrgSettings) {}

  async resolve(
    orgId: string,
    model: RunInputSnapshot['model'],
  ): Promise<string | null> {
    const config = await readStoredAiModelsConfig(this.settings.of(orgId));
    return config === null
      ? null
      : familyOf(configuredFamilies(config), model.modelKey ?? null);
  }
}

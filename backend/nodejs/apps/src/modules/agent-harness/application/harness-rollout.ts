import {
  decideAdmissionRollout,
  decideChatModeRollout,
  decideOrgRollout,
  orgHarnessOnFrom,
  HarnessRolloutDecision,
  HarnessRolloutSettings,
} from '../domain/rollout';
import {
  HarnessOrgRollout,
  HarnessRolloutPolicy,
  HarnessRolloutRequest,
  HarnessRolloutSettingsSource,
  ModelFamilyResolver,
} from '../ports/harness-rollout';

export class HarnessRollout implements HarnessRolloutPolicy {
  constructor(
    private readonly source: HarnessRolloutSettingsSource,
    private readonly families: ModelFamilyResolver,
  ) {}

  async decide(
    request: HarnessRolloutRequest,
  ): Promise<HarnessRolloutDecision> {
    const settings = await this.source.read(request.orgId);
    const org = decideChatModeRollout(
      decideOrgRollout(settings),
      request.model.chatMode,
      request.assistant === true,
      {
        ...(settings.assistantModes !== undefined
          ? { assistantModes: settings.assistantModes }
          : {}),
        ...(request.durableRequested === true
          ? { durableRequested: true }
          : {}),
      },
    );
    // Resolving the family reads model config; skip it when nothing depends on it.
    if (!org.useHarness || settings.modelFamilies === null) return org;
    const family = await this.families.resolve(request.orgId, request.model);
    return decideAdmissionRollout(settings, family);
  }

  settings(orgId: string): Promise<HarnessRolloutSettings> {
    return this.source.read(orgId);
  }

  async decideForOrg(orgId: string): Promise<HarnessOrgRollout> {
    const settings = await this.source.read(orgId);
    const harnessOn = orgHarnessOnFrom(settings);
    return {
      ...decideOrgRollout(settings),
      harnessOn,
      resumeInFlightRuns: harnessOn && settings.infrastructureReady,
    };
  }
}

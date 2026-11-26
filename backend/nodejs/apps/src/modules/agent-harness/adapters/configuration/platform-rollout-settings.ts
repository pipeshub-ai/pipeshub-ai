import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { getPlatformSettingsFromStore } from '../../../configuration_manager/utils/util';
import { envFlag } from '../../../../libs/utils/env-flags';
import {
  AgentHarnessEnvironment,
  isAgentHarnessInfrastructureReady,
} from '../../config';
import {
  assistantModesWith,
  HarnessRolloutSettings,
} from '../../domain/rollout';
import { HarnessRolloutSettingsSource } from '../../ports/harness-rollout';
import { CollaboratorTurnsSetting } from '../../ports/collaborator-turns';
import { ScheduledRunsSetting } from '../../ports/schedules';
import { KnowledgePublicationsSetting } from '../../ports/knowledge-publications';
import { ActivityPanelSetting } from '../../ports/activity-panel';
import type { OrgStopReader } from '../../ports/operator-stops';
import type { OrgSettings } from '../../ports/org-settings';
import { singleStoreOrgSettings } from './single-store-org-settings';

export const AGENT_HARNESS_LABS_FLAG = 'ENABLE_AGENT_HARNESS';
export const SCHEDULED_RUNS_LABS_FLAG = 'ENABLE_SCHEDULED_RUNS';
export const KNOWLEDGE_PUBLICATIONS_LABS_FLAG = 'ENABLE_KNOWLEDGE_PUBLICATIONS';

/**
 * The org's platform settings (`orgSettings`, for the org asked about), read
 * on every call so the Labs "Agent harness" switch applies to the next
 * admission without a restart. The kill switch is on while the operator's env
 * setting `AGENT_HARNESS_KILL_SWITCH` is, for the whole instance (CTL-5), or
 * `orgStopped` says the org is stopped (ADR-OPS-01; absent: never); the
 * default chat's Agent mode comes with the harness.
 */
export class PlatformHarnessRolloutSettings implements HarnessRolloutSettingsSource {
  constructor(
    store: KeyValueStoreService,
    private readonly environment: AgentHarnessEnvironment = process.env,
    private readonly orgStopped: OrgStopReader | null = null,
    private readonly orgSettings: OrgSettings = singleStoreOrgSettings(store),
  ) {}

  /**
   * An org or instance stop (ADR-OPS-01). Stops that cannot be read count as
   * on: the turn then takes today's chat, which still answers, rather than a
   * harness an operator may have stopped.
   */
  private async operatorStopped(orgId: string): Promise<boolean> {
    if (this.orgStopped === null) return false;
    try {
      return await this.orgStopped(orgId);
    } catch {
      return true;
    }
  }

  async read(orgId: string): Promise<HarnessRolloutSettings> {
    const [{ featureFlags, agentHarness }, operatorStopped] = await Promise.all(
      [
        getPlatformSettingsFromStore(this.orgSettings.of(orgId)),
        this.operatorStopped(orgId),
      ],
    );
    return {
      infrastructureReady: isAgentHarnessInfrastructureReady(this.environment),
      killSwitch:
        envFlag('AGENT_HARNESS_KILL_SWITCH', this.environment) ||
        operatorStopped,
      orgEnabled: featureFlags[AGENT_HARNESS_LABS_FLAG] === true,
      modelFamilies: agentHarness?.modelFamilies ?? null,
      assistantModes: assistantModesWith(true),
    };
  }
}

/**
 * Collaborators' turns (SS2) come with the agent harness (CTL-5): sharing a
 * conversation for editing is the person's own control.
 * shortcut: a constant behind the port that admission still asks, remove the port when CTL-5's flags have settled.
 */
export class PlatformCollaboratorTurns implements CollaboratorTurnsSetting {
  // eslint-disable-next-line @typescript-eslint/no-useless-constructor -- keeps the store-taking signature its siblings share, so wiring stays uniform
  constructor(_store?: KeyValueStoreService) {}

  enabled(_orgId: string): Promise<boolean> {
    return Promise.resolve(true);
  }
}

/**
 * Labs `ENABLE_SCHEDULED_RUNS` (P8a): the org's switch for scheduled and
 * triggered agent runs. Read at each request and each firing; anything but
 * `true` is off.
 */
export class PlatformScheduledRuns implements ScheduledRunsSetting {
  constructor(private readonly settings: OrgSettings) {}

  async enabled(orgId: string): Promise<boolean> {
    const { featureFlags } = await getPlatformSettingsFromStore(
      this.settings.of(orgId),
    );
    return featureFlags[SCHEDULED_RUNS_LABS_FLAG] === true;
  }
}

/**
 * Labs `ENABLE_KNOWLEDGE_PUBLICATIONS` (P2.13): the org's switch for knowledge
 * publications. Read at each request; anything but `true` is off. The query
 * service reads the same flag again before it acts.
 */
export class PlatformKnowledgePublications implements KnowledgePublicationsSetting {
  constructor(private readonly settings: OrgSettings) {}

  async enabled(orgId: string): Promise<boolean> {
    const { featureFlags } = await getPlatformSettingsFromStore(
      this.settings.of(orgId),
    );
    return featureFlags[KNOWLEDGE_PUBLICATIONS_LABS_FLAG] === true;
  }
}

/**
 * The activity drawer (UX5) comes with the agent harness (CTL-5): it only shows
 * the run's owner what their own run journaled, and opens only on their click.
 * shortcut: a constant behind the port the route still asks, remove the port when CTL-5's flags have settled.
 */
export class PlatformActivityPanel implements ActivityPanelSetting {
  // eslint-disable-next-line @typescript-eslint/no-useless-constructor -- keeps the store-taking signature its siblings share, so wiring stays uniform
  constructor(_store?: KeyValueStoreService) {}

  enabled(_orgId: string): Promise<boolean> {
    return Promise.resolve(true);
  }
}

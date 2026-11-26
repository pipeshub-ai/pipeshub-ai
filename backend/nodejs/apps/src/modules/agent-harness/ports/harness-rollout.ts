import { RunInputSnapshot } from '../domain/execution';
import {
  HarnessRolloutDecision,
  HarnessRolloutSettings,
} from '../domain/rollout';

export interface HarnessRolloutRequest {
  orgId: string;
  model: RunInputSnapshot['model'];
  /** The built-in assistant (no agent): its chat modes, not an agent's. */
  assistant?: boolean;
  /** The turn asked for the durable harness (ASSIST1); only the assistant's Agent mode reads it. */
  durableRequested?: boolean;
}

export interface HarnessOrgRollout extends HarnessRolloutDecision {
  /** The harness is on for the org (ADR-OPS-08, `orgHarnessOnFrom`): what every surface and behaviour keys on. */
  harnessOn: boolean;
  /**
   * Durable runs already admitted can still be read and reattached: the harness on for the org and its
   * infrastructure ready, kill switch or not.
   */
  resumeInFlightRuns: boolean;
}

/**
 * Decides whether a NEW admission uses the durable harness. Claim, renew,
 * commit and run control never consult it: an admitted run keeps its pinned
 * `harnessMajor` whatever the rollout says later.
 */
export interface HarnessRolloutPolicy {
  decide(request: HarnessRolloutRequest): Promise<HarnessRolloutDecision>;
  decideForOrg(orgId: string): Promise<HarnessOrgRollout>;
  /** The org's switches as read for these decisions; the control plane folds them as the org's `rollout` section. */
  settings?(orgId: string): Promise<HarnessRolloutSettings>;
}

export interface HarnessRolloutSettingsSource {
  read(orgId: string): Promise<HarnessRolloutSettings>;
}

export interface ModelFamilyResolver {
  /** Null when the model cannot be matched to a configured model. */
  resolve(
    orgId: string,
    model: RunInputSnapshot['model'],
  ): Promise<string | null>;
}

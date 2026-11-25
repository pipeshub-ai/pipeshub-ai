import type { HarnessRun } from '../domain/execution';
import type {
  Decider,
  DecisionRefusal,
  PersonDecision,
} from '../domain/interactions';
import type { CommitFence } from './commit-fence';

/** Whether a person is an approver or an admin of their org; read once per request. */
export interface OrgStanding {
  isApprover: boolean;
  isOrgAdmin: boolean;
}

/** A refusal a rule gives, with what the person reads. */
export interface RuleRefusal {
  code: DecisionRefusal;
  message: string;
}

/** An ask that waits for more approvals than it holds; the call is not approved yet. */
export interface ApprovalsCounted {
  counted: number;
  required: number;
}

/**
 * The rules some of a run's asks carry beyond the usual one, as they apply to
 * one viewer. Every method answers for the asks a rule decides; for any other
 * ask it answers `undefined` (or nothing), and the usual rule decides.
 */
export interface ViewerApprovalRules {
  /** A rule lets the viewer decide this run though they are not in its session. */
  readonly reaches: boolean;
  /** Null: allowed; undefined: the usual rule decides. */
  refusal(
    decider: Decider,
    decision: PersonDecision,
    interactionId: string,
  ): RuleRefusal | null | undefined;
  /** Whether the viewer may approve the ask now, as the approvals list shows it; undefined: the usual rule. */
  approves(decider: Decider, interactionId: string): boolean | undefined;
  /**
   * What the decision holds in its transaction for the standing these asks' rules
   * stand on; `refuse` makes the error losing it raises.
   */
  fence(
    decider: Decider,
    decision: PersonDecision,
    interactionIds: readonly string[],
    refuse: (refusal: RuleRefusal) => () => Error,
  ): CommitFence | undefined;
  /**
   * Counts the decision toward the asks a rule says need several approvals. Answers
   * those that still wait, with their counts; the caller resolves the rest.
   * `fence(ids)` is the decision's own fence for those asks.
   */
  count(
    decider: Decider,
    decision: PersonDecision,
    by: string,
    interactionIds: readonly string[],
    fence: (interactionIds: readonly string[]) => CommitFence | undefined,
  ): Promise<ReadonlyMap<string, ApprovalsCounted>>;
  /** Fields these rules add to the viewer's decision authority. */
  authority(decider: Decider): Promise<Record<string, unknown>>;
}

/** The rules a run's asks carry, per viewer. */
export interface RunApprovalRules {
  forViewer(
    viewer: { orgId: string; actorId: string },
    standing: OrgStanding,
  ): Promise<ViewerApprovalRules>;
}

/**
 * Rules beyond the usual one that an ask may carry (an extension point, bound
 * to `AGENT_HARNESS_APPROVAL_RULES`). The usual rule: the run's actor (and an
 * approver a bound directory names) approves; the session owner and org admins may deny.
 */
export interface ApprovalRules {
  /**
   * The rules the run's asks carry, or null when none does. `explained`: each
   * ask's reason as the caller already read it, by interaction id; absent,
   * they are read from the run.
   */
  forRun(
    run: HarnessRun,
    explained?: ReadonlyMap<string, unknown>,
  ): Promise<RunApprovalRules | null>;
}

/** The default: no ask carries a rule, so the usual rule decides every one. */
export const usualApprovalRules: ApprovalRules = {
  forRun: () => Promise.resolve(null),
};

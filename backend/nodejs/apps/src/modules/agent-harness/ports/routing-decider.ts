import type {
  EffectiveRunConfig,
  PinBasis,
  RoutingFacts,
} from '../../control-plane';
import { RunInputSnapshot } from '../domain/execution';
import { HarnessRolloutDecision } from '../domain/rollout';

/**
 * One chat turn as routing sees it: who sends it, where, in which mode. Ids
 * come from the authenticated request and the route, never from the body;
 * `projectId` is the project a request names for a conversation it creates
 * (an existing conversation's project is read from its record).
 */
export interface RoutingSubject {
  orgId: string;
  actorId: string;
  agentKey?: string;
  sessionId?: string;
  projectId?: string;
  model: RunInputSnapshot['model'];
  /** A schedule's firing: no person waits on it, so worker liveness does not route it. */
  scheduled?: boolean;
  /** The assistant's turn asked for the durable harness (ASSIST1); the org setting decides. */
  durableRequested?: boolean;
  /** The person's chat switch asked for the classic path (CTL-7); admission decides once it knows the agent. */
  legacyRequested?: boolean;
}

/** What the turn runs against, decided by the server. */
export interface RoutingScope {
  agentKey?: string;
  projectId?: string;
}

/**
 * What the control plane resolved a turn's route on (CPD-10 §2). A pin made
 * on the same `facts` and `basis` holds the same route, and reads no head
 * again; a legacy turn's header is a projection of `config` and `basis`.
 */
export interface ResolvedRoute {
  facts: RoutingFacts;
  /** The snapshot the route was resolved on, with the revisions it was loaded at. */
  basis: PinBasis;
  /** The `rollout.*` rows as resolved: the stages and the routing lists. */
  config: EffectiveRunConfig;
}

export interface RoutingDecision {
  routing: HarnessRolloutDecision;
  scope: RoutingScope;
  /** Present when the resolved run configuration decided (`CONTROL_PLANE_PIN=on`). */
  resolved?: ResolvedRoute;
  /**
   * `CONTROL_PLANE_PIN=shadow`: what the control plane resolves beside the
   * decision, settling after it; undefined when it could not. Compared and
   * counted, never acted on, and no turn waits for it.
   */
  shadow?: Promise<ResolvedRoute | undefined>;
  /**
   * The turn asked for the classic path and its conversation has no durable turns: admission routes it to
   * today's loop unless the agent needs the harness (CTL-7).
   */
  classicRequested?: true;
}

/**
 * Durable or legacy, project scope and agent scope for one turn
 * (control-plane 05 §5). The first adapter wraps today's rollout and the
 * conversation's placement; the second (CPD-10) reads the resolved run
 * configuration.
 * Rejects only when it cannot decide; a decision to stay on today's loop is
 * a value.
 */
export interface RoutingDecider {
  decide(subject: RoutingSubject): Promise<RoutingDecision>;
}

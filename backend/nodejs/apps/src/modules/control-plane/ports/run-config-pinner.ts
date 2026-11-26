import type {
  PinBasis,
  PinResult,
  PinSubject,
  RouteResult,
  RouteSubject,
} from '../domain/resolution';

/** The one admission-time entry point (03-interfaces §4.4, FR-29). */
export interface RunConfigPinner {
  /**
   * Durable or today's loop for the subject's turn, from the rollout rows
   * alone: no agent, user or request layer is read. Rejects when the rows
   * cannot be resolved; a route is never guessed.
   */
  route(subject: RouteSubject): Promise<RouteResult>;
  /** `basis` (a route's, of the same org, agent and actor) is pinned on as it is: nothing is read again. */
  pin(subject: PinSubject, basis?: PinBasis): Promise<PinResult>;
}

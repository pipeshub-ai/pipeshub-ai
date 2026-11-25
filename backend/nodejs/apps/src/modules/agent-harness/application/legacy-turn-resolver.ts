import {
  type PinBasis,
  type PinSubject,
  type ResolvedRunConfig,
  type Routing,
  type RunConfigPinner,
} from '../../control-plane';
import { HarnessRunConfigUnavailableError } from '../domain/errors';
import type { ResolvedRoute } from '../ports/routing-decider';

/** One chat turn today's loop serves, as the server knows it: ids from the authenticated request and the route. */
export interface LegacyTurn {
  orgId: string;
  actorId: string;
  /** The query route's agent; null for the built-in assistant. */
  agentKey: string | null;
  chatMode: string;
  /** The project a request names for the conversation it creates. */
  projectId?: string;
}

/** What a legacy turn's header is made of. */
export interface LegacyTurnResolution {
  pin: ResolvedRunConfig;
  /** Why the turn is on today's loop. */
  routing: Routing;
  /** Today's readers cannot read an approvals layer the turn narrows through. */
  unreadableApprovals: boolean;
}

/**
 * Policy would route the turn durable and today's loop serves it all the
 * same: a route or a request the durable path does not take, or an admission
 * that could not be made and wrote nothing.
 */
export const NOT_SERVED_DURABLY = 'not_served_durably';

export interface LegacyTurnResolverOptions {
  pinner: RunConfigPinner;
  /** `holdsUnreadableLayer` over the approvals document as it is now; absent: no document is wired. */
  unreadableApprovals?: (
    orgId: string,
    agentKey: string | null,
  ) => Promise<boolean>;
}

/**
 * The pin of a turn today's loop serves (CPD-10 §3). Bound only under
 * `CONTROL_PLANE_PIN=on`: in `off` and `shadow` there is no resolver, so
 * nothing is read. The subject holds what is known without asking the query service for
 * the agent's definition: the stored layers of the org, the agent and the
 * actor are in the snapshot; the definition's own narrowing, the agent's
 * owner and the request's model are not, and the header carries none of them.
 * A pin the control plane refuses or cannot make is `RUN_CONFIG_UNAVAILABLE`:
 * the turn is never handed to today's loop without one.
 */
export class LegacyTurnResolver {
  /** A snapshot is never changed in place, so what the document said of it is asked once. */
  private readonly unreadable = new WeakMap<PinBasis, Promise<boolean>>();

  constructor(private readonly options: LegacyTurnResolverOptions) {}

  /** `routed`: what the chat-turn middleware resolved the turn's route on, when it ran. */
  async resolve(
    turn: LegacyTurn,
    routed?: ResolvedRoute,
  ): Promise<LegacyTurnResolution> {
    try {
      return await this.pinned(turn, routed);
    } catch (error) {
      throw error instanceof HarnessRunConfigUnavailableError
        ? error
        : new HarnessRunConfigUnavailableError();
    }
  }

  private async pinned(
    turn: LegacyTurn,
    routed: ResolvedRoute | undefined,
  ): Promise<LegacyTurnResolution> {
    // A route resolved for another agent (Universal Agent Mode on an agent's conversation) is not this turn's.
    const own = routed?.basis.agentKey === turn.agentKey ? routed : undefined;
    const subject: PinSubject = {
      orgId: turn.orgId,
      actorId: turn.actorId,
      agentKey: turn.agentKey,
      projectId: own?.facts.placement?.projectId ?? turn.projectId ?? null,
      chatMode: turn.chatMode,
      surface: 'web',
      request: { schemaVersion: 1, scope: { kind: 'request' } },
      ...(own !== undefined ? { routingFacts: own.facts } : {}),
    };
    const basis =
      own?.basis ?? (await this.options.pinner.route(subject)).basis;
    const [pin, unreadableApprovals] = await Promise.all([
      this.options.pinner.pin(subject, basis),
      this.unreadableOn(basis, turn),
    ]);
    // A pin the control plane refuses: today's loop would run the turn on the live documents.
    if ('code' in pin) throw new HarnessRunConfigUnavailableError();
    return {
      pin,
      routing:
        pin.routing.path === 'legacy'
          ? pin.routing
          : { path: 'legacy', reason: NOT_SERVED_DURABLY },
      unreadableApprovals,
    };
  }

  private unreadableOn(basis: PinBasis, turn: LegacyTurn): Promise<boolean> {
    const read = this.options.unreadableApprovals;
    if (read === undefined) return Promise.resolve(false);
    let asked = this.unreadable.get(basis);
    if (asked === undefined) {
      asked = read(turn.orgId, turn.agentKey);
      this.unreadable.set(basis, asked);
      // A read that failed is asked again, not remembered.
      asked.catch(() => this.unreadable.delete(basis));
    }
    return asked;
  }
}

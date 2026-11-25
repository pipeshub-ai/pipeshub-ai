import type {
  HeadRevisions,
  MeCapabilitiesSource,
  TurnRoute,
  TurnRoutes,
} from '../../control-plane';
import type { RoutingDecider } from '../ports/routing-decider';

/** The chat modes an agent's routes take (`routing.json`'s `chatMode` levels for an agent). */
export const ROUTED_AGENT_MODES = [
  'quick',
  'auto',
  'react',
  'deep',
  'planExecute',
  'verification',
] as const;

/** The built-in assistant's: its two search modes, Universal Agent Mode, and the default of a body that names none. */
export const ROUTED_ASSISTANT_MODES = [
  'internal_search',
  'web_search',
  'agent',
  'quick',
] as const;

/** Routing reads no agent's own layer, so any key routes as every agent does. */
const ANY_AGENT = 'any-agent';

export interface RoutingCapabilitiesOptions {
  /** Who decides a turn in this process: today's rollout, or the resolved configuration under `CONTROL_PLANE_PIN=on`. */
  decider: RoutingDecider;
  heads: Pick<HeadRevisions, 'head'>;
}

/**
 * `/me/capabilities` (CPD-10 §6.3): each chat mode's route for the caller's
 * next new conversation, asked of the decider that would route it.
 */
export class RoutingCapabilities implements MeCapabilitiesSource {
  constructor(private readonly options: RoutingCapabilitiesOptions) {}

  headRevision(orgId: string): Promise<number> {
    return this.options.heads.head(orgId);
  }

  async routing(orgId: string, userId: string): Promise<TurnRoutes> {
    const [agent, assistant] = await Promise.all([
      this.routes(orgId, userId, ROUTED_AGENT_MODES, ANY_AGENT),
      this.routes(orgId, userId, ROUTED_ASSISTANT_MODES),
    ]);
    return { agent, assistant };
  }

  private async routes(
    orgId: string,
    actorId: string,
    modes: readonly string[],
    agentKey?: string,
  ): Promise<Record<string, TurnRoute>> {
    const { decider } = this.options;
    const decided = await Promise.all(
      modes.map(async (chatMode): Promise<[string, TurnRoute]> => {
        const { routing } = await decider.decide({
          orgId,
          actorId,
          ...(agentKey !== undefined ? { agentKey } : {}),
          model: { chatMode },
        });
        return [
          chatMode,
          routing.useHarness
            ? { path: 'durable', reason: null }
            : { path: 'legacy', reason: routing.reason },
        ];
      }),
    );
    return Object.fromEntries(decided);
  }
}

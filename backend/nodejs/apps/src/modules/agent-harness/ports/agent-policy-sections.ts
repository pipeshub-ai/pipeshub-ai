import type { WriteContext } from '../../control-plane';

/** An agent layer's sections by name: what moves with the agent in an export (C4.08). */
export type PortableSections = Partial<Record<string, unknown>>;

/** What a scope's layer holds that moves with it, and the sections it holds that do not. */
export interface PortableLayer {
  readonly sections: PortableSections;
  readonly withheld: readonly string[];
}

/**
 * Where an agent's own policy sections are kept, for an agent export and import. Bound at
 * `CONTROL_PLANE_POLICY_ADMIN` where a deployment keeps agent layers; unbound, an agent travels without any.
 */
export interface AgentPolicySections {
  offers(section: string): boolean;
  /** Refuses sections a new agent's layer could not hold, before the agent is created. */
  check(
    orgId: string,
    scope: string,
    sections: PortableSections,
  ): Promise<void>;
  portable(orgId: string, scope: string): Promise<PortableLayer>;
  create(
    ctx: WriteContext,
    scope: string,
    sections: PortableSections,
  ): Promise<number>;
}

import type { PolicyLayer, ScopeKind } from '../domain/policy-alias';
import type { PinSubject, Snapshot } from '../domain/resolution';
import { teamLayerScope } from '../domain/team-scope';
import type { PolicyLayerSource } from '../ports/policy-layer-source';

/**
 * The team layer of a pin: the layer of the agent's owner team (02-lld §3.1),
 * which team admins write when the org delegates it. A run without an agent,
 * or an agent no team owns, has no team layer.
 */
export class TeamPolicyLayerSource implements PolicyLayerSource {
  readonly kind: ScopeKind = 'team';

  layer(_subject: PinSubject, snapshot: Snapshot): PolicyLayer | null {
    const scope = teamLayerScope(snapshot);
    return scope === null ? null : (snapshot.layers.get(scope) ?? null);
  }
}

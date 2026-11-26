import type { PolicyLayer, ScopeKind } from '../domain/policy-alias';
import type { PinSubject, Snapshot } from '../domain/resolution';

/**
 * Produces one layer kind for a subject (03-interfaces §4.2); adding a layer
 * kind is one source and one position in the list. Synchronous on purpose:
 * all I/O happens once in the snapshot loader, so a source cannot add a round
 * trip to the hot path.
 */
export interface PolicyLayerSource {
  readonly kind: ScopeKind;
  layer(subject: PinSubject, snapshot: Snapshot): PolicyLayer | null;
}

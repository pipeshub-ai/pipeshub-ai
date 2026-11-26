/**
 * Features an edition's bound services let this Node name to its workers on
 * each claim (an open vocabulary: a worker ignores a name it does not know).
 * Bound at `AGENT_HARNESS_CLAIM_FEATURES`; unbound: none. A feature is named
 * only while what serves it is bound, so a worker never relies on a route or
 * rule this Node does not have.
 */
export interface ClaimFeatureSource {
  features(): readonly string[];
}

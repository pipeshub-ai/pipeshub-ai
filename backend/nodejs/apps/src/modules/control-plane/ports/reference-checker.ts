import type { Problem } from '../contracts/generated';
import type { PolicyLayer } from '../domain/policy-alias';

/**
 * That every reference a layer makes exists in the org (03-interfaces §5.6):
 * user groups and users, installations in `tools.grant`, configured
 * models, engine kinds. One `Problem` per missing reference, in layer order.
 */
export interface ReferenceChecker {
  check(orgId: string, layer: PolicyLayer): Promise<Problem[]>;
}

import type { PolicyLayer } from './policy-alias';

/**
 * Who may use the harness, each coding engine and each configured model, as
 * the org's user groups (G-41, GA-21). Org layer only. Not a dimension:
 * admission re-reads the actor's membership at every admission.
 */
export const ACCESS_SECTION = 'access';

export type AccessGroupsPolicy = NonNullable<PolicyLayer['access']>;

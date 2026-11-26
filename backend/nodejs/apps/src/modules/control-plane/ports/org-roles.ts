/** A person's live role in an org: `null` when they are not (or no longer) in it. */
export type OrgRole = 'admin' | 'member';

/**
 * Reads the role on every call (03-interfaces §5.1: a demoted admin is denied
 * on the next request, never by a role claim cached at login).
 */
export interface OrgRoles {
  roleOf(orgId: string, userId: string): Promise<OrgRole | null>;
}

/** The actions an org delegates to its teams' admins (`catalog.delegation`). */
export interface DelegationSource {
  delegatedActions(orgId: string): Promise<readonly string[]>;
}

/** Membership of the org's user groups. */
export interface GroupMembership {
  isMember(orgId: string, groupId: string, userId: string): Promise<boolean>;
}

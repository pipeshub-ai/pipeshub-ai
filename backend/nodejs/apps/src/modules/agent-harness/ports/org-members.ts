/**
 * Whether a user is a live member of an org, from the org's own user records (never the request). A route that
 * names another user resolves them with it inside the caller's org first, so a user of another org is not found
 * before anything else is said about the route (QA-26).
 */
export interface OrgMemberLookup {
  isMember(orgId: string, userId: string): Promise<boolean>;
}

/** How an admin page names a member: from the org's own user records. */
export interface MemberProfile {
  name: string;
  email: string;
}

/**
 * Names for user ids an org's records hold (AUDITUI), read when a page is
 * shown and never stored with the record. An id that is not a live user of
 * this org (deleted, another org's) is absent from the answer.
 */
export interface OrgMemberProfiles {
  profiles(
    orgId: string,
    userIds: readonly string[],
  ): Promise<Map<string, MemberProfile>>;
}

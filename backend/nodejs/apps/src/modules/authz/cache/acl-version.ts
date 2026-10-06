/**
 * `aclVersion` is bumped in the same write that changes who can access a chat
 * or project, so decision-cache keys that embed it can never serve a decision
 * computed before that write (permissions-inheritance 50 §6.1).
 */

/** Mongo update fragment: merge into an existing update as `{ ...update, ...ACL_VERSION_INC }`. */
export const ACL_VERSION_INC = { $inc: { aclVersion: 1 } } as const;

/** `.lean()` skips schema defaults, so a missing value is version 0. */
export const readAclVersion = (
  doc: { aclVersion?: number | null } | null | undefined,
): number => doc?.aclVersion ?? 0;

/** For the `doc.field = ...; doc.save()` writers. */
export const bumpAclVersionOnDoc = (doc: {
  aclVersion?: number | null;
}): void => {
  doc.aclVersion = readAclVersion(doc) + 1;
};

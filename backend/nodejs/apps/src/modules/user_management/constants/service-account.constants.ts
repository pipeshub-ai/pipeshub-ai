/**
 * Service accounts are user records that no person signs in as. Automation
 * authenticates as one, and it reads with its own permissions rather than
 * borrowing a colleague's.
 *
 * They are ordinary users in every way the permission graph cares about,
 * because that is the point: the graph already knows how to answer "what can
 * this user see", and a service account should get that same answer rather
 * than a second, parallel notion of access that has to be kept in step.
 */

/**
 * Every service account gets an address under this domain.
 *
 * Email is required and unique on the user record, and the permission-graph
 * sync looks users up by email, so a service account cannot simply go without
 * one. `.internal` is reserved by RFC 8375 and never resolves on the public
 * internet, so mail addressed here cannot escape to a real mailbox even if
 * some future code path tries to send it.
 */
export const SERVICE_ACCOUNT_EMAIL_DOMAIN = 'service.pipeshub.internal';

/** Service accounts are always members. See {@link assertServiceAccountRole}. */
export const SERVICE_ACCOUNT_ROLE = 'member' as const;

/**
 * Refuses to let a service account hold the admin role.
 *
 * Setting the role at creation is not enough on its own. A service account is
 * an ordinary user record, so every path that edits users can reach it — the
 * role-update endpoint and the invite processor both write `role` — and
 * `isUserOrgAdmin` reads that field without caring what kind of principal it
 * belongs to. Promoting one would produce exactly what service accounts exist
 * to avoid: admin rights with no person attached to them.
 *
 * This is called from the user schema's save and update hooks, so it applies
 * wherever the role is written rather than only where service accounts are
 * created.
 */
export const SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE =
  'A service account cannot be an administrator';

export function assertServiceAccountRole(
  kind: string | undefined,
  role: string | undefined,
): void {
  if (kind === 'service' && role === 'admin') {
    throw new Error(SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE);
  }
}

/**
 * Slugs are what the operator names the account, and they become the local
 * part of its address, so they have to be safe to put there: lowercase
 * letters, digits and single hyphens, not starting or ending with one.
 */
const SERVICE_ACCOUNT_SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export const SERVICE_ACCOUNT_SLUG_MIN_LENGTH = 3;
export const SERVICE_ACCOUNT_SLUG_MAX_LENGTH = 48;

export function isValidServiceAccountSlug(slug: string): boolean {
  return (
    slug.length >= SERVICE_ACCOUNT_SLUG_MIN_LENGTH &&
    slug.length <= SERVICE_ACCOUNT_SLUG_MAX_LENGTH &&
    SERVICE_ACCOUNT_SLUG_PATTERN.test(slug)
  );
}

/**
 * The address for a service account slug.
 *
 * The org id is part of the local part because email is unique across the
 * whole users collection, not per organisation: without it, two orgs could
 * not both have an account called `nightly-sync`.
 */
export function buildServiceAccountEmail(slug: string, orgId: string): string {
  return `svc-${slug}-${orgId}@${SERVICE_ACCOUNT_EMAIL_DOMAIN}`.toLowerCase();
}

/**
 * Whether an address belongs to the service-account space.
 *
 * This is a convenience for reading and for defence in depth. It is never the
 * thing that decides whether a record is a service account — the `kind` field
 * is. What keeps the two in step is
 * {@link assertReservedEmailDomainBelongsToServiceAccount}, which refuses to
 * store an address here on anything else.
 */
export function isServiceAccountEmail(
  email: string | undefined | null,
): boolean {
  if (email === undefined || email === null || email === '') return false;
  return email.toLowerCase().endsWith(`@${SERVICE_ACCOUNT_EMAIL_DOMAIN}`);
}

/**
 * Recovers the operator-facing name from a service account's address.
 *
 * The slug is not stored on the record. The address is built from it and is
 * already unique, so keeping a second copy would only create something that
 * can drift out of step with the first. `orgId` is required because it forms
 * part of the local part.
 */
export function serviceAccountSlugFromEmail(
  email: string,
  orgId: string,
): string {
  const localPart = email.split('@')[0] ?? '';
  const withoutPrefix = localPart.startsWith('svc-')
    ? localPart.slice('svc-'.length)
    : localPart;
  const orgSuffix = `-${orgId.toLowerCase()}`;
  return withoutPrefix.endsWith(orgSuffix)
    ? withoutPrefix.slice(0, -orgSuffix.length)
    : withoutPrefix;
}

/**
 * The domain is reserved, so only a service account may hold an address in it.
 *
 * Without this the domain is a naming convention rather than a reservation. A
 * person invited at `svc-nightly-<orgId>@service.pipeshub.internal` would read
 * as a machine identity everywhere the address is shown, while being a human
 * account that can sign in and hold a password — which is the opposite of what
 * a service account is, and the opposite of what someone reviewing the account
 * list would conclude. It would also take a name a real service account might
 * later need, since email is unique across the collection.
 *
 * Enforced from the user schema's save and update hooks rather than at the
 * endpoints, because four paths set a human's address — create, bulk invite,
 * the CSV invite upload, and the change-email endpoint — and only the write
 * boundary covers all of them.
 */
export const SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE =
  `Only a service account can use an address at ${SERVICE_ACCOUNT_EMAIL_DOMAIN}`;

export function assertReservedEmailDomainBelongsToServiceAccount(
  kind: string | undefined,
  email: string | undefined | null,
): void {
  if (isServiceAccountEmail(email) && kind !== 'service') {
    throw new Error(SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE);
  }
}

/** Matches any address in the reserved domain, for querying stored records. */
export function reservedEmailDomainPattern(): RegExp {
  // The domain is a literal with a dot, which has to be escaped to match one.
  const domain = SERVICE_ACCOUNT_EMAIL_DOMAIN.replace(/\./g, '\\.');
  return new RegExp(`@${domain}$`, 'i');
}

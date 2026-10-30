import { getUserRepository } from '../../../libs/repositories/user';
import { getCredentialRepository } from '../../../libs/repositories/credential';

/** Reserved domain of the bundled demo personas (RFC 2606 `.example`). */
export const DEMO_ACCOUNT_DOMAIN = 'acme-demo.example';

export function isDemoAccountEmail(email: unknown): boolean {
  return (
    typeof email === 'string' &&
    email.trim().toLowerCase().endsWith(`@${DEMO_ACCOUNT_DOMAIN}`)
  );
}

/**
 * Let the org's sample accounts (Alice, Bob, ...) sign in, or stop them.
 * They were created with a shared starting password, so they are switched off
 * with the demo. Never the person making the change, whatever their address.
 * `isDisabled` refuses new sign-ins and ends existing sessions.
 */
export async function setSampleAccountsSignIn(
  orgId: string,
  callerUserId: string,
  enabled: boolean,
): Promise<number> {
  return getUserRepository().setDisabledByEmailDomain(
    orgId,
    DEMO_ACCOUNT_DOMAIN,
    callerUserId,
    !enabled,
  );
}

/**
 * A removed sample account is only soft-deleted, and its address stays taken
 * by the unique index, so the demo's accounts could never be created again.
 * Clear those leftovers (and their credentials) before creating one anew.
 */
export async function clearRemovedSampleAccount(
  email: string,
  orgId: string,
): Promise<void> {
  // This org's leftovers only. An address held by another org's removed
  // account stays theirs; the unique index then refuses the create.
  const users = getUserRepository();
  for (const id of await users.listRemovedIdsByEmail(orgId, email)) {
    // An invite may restore the account meanwhile: its credentials go only
    // with an account that was still removed when deleted.
    if (await users.deleteRemoved(orgId, id)) {
      await getCredentialRepository().deleteForUser(id, email);
    }
  }
}

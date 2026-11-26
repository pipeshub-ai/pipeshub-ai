import { getUserRepository } from '../../../libs/repositories/user';
import { getCredentialRepository } from '../../../libs/repositories/credential';
import type { PrincipalDeactivationPort } from '../../agent-harness/application/principal-deactivation';
import {
  oauthUserTokenRevoker,
  type UserTokenRevoker,
} from './user-token-revoker';

/** What switching a sample account off stops besides its sign-in (SEC-AZ-08). */
export interface SampleAccountStops {
  tokens?: UserTokenRevoker;
  /** The harness's; null when the harness is not composed (no runs, no schedules). */
  deactivation?: PrincipalDeactivationPort | null;
}

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
 * `isDisabled` refuses new sign-ins and ends existing sessions; switching
 * them off also revokes their tokens and stops their harness runs and
 * schedules, as an admin's deactivation does (SEC-AZ-08).
 */
export async function setSampleAccountsSignIn(
  orgId: string,
  callerUserId: string,
  enabled: boolean,
  stops: SampleAccountStops = {},
): Promise<number> {
  const users = getUserRepository();
  // Read before the switch: these are the accounts whose handed-out work is then stopped.
  const switchedOff = enabled
    ? []
    : (await users.listAllLive(orgId)).filter(
        (user) =>
          isDemoAccountEmail(user.email) &&
          user.isDisabled !== true &&
          user._id !== callerUserId,
      );
  const changed = await users.setDisabledByEmailDomain(
    orgId,
    DEMO_ACCOUNT_DOMAIN,
    callerUserId,
    !enabled,
  );
  const tokens = stops.tokens ?? oauthUserTokenRevoker;
  for (const { _id: userId } of switchedOff) {
    await tokens.revokeEveryTokenForUser(userId);
    await stops.deactivation?.deactivate(orgId, userId, callerUserId);
  }
  return changed;
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

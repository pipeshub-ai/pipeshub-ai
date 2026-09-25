import { Types } from 'mongoose';
import { Users } from '../schema/users.schema';
import { UserCredentials } from '../../auth/schema/userCredentials.schema';

/** Reserved domain of the bundled demo personas (RFC 2606 `.example`). */
export const DEMO_ACCOUNT_DOMAIN = 'acme-demo.example';

export function isDemoAccountEmail(email: unknown): boolean {
  return (
    typeof email === 'string' &&
    email.trim().toLowerCase().endsWith(`@${DEMO_ACCOUNT_DOMAIN}`)
  );
}

const escapeRegExp = (text: string): string =>
  text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const DEMO_EMAIL = new RegExp(`@${escapeRegExp(DEMO_ACCOUNT_DOMAIN)}$`, 'i');

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
  const filter: Record<string, unknown> = {
    orgId,
    isDeleted: false,
    email: DEMO_EMAIL,
  };
  if (Types.ObjectId.isValid(callerUserId)) {
    filter._id = { $ne: new Types.ObjectId(callerUserId) };
  }
  const result = await Users.updateMany(filter, {
    $set: { isDisabled: !enabled },
  });
  return result.modifiedCount;
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
  const stale = await Users.find({ email, orgId, isDeleted: true })
    .select('_id')
    .lean();
  for (const { _id } of stale) {
    // An invite may restore the account meanwhile: its credentials go only
    // with an account that was still removed when deleted.
    const removed = await Users.findOneAndDelete({
      _id,
      orgId,
      isDeleted: true,
    })
      .select('_id')
      .lean();
    if (removed) await UserCredentials.deleteMany({ userId: _id });
  }
}

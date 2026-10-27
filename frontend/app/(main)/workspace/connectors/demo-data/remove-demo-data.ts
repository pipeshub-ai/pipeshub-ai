import { isAxiosError } from 'axios';
import { isProcessedError } from '@/lib/api/api-error';
import { ConnectorsApi } from '../api';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import { DEMO_ACCOUNT_DOMAIN, isSampleAccountEmail } from './demo-data';

export interface SampleAccount {
  /** MongoDB id, which is what the delete endpoint takes. */
  userId: string;
  email: string;
  name?: string;
}

/**
 * Accounts on the reserved demo domain, such as Alice and Bob. Never the
 * person doing the removal, whatever address they signed up with.
 */
export async function findSampleAccounts(currentUserEmail?: string | null): Promise<SampleAccount[]> {
  const { users } = await UsersApi.listUsers({ search: DEMO_ACCOUNT_DOMAIN, limit: 100 });
  const me = (currentUserEmail ?? '').trim().toLowerCase();
  return users
    .filter((u) => !!u.userId && isSampleAccountEmail(u.email))
    .filter((u) => (u.email ?? '').trim().toLowerCase() !== me)
    .map((u) => ({ userId: u.userId, email: u.email as string, name: u.name }));
}

function httpStatusOf(error: unknown): number | undefined {
  if (isProcessedError(error)) return error.statusCode;
  if (isAxiosError(error)) return error.response?.status;
  return undefined;
}

/**
 * Whether a failed delete means the connector is already gone or on its way
 * out. Deleting is not repeatable: a connector already being deleted answers
 * 409, so a retry after a partial failure must not stop there. A 409 can also
 * mean an agent still uses the connector, so only a deletion in progress counts.
 */
async function alreadyRemoved(connectorId: string, error: unknown): Promise<boolean> {
  const status = httpStatusOf(error);
  if (status === 404) return true;
  if (status !== 409) return false;
  try {
    const instance = await ConnectorsApi.getConnectorInstance(connectorId);
    return instance.status === CONNECTOR_INSTANCE_STATUS.DELETING;
  } catch (lookupError) {
    return httpStatusOf(lookupError) === 404;
  }
}

export interface RemoveDemoDataResult {
  /** Sample accounts that could not be deleted; the demo data itself is gone. */
  failedAccounts: SampleAccount[];
}

/**
 * Delete the Demo connector instances, then the chosen sample accounts.
 *
 * The connector goes first because it is what puts Acme Corp into answers. If
 * it fails, this throws and nothing else is touched; an account that fails
 * afterwards is reported rather than undoing a removal that already happened.
 * A connector already gone or being deleted counts as done, so running this
 * again after a partial failure picks up where it stopped.
 */
export async function removeDemoData(
  connectorIds: string[],
  accounts: SampleAccount[],
): Promise<RemoveDemoDataResult> {
  for (const id of connectorIds) {
    try {
      await ConnectorsApi.deleteConnectorInstance(id);
    } catch (error) {
      if (!(await alreadyRemoved(id, error))) throw error;
    }
  }
  const failedAccounts: SampleAccount[] = [];
  for (const account of accounts) {
    try {
      await UsersApi.deleteUser(account.userId);
    } catch {
      failedAccounts.push(account);
    }
  }
  return { failedAccounts };
}

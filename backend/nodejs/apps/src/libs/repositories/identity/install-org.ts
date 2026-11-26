import { forOrg, getCurrentOrgId } from '../../context/orgContext';
import { docStore } from '../store-select';
import { getIdentityPort } from './index';

/**
 * Runs a lookup that arrives with no organisation bound (a sign-in by address) as the install's organisation. The store
 * is PostgreSQL only: MongoDB answers such a lookup without a context, so nothing is bound or read for it. With no
 * organisation yet, `none` is the answer.
 */
export async function inInstallOrg<T>(
  reason: string,
  fn: () => Promise<T>,
  none: T,
): Promise<T> {
  if (getCurrentOrgId() !== undefined || docStore() !== 'postgres') return fn();
  const orgId = await getIdentityPort().oldestOrgId();
  return orgId === null ? none : forOrg(orgId, reason, fn);
}

import { forOrg } from '../../../libs/context/orgContext';
import { getOrgRepository } from '../../../libs/repositories/org';
import { findOrgAdminUserIds } from '../../user_management/services/user-admin.service';

/** One organisation whose admins receive an installation-wide alert. */
export interface PlatformRecipientOrg {
  orgId: string;
  userIds: string[];
}

/**
 * Who receives alerts about the whole installation rather than one
 * organisation (such as the code sandbox load alerts): the admins of the
 * installation's organisation. Empty when there is no organisation yet or it
 * has no admins.
 */
export async function resolvePlatformAdminRecipients(): Promise<
  PlatformRecipientOrg[]
> {
  const org = await getOrgRepository().getFirstLive();
  if (!org) return [];
  const orgId = org._id;
  const userIds = await forOrg(orgId, 'platform-alert-recipients', () =>
    findOrgAdminUserIds(orgId),
  );
  return userIds.length ? [{ orgId, userIds }] : [];
}

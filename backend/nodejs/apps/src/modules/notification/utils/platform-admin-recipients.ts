import { forOrg, getCurrentOrgId } from '../../../libs/context/orgContext';
import { getOrgRepository } from '../../../libs/repositories/org';
import { Logger } from '../../../libs/services/logger.service';
import { findOrgAdminUserIds } from '../../user_management/services/user-admin.service';

/** One organisation whose admins receive an installation-wide alert. */
export interface PlatformRecipientOrg {
  orgId: string;
  userIds: string[];
}

/**
 * Who receives alerts about the whole installation rather than one
 * organisation (such as the code sandbox load alerts): the admins of the
 * installation's organisation. Their numbers cover the whole installation, so
 * they go out only while exactly one organisation is live: with several (an
 * organisation restored next to a newer one, say) the first one found could
 * be any of them, and nobody is told; the process then logs one warning, as
 * it does while none is live. Empty too when there is no organisation
 * yet or it has no admins.
 */
export async function resolvePlatformAdminRecipients(): Promise<
  PlatformRecipientOrg[]
> {
  const { count, oldestId: orgId } = await getOrgRepository().liveSummary();
  if (count !== 1 || orgId === null) {
    warnNotDeliveredOnce(count === 0 ? 'none' : 'several');
    return [];
  }
  // A signed copy of an alert is delivered inside the organisation it names: another one's copy gets nothing, and the
  // installation's organisation is never bound inside it.
  const bound = getCurrentOrgId();
  if (bound !== undefined && bound !== orgId) return [];
  const userIds = await forOrg(orgId, 'platform-alert-recipients', () =>
    findOrgAdminUserIds(orgId),
  );
  return userIds.length ? [{ orgId, userIds }] : [];
}

const warned = new Set<'none' | 'several'>();

/** Once per process and reason, naming no organisation: an operator has to learn why the alerts stopped. */
function warnNotDeliveredOnce(reason: 'none' | 'several'): void {
  if (warned.has(reason)) return;
  warned.add(reason);
  Logger.getInstance({ service: 'platform-alert-recipients' }).warn(
    reason === 'none'
      ? 'Installation alerts are not delivered: no organisation is live'
      : 'Installation alerts are not delivered: more than one organisation is live',
  );
}

/** Test hook: the next call with no or several live organisations warns again. */
export function resetPlatformRecipientWarnings(): void {
  warned.clear();
}

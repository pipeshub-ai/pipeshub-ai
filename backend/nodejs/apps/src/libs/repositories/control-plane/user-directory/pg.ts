import type { GroupMembership } from '../../../../modules/control-plane/ports/org-roles';
import { getGroupRepository } from '../../group';
import { oidOf } from '../../pg-util';
import { cpUnit } from '../pg-units';

export { LiveOrgRoles } from './mongo';

/**
 * Membership of the org's user groups through main's group repository, which reads
 * `identity.user_group_members` on PostgreSQL. A deleted group makes nobody a member.
 */
export class RepositoryGroupMembership implements GroupMembership {
  async isMember(
    orgId: string,
    groupId: string,
    userId: string,
  ): Promise<boolean> {
    if (![orgId, groupId, userId].every((id) => oidOf(id) !== undefined))
      return false;
    const groups = await cpUnit(undefined, orgId, { write: false }, () =>
      getGroupRepository().groupsOfUser(orgId, userId),
    );
    return groups.some((group) => group._id === oidOf(groupId));
  }
}

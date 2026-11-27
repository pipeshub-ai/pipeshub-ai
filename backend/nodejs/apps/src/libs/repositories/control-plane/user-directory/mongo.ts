import mongoose from 'mongoose';
import { UserGroups } from '../../../../modules/user_management/schema/userGroup.schema';
import { getActiveUserOrgRole } from '../../../../modules/user_management/services/user-admin.service';
import type {
  GroupMembership,
  OrgRole,
  OrgRoles,
} from '../../../../modules/control-plane/ports/org-roles';

/** The live org role from the users collection (`User.role`), read on every call. */
export class LiveOrgRoles implements OrgRoles {
  roleOf(orgId: string, userId: string): Promise<OrgRole | null> {
    return getActiveUserOrgRole(userId, orgId);
  }
}

/** Membership of the org's user groups (`userGroups`). */
export class UserGroupMembership implements GroupMembership {
  async isMember(
    orgId: string,
    groupId: string,
    userId: string,
  ): Promise<boolean> {
    if (![orgId, groupId, userId].every((id) => mongoose.isValidObjectId(id)))
      return false;
    return (
      (await UserGroups.exists({
        _id: groupId,
        orgId,
        isDeleted: { $ne: true },
        users: userId,
      })) !== null
    );
  }
}

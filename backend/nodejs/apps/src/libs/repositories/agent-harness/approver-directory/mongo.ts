import mongoose from 'mongoose';
import { ApproverDirectory } from '../../../../modules/agent-harness/ports/approver-directory';
import type { ApproverPeople } from './people';
import type { OrgMemberLookup } from '../../../../modules/agent-harness/ports/org-members';
import { Users } from '../../../../modules/user_management/schema/users.schema';
import { isUserOrgAdmin } from '../../../../modules/user_management/services/user-admin.service';

/** `ApproverPeople` over the Mongo `users` collection. */
export class MongoApproverPeople implements ApproverPeople {
  async isLiveUser(orgId: string, userId: string): Promise<boolean> {
    if (!mongoose.isValidObjectId(orgId) || !mongoose.isValidObjectId(userId))
      return false;
    return (
      (await Users.exists({ _id: userId, orgId, isDeleted: { $ne: true } })) !==
      null
    );
  }
}

/**
 * Admins from the user's org role; nobody approves by group, so an approval
 * is the run's actor's alone.
 */
export class HarnessApproverDirectory
  implements ApproverDirectory, OrgMemberLookup
{
  constructor(
    private readonly people: ApproverPeople = new MongoApproverPeople(),
  ) {}

  async isOrgAdmin(orgId: string, userId: string): Promise<boolean> {
    return isUserOrgAdmin(userId, orgId);
  }

  /** QA-26 (`OrgMemberLookup`): a live user of this org; an id of another shape, org or a deleted user is not. */
  async isMember(orgId: string, userId: string): Promise<boolean> {
    return this.people.isLiveUser(orgId, userId);
  }

  /** Nobody but the run's actor approves here; an edition's directory may name others. */
  isApprover(_orgId: string, _userId: string): Promise<boolean> {
    return Promise.resolve(false);
  }
}

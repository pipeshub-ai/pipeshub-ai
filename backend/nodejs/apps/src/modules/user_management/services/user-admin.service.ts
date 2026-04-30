import { UserGroups, type UserGroup } from '../schema/userGroup.schema';

export const isUserOrgAdmin = async (
  userId: string,
  orgId: string,
): Promise<boolean> => {
  const groups = await UserGroups.find({
    orgId,
    users: { $in: [userId] },
    isDeleted: false,
  }).select('type');

  return groups.some((userGroup: UserGroup) => userGroup.type === 'admin');
};

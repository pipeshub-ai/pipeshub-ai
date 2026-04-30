import { UserGroups } from '../schema/userGroup.schema';

export const isUserOrgAdmin = async (
  userId: string,
  orgId: string,
): Promise<boolean> => {
  const groups = await UserGroups.find({
    orgId,
    users: { $in: [userId] },
    isDeleted: false,
  }).select('type');

  return groups.some((userGroup: any) => userGroup.type === 'admin');
};

import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { UserGroups } from '../schema/userGroup.schema';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';

export const userAdminCheck = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    if (!userId || !orgId) {
      throw new NotFoundError('Account not found');
    }

    const groups = await UserGroups.find({
      orgId,
      users: { $in: [userId] },
      isDeleted: false,
    }).select('type');

    const isAdmin = groups.find((userGroup: any) => userGroup.type === 'admin');

    if (!isAdmin) {
      throw new BadRequestError('Admin access required');
    }
    next();
  } catch (error) {
    next(error);
  }
};

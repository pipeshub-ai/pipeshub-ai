import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { UserGroups } from '../schema/userGroup.schema';
import { Types } from 'mongoose';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
export const userAdminOrSelfCheck = async (
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

    const isAdmin = groups.find((usergrp) => usergrp.type === 'admin');

    const { id } = req.params;

    if (!isAdmin && !new Types.ObjectId(userId).equals(id)) {
      throw new BadRequestError(
        "You dont have admin access and can't change other users",
      );
    }

    next();
  } catch (error) {
    next(error);
  }
};

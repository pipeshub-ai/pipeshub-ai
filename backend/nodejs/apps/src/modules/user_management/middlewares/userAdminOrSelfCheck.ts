import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Types } from 'mongoose';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { isUserOrgAdmin } from '../services/user-admin.service';

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

    const isAdmin = await isUserOrgAdmin(userId, orgId);
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

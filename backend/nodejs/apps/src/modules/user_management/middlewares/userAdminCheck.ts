import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  ADMIN_ACCESS_REQUIRED_MESSAGE,
  isUserOrgAdmin,
} from '../services/user-admin.service';
import {
  ForbiddenError,
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

    const isAdmin = await isUserOrgAdmin(userId, orgId);

    if (!isAdmin) {
      throw new ForbiddenError(ADMIN_ACCESS_REQUIRED_MESSAGE);
    }
    next();
  } catch (error) {
    next(error);
  }
};

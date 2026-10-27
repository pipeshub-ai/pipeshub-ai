import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { isValidId } from '../../../libs/repositories/ids';
import {
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { isUserOrgAdmin } from '../services/user-admin.service';

export const userAdminOrSelfCheck = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string | undefined;
    const orgId = req.user?.orgId as string | undefined;
    if (!userId || !orgId) {
      throw new NotFoundError('Account not found');
    }

    const isAdmin = await isUserOrgAdmin(userId, orgId);
    const id = req.params.id as string;

    if (
      !isAdmin &&
      !(isValidId(userId) && userId.toLowerCase() === id.toLowerCase())
    ) {
      throw new ForbiddenError(
        "You don't have admin access and can't change other users",
      );
    }

    next();
  } catch (error) {
    next(error);
  }
};

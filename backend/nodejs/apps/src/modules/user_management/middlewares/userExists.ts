import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Org } from '../schema/org.schema';
import { Users } from '../schema/users.schema';
import { NotFoundError } from '../../../libs/errors/http.errors';

export const userExists = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
) => {
  try {
    const org = await Org.findOne({
      _id: req.user?.orgId,
      isDeleted: false,
    });

    if (!org) {
      throw new NotFoundError('Account not found');
    }
    const { id } = req.params;
    // JWT puts the acting user on req.user as { userId, orgId }. Downstream
    // handlers still expect the target user document here, but must keep the
    // actor id for privilege checks (e.g. role changes).
    const actorUserId = req.user?.userId;

    const user = await Users.findOne({
      _id: id,
      orgId: req.user?.orgId,
      isDeleted: false,
    }).exec();

    if (!user) {
      throw new NotFoundError('User not found');
    }
    req.user = user;
    if (actorUserId) {
      (req.user as any).userId = actorUserId;
    }
    next();
  } catch (error) {
    next(error);
  }
};

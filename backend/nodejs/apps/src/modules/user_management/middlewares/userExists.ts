import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { getOrgRepository } from '../../../libs/repositories/org';
import { getUserRepository } from '../../../libs/repositories/user';
import { NotFoundError } from '../../../libs/errors/http.errors';

export const userExists = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
) => {
  try {
    const org = await getOrgRepository().getLive(req.user?.orgId as string);

    if (!org) {
      throw new NotFoundError('Account not found');
    }
    const { id } = req.params;
    // JWT puts the acting user on req.user as { userId, orgId }. Downstream
    // handlers still expect the target user document here, but must keep the
    // actor id for privilege checks (e.g. role changes).
    const actorUserId = req.user?.userId;

    const user = await getUserRepository().getLive(
      req.user?.orgId as string,
      id as string,
    );

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

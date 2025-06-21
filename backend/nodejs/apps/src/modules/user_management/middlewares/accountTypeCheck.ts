import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Org } from '../schema/org.schema';

export const accountTypeCheck = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const orgId = req.user?.orgId;
    if (!orgId) {
      throw new NotFoundError('Account not found');
    }
    const org = await Org.findOne({ orgId, isDeleted: false });
    if (!org) {
      throw new BadRequestError('Organisation not found');
    }
    if (org.accountType === 'individual') {
      throw new BadRequestError('Access denied for individual accounts');
    }
    next();
  } catch (error) {
    next(error);
  }
};

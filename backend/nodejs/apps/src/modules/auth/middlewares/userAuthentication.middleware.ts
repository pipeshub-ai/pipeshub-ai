import { Response, NextFunction } from 'express';
import { isJwtTokenValid } from '../utils/validateJwt';
import { AuthSessionRequest } from './types';
import { SessionService } from '../services/session.service';
import {
  BadRequestError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { UserGroups } from '../../user_management/schema/userGroup.schema';
import { AppConfig } from '../../tokens_manager/config/config';

export const userValidator = (
  req: AuthSessionRequest,
  _res: Response,
  next: NextFunction,
) => {
  try {
    const container = req.container;
    if (!container) {
      throw new NotFoundError('Auth Container not found');
    }
    const config = container.get<AppConfig>('AppConfig');

    const decodedData = isJwtTokenValid(req, config.jwtSecret);
    if (!decodedData) {
      throw new UnauthorizedError('Invalid Token');
    }
    req.user = decodedData;
    next();
  } catch (error) {
    next(error);
  }
};

export const adminValidator = async (
  req: AuthSessionRequest,
  _res: Response,
  next: NextFunction,
) => {
  try {
    const container = req.container;
    if (!container) {
      throw new NotFoundError('Auth Container not found');
    }
    const config = container.get<AppConfig>('AppConfig');
    const decodedData = isJwtTokenValid(req, config.jwtSecret);
    if (!decodedData) {
      throw new UnauthorizedError('Invalid Token');
    }
    req.user = decodedData;
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

    let isAdmin = groups.find((userGroup: any) => userGroup.type === 'admin');

    if (!isAdmin) {
      throw new BadRequestError('Admin access required');
    }

    next();
  } catch (error) {
    next(error);
  }
};

export const authSessionMiddleware = async (
  req: AuthSessionRequest,
  _res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const container = req.container;
    if (!container) {
      throw new NotFoundError('Auth container not found');
    }
    const sessionService = container.get<SessionService>('SessionService');
    const sessionToken = req.headers['x-session-token'] as string;
    if (!sessionToken) {
      throw new UnauthorizedError('Invalid session token');
    }
    const session = await sessionService.getSession(sessionToken);
    if (!session) {
      throw new UnauthorizedError('Invalid session');
    }
    req.sessionInfo = session;
    next();
  } catch (error) {
    next(error);
  }
};

// auth.middleware.ts
import { Response, NextFunction, Request, RequestHandler } from 'express';
import { UnauthorizedError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import { AuthenticatedServiceRequest, AuthenticatedUserRequest } from './types';
import { AuthTokenService } from '../services/authtoken.service';
import { inject, injectable } from 'inversify';
import { UserActivities } from '../../modules/auth/schema/userActivities.schema';
import { userActivitiesType } from '../utils/userActivities.utils';

const { LOGOUT } = userActivitiesType;

@injectable()
export class AuthMiddleware {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('AuthTokenService') private tokenService: AuthTokenService,
  ) {
    this.authenticate = this.authenticate.bind(this);
  }

  async authenticate(
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ) {
    try {
      const token = this.extractToken(req);
      if (!token) {
        throw new UnauthorizedError('No token provided');
      }

      const decoded = await this.tokenService.verifyToken(token);
      req.user = decoded;

      // Search for user activities for this user
      const userId = decoded?.userId;
      const orgId = decoded?.orgId;

      if (userId && orgId) {
        let userActivity: any;
        try {
          userActivity = await UserActivities.findOne({
            userId: userId,
            orgId: orgId,
            isDeleted: false,
            activityType: LOGOUT,
          })
            .sort({ createdAt: -1 }) // Sort by most recent first
            .lean()
            .exec();
          
        } catch (activityError) {
          this.logger.error('Failed to fetch user activity', activityError);
        }

        if(userActivity) {
          const tokenIssuedAt = decoded.iat ? decoded.iat * 1000 : 0;
          const logoutTimestamp = (userActivity?.createdAt).getTime() 
          if (logoutTimestamp > tokenIssuedAt ) {
            this.logger.error('Need to send session expired notification to user');
            throw new UnauthorizedError('Session expired, please login again');
          }
        }
      }

      this.logger.debug('User authenticated', decoded);
      next();
    } catch (error) {
      next(error);
    }
  }

  scopedTokenValidator = (scope: string): RequestHandler => {
    return async (
      req: AuthenticatedServiceRequest,
      _res: Response,
      next: NextFunction,
    ) => {
      try {
        const token = this.extractToken(req);

        if (!token) {
          throw new UnauthorizedError('No token provided');
        }

        const decoded = await this.tokenService.verifyScopedToken(token, scope);
        req.tokenPayload = decoded;

        this.logger.debug('User authenticated', decoded);
        next();
      } catch (error) {
        next(error);
      }
    };
  };

  extractToken(req: Request): string | null {
    const authHeader = req.headers.authorization;
    if (!authHeader) return null;

    const [bearer, token] = authHeader.split(' ');
    return bearer === 'Bearer' && token ? token : null;
  }
}

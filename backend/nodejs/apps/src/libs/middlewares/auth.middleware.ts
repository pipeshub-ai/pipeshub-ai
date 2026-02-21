// auth.middleware.ts
import { Response, NextFunction, Request, RequestHandler } from 'express';
import { UnauthorizedError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import { AuthenticatedServiceRequest, AuthenticatedUserRequest } from './types';
import { AuthTokenService } from '../services/authtoken.service';
import { inject, injectable } from 'inversify';
import { UserActivities } from '../../modules/auth/schema/userActivities.schema';
import { userActivitiesType } from '../utils/userActivities.utils';
import { TokenScopes } from '../enums/token-scopes.enum';

const { LOGOUT, PASSWORD_CHANGED } = userActivitiesType;
// Delay in milliseconds between password change activity and token generation
const PASSWORD_CHANGE_TOKEN_DELAY_MS = 1000;

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
      // Normalize for OAuth tokens that use org_id / sub so controllers get orgId / userId
      if (req.user) {
        if (req.user.orgId === undefined && req.user.org_id !== undefined) {
          req.user.orgId = req.user.org_id;
        }
        if (req.user.userId === undefined && req.user.sub !== undefined) {
          req.user.userId = req.user.sub;
        }
      }

      // Search for user activities for this user
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      if (userId && orgId) {
        let userActivity: any;
        try {
          userActivity = await UserActivities.findOne({
            userId: userId,
            orgId: orgId,
            isDeleted: false,
            activityType: { $in: [LOGOUT, PASSWORD_CHANGED] },
          })
            .sort({ createdAt: -1 }) // Sort by most recent first
            .lean()
            .exec();
          
        } catch (activityError) {
          this.logger.error('Failed to fetch user activity', activityError);
        }

        if(userActivity) {
          const tokenIssuedAt = decoded.iat ? decoded.iat * 1000 : 0;
          const activityTimestamp = (userActivity?.createdAt).getTime() 
          if (activityTimestamp > tokenIssuedAt + PASSWORD_CHANGE_TOKEN_DELAY_MS) {
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

        const userId = decoded?.userId;
        const orgId = decoded?.orgId;

        this.logger.info(`userId: ${userId}, orgId: ${orgId}, scope: ${scope}`);
  
        if (userId && orgId && scope === TokenScopes.PASSWORD_RESET) {
          let userActivity: any;
          try {
            userActivity = await UserActivities.findOne({
              userId: userId,
              orgId: orgId,
              isDeleted: false,
              activityType: PASSWORD_CHANGED,
            })
              .sort({ createdAt: -1 }) // Sort by most recent first
              .lean()
              .exec();
            
          } catch (activityError) {
            this.logger.error('Failed to fetch user activity', activityError);
          }
  
          if(userActivity) {
            const tokenIssuedAt = decoded.iat ? decoded.iat * 1000 : 0;
            const activityTimestamp = (userActivity?.createdAt).getTime() 
            if (activityTimestamp > tokenIssuedAt ) {
              throw new UnauthorizedError('Password reset link expired, please request for a new link');
            }
          }
        }
  
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

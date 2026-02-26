// auth.middleware.ts
import { Response, NextFunction, Request, RequestHandler } from 'express';
import jwt from 'jsonwebtoken'
import { UnauthorizedError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import { AuthenticatedServiceRequest, AuthenticatedUserRequest } from './types';
import { AuthTokenService } from '../services/authtoken.service';
import { inject, injectable } from 'inversify';
import { IUserActivity, UserActivities } from '../../modules/auth/schema/userActivities.schema';
import { userActivitiesType } from '../utils/userActivities.utils';
import { TokenScopes } from '../enums/token-scopes.enum';
import { OAuthTokenService } from '../../modules/oauth_provider/services/oauth_token.service';
import { Users } from '../../modules/user_management/schema/users.schema';
import { Org } from '../../modules/user_management/schema/org.schema';
import { OAuthApp } from '../../modules/oauth_provider/schema/oauth.app.schema';
import { resolveOAuthTokenService } from '../services/oauth-token-service.provider';

export type OAuthTokenServiceFactory = () => OAuthTokenService | null;

const { LOGOUT, PASSWORD_CHANGED } = userActivitiesType;
// Delay in milliseconds between password change activity and token generation
const PASSWORD_CHANGE_TOKEN_DELAY_MS = 1000;

@injectable()
export class AuthMiddleware {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('AuthTokenService') private tokenService: AuthTokenService,
    private oauthTokenServiceFactory: OAuthTokenServiceFactory = resolveOAuthTokenService,
  ) {
    this.authenticate = this.authenticate.bind(this);
  }

  private isOAuthToken(
    decoded: Record<string, any> | null,
  ): boolean {
    return decoded !== null && decoded.tokenType === 'oauth' && typeof decoded.client_id === 'string' && typeof decoded.iss === 'string';
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

      // Security: Strip internal headers that could be spoofed
      delete req.headers['x-oauth-user-id'];

      // peek at the token payload to determine token type
      const rawDecoded = jwt.decode(token) as Record<string, any> | null;

      if (!this.isOAuthToken(rawDecoded)) {
        // authenticate regular token
        await this.authenticateRegularToken(token, req);
      } else {
        if (!this.oauthTokenServiceFactory()) {
          throw new UnauthorizedError('OAuth authentication is not configured');
        }
        // authenticate oauth token
        await this.authenticateOAuthToken(token, req);
      }

      next();
    } catch (error) {
      next(error);
    }
  }

  /**
   * Authenticate using a regular token.
   */
  private async authenticateRegularToken(
    token: string,
    req: AuthenticatedUserRequest,
  ): Promise<void> {
    const decoded = await this.tokenService.verifyToken(token);
    req.user = decoded;

    // search for user activities for this user
    const userId = decoded?.userId;
    const orgId = decoded?.orgId;
    const user = await Users.findOne({
      _id: userId,
      isDeleted: false,
    }).lean()
      .exec();
    if (!user) {
      throw new UnauthorizedError('User not found, please login again');
    }

    if (userId && orgId) {
      let userActivity: IUserActivity | null = null;
      try {
        userActivity = await UserActivities.findOne({
          userId: userId,
          orgId: orgId,
          isDeleted: false,
          activityType: { $in: [LOGOUT, PASSWORD_CHANGED] },
        })
          .sort({ createdAt: -1 }) // sort by most recent first
          .lean()
          .exec();

      } catch (activityError) {
        this.logger.error('Failed to fetch user activity', activityError);
      }

      if (userActivity) {
        const tokenIssuedAt = decoded.iat ? decoded.iat * 1000 : 0;
        const activityTimestamp = userActivity.createdAt?.getTime() || 0;
        if (activityTimestamp > tokenIssuedAt + PASSWORD_CHANGE_TOKEN_DELAY_MS) {
          throw new UnauthorizedError('Session expired, please login again');
        }
      }
    }

    this.logger.debug('User authenticated', decoded);
  }

  /**
   * Authenticate using an OAuth access token.
   * Verifies the token, enforces scopes, and populates req.user.
   */
  private async authenticateOAuthToken(
    token: string,
    req: AuthenticatedUserRequest,
  ): Promise<void> {
    const oauthTokenService = this.oauthTokenServiceFactory()!;

    // verify the oauth token (checks signature, expiry, revocation status)
    const payload = await oauthTokenService.verifyAccessToken(token);

    const tokenScopes = payload.scope.split(' ');

    let userId = payload.userId;
    const orgId = payload.orgId;
    let { fullName, accountType } = payload;

    // for client_credentials tokens (userId === client_id), resolve the app owner
    const isClientCredentials = userId === payload.client_id;
    if (isClientCredentials) {
      // prefer createdBy from JWT payload (embedded at token generation time)
      if (payload.createdBy) {
        userId = payload.createdBy;
      } else {
        // fallback for tokens generated before createdBy was embedded
        try {
          const app = await OAuthApp.findOne({
            clientId: payload.client_id,
            isDeleted: false,
          })
            .select('createdBy')
            .lean()
            .exec();
          if (app) {
            userId = app.createdBy.toString();
          } else {
            throw new UnauthorizedError('OAuth app not found or revoked');
          }
        } catch (err) {
          this.logger.error('Failed to look up OAuth app owner', err);
          throw new UnauthorizedError('Failed to look up OAuth app owner');
        }
      }
    }

    let email: string | undefined;
    if (userId) {
      try {
        const user = await Users.findOne({
          _id: userId,
          orgId: orgId,
          isDeleted: false,
        })
          .select('email fullName')
          .lean()
          .exec();

        if (user) {
          email = user.email;
          if (!fullName) {
            fullName = user.fullName;
          }
        }
      } catch (err) {
        this.logger.error('Failed to look up OAuth user email', err);
      }
    }

    if (!accountType && isClientCredentials) {
      try {
        const org = await Org.findOne({
          _id: orgId,
          isDeleted: false,
        })
          .select('accountType')
          .lean()
          .exec();
        if (org) {
          accountType = org.accountType;
        }
      } catch (err) {
        this.logger.error('Failed to look up org for OAuth token', err);
        throw new UnauthorizedError('Failed to look up org for OAuth token');
      }
    }

    req.user = {
      userId,
      orgId,
      email,
      fullName,
      accountType,
      isOAuth: true,
      oauthClientId: payload.client_id,
      oauthScopes: tokenScopes,
    };

    // for client_credentials grants, the token's userId is the client_id (not a real user).
    // pass the resolved app owner userId via header so Python services can use it.
    if (isClientCredentials) {
      req.headers['x-oauth-user-id'] = userId;
    }

    this.logger.debug('OAuth user authenticated', {
      userId,
      orgId,
      clientId: payload.client_id,
      scopes: tokenScopes,
    });
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
          let userActivity: IUserActivity | null = null;
          try {
            userActivity = await UserActivities.findOne({
              userId: userId,
              orgId: orgId,
              isDeleted: false,
              activityType: PASSWORD_CHANGED,
            })
              .sort({ createdAt: -1 }) // sort by most recent first
              .lean()
              .exec();

          } catch (activityError) {
            this.logger.error('Failed to fetch user activity', activityError);
          }

          if (userActivity) {
            const tokenIssuedAt = decoded.iat ? decoded.iat * 1000 : 0;
            const activityTimestamp = userActivity.createdAt?.getTime() || 0;
            if (activityTimestamp > tokenIssuedAt) {
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

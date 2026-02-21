// auth.middleware.ts
import { Response, NextFunction, Request, RequestHandler } from 'express';
import jwt from 'jsonwebtoken';
import { UnauthorizedError, ForbiddenError } from '../errors/http.errors';
import { Logger } from '../services/logger.service';
import { AuthenticatedServiceRequest, AuthenticatedUserRequest } from './types';
import { AuthTokenService } from '../services/authtoken.service';
import { inject, injectable } from 'inversify';
import { UserActivities } from '../../modules/auth/schema/userActivities.schema';
import { userActivitiesType } from '../utils/userActivities.utils';
import { TokenScopes } from '../enums/token-scopes.enum';
import { OAuthTokenService } from '../../modules/oauth_provider/services/oauth_token.service';
import { OAuthTokenPayload } from '../../modules/oauth_provider/types/oauth.types';
import { OAuthScopes } from '../../modules/oauth_provider/config/scopes.config';
import { Users } from '../../modules/user_management/schema/users.schema';
import { Org } from '../../modules/user_management/schema/org.schema';
import { OAuthApp } from '../../modules/oauth_provider/schema/oauth.app.schema';

const { LOGOUT, PASSWORD_CHANGED } = userActivitiesType;
// Delay in milliseconds between password change activity and token generation
const PASSWORD_CHANGE_TOKEN_DELAY_MS = 1000;

@injectable()
export class AuthMiddleware {
  // Static OAuth service shared across all AuthMiddleware instances
  private static oauthTokenService: OAuthTokenService | null = null;

  constructor(
    @inject('Logger') private logger: Logger,
    @inject('AuthTokenService') private tokenService: AuthTokenService,
  ) {
    this.authenticate = this.authenticate.bind(this);
  }

  /**
   * Initialize OAuth services for all AuthMiddleware instances.
   * Called once from app.ts after the OAuth provider container is initialized.
   */
  static setOAuthServices(oauthTokenService: OAuthTokenService): void {
    AuthMiddleware.oauthTokenService = oauthTokenService;
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

      // Peek at the token payload to determine token type
      const rawDecoded = jwt.decode(token) as Record<string, any> | null;

      if (this.isOAuthToken(rawDecoded) && AuthMiddleware.oauthTokenService) {
        // OAuth token path
        await this.authenticateOAuthToken(token, req);
      } else {
        // Regular JWT path (existing behavior)
        await this.authenticateRegularToken(token, req);
      }

      next();
    } catch (error) {
      next(error);
    }
  }

  /**
   * Check if a decoded token payload is an OAuth access token.
   */
  private isOAuthToken(decoded: Record<string, any> | null): decoded is OAuthTokenPayload {
    return (
      decoded !== null &&
      decoded.token_type === 'access' &&
      typeof decoded.client_id === 'string'
    );
  }

  /**
   * Authenticate using a regular JWT token (existing behavior).
   */
  private async authenticateRegularToken(
    token: string,
    req: AuthenticatedUserRequest,
  ): Promise<void> {
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
  }

  /**
   * Authenticate using an OAuth access token.
   * Verifies the token, enforces scopes, and populates req.user.
   */
  private async authenticateOAuthToken(
    token: string,
    req: AuthenticatedUserRequest,
  ): Promise<void> {
    const oauthTokenService = AuthMiddleware.oauthTokenService!;

    // Verify the OAuth token (checks signature, expiry, revocation status)
    const payload = await oauthTokenService.verifyAccessToken(token);

    // Enforce scope-based access control
    const tokenScopes = payload.scope.split(' ');
    const requiredScopes = this.getRequiredScopesForEndpoint(
      req.method,
      req.originalUrl,
    );

    if (requiredScopes.length === 0) {
      // No OAuth scope covers this endpoint - deny access
      throw new ForbiddenError(
        'This endpoint is not accessible with OAuth tokens',
      );
    }

    // Check if the token has at least one of the required scopes
    const hasScope = requiredScopes.some((scope) => tokenScopes.includes(scope));
    if (!hasScope) {
      throw new ForbiddenError(
        `Insufficient scope. Required: ${requiredScopes.join(' or ')}`,
      );
    }

    // Build req.user in the format controllers expect
    let userId = payload.sub;
    const orgId = payload.org_id;

    let email: string | undefined;
    let fullName: string | undefined;
    let accountType: string | undefined;

    // For client_credentials tokens (sub === client_id), resolve the app owner
    const isClientCredentials = userId === payload.client_id;
    if (isClientCredentials) {
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
        }
      } catch (err) {
        this.logger.error('Failed to look up OAuth app owner', err);
      }
    }

    // Look up user details
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
          fullName = user.fullName;
        }
      } catch (err) {
        this.logger.error('Failed to look up OAuth user details', err);
      }

      try {
        const org = await Org.findOne({
          _id: orgId,
          isDeleted: false,
        })
          .select('accountType')
          .lean()
          .exec();
        if (org) {
          accountType = (org as any).accountType;
        }
      } catch (err) {
        this.logger.error('Failed to look up org for OAuth token', err);
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

    // Replace the OAuth token in the Authorization header with a regular JWT
    // so downstream Python services (which only understand regular/scoped JWTs) can validate it
    const downstreamToken = this.tokenService.generateToken({
      userId,
      orgId,
      email,
      fullName,
      accountType,
    });
    req.headers.authorization = `Bearer ${downstreamToken}`;

    this.logger.debug('OAuth user authenticated', {
      userId,
      orgId,
      clientId: payload.client_id,
      scopes: tokenScopes,
    });
  }

  /**
   * Determine which OAuth scopes are required for the given HTTP method and path.
   * Returns an array of scope names that cover this endpoint.
   */
  private getRequiredScopesForEndpoint(method: string, path: string): string[] {
    const requiredScopes: string[] = [];
    const upperMethod = method.toUpperCase();

    for (const [scopeName, scopeDef] of Object.entries(OAuthScopes)) {
      for (const endpointPattern of scopeDef.endpoints) {
        if (this.matchEndpointPattern(upperMethod, path, endpointPattern)) {
          requiredScopes.push(scopeName);
          break; // One match per scope is enough
        }
      }
    }

    return requiredScopes;
  }

  /**
   * Match an HTTP method+path against a scope endpoint pattern.
   * Patterns: "GET /api/v1/users", "GET /api/v1/users/:id", "GET /api/v1/knowledgeBase/*"
   */
  private matchEndpointPattern(method: string, requestPath: string, pattern: string): boolean {
    const spaceIndex = pattern.indexOf(' ');
    if (spaceIndex === -1) return false;

    const patternMethod = pattern.substring(0, spaceIndex).toUpperCase();
    const patternPath = pattern.substring(spaceIndex + 1);

    if (patternMethod !== method) return false;

    // Normalize: remove trailing slash and query string
    const normalizedPath = (requestPath.split('?')[0] || requestPath).replace(/\/+$/, '');
    const normalizedPattern = patternPath.replace(/\/+$/, '');

    const pathSegments = normalizedPath.split('/').filter(Boolean);
    const patternSegments = normalizedPattern.split('/').filter(Boolean);

    for (let i = 0; i < patternSegments.length; i++) {
      const pSeg = patternSegments[i]!;

      // Wildcard: matches everything from here onwards
      if (pSeg === '*') {
        return true;
      }

      // Path has fewer segments than pattern
      if (i >= pathSegments.length) {
        return false;
      }

      // Parameter placeholder: matches any single segment
      if (pSeg.startsWith(':')) {
        continue;
      }

      // Exact segment match
      if (pSeg !== pathSegments[i]) {
        return false;
      }
    }

    // All pattern segments matched; path should not have extra segments
    // unless the pattern ended with wildcard (already handled above)
    return pathSegments.length === patternSegments.length;
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

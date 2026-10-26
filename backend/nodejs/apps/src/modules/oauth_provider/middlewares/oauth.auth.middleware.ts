import { Request, Response, NextFunction } from 'express';
import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { OAuthTokenService } from '../services/oauth_token.service';
import {
  ScopeValidatorService,
  parseScopeString,
} from '../services/scope.validator.service';
import { OAuthTokenPayload } from '../types/oauth.types';
import {
  InvalidTokenError,
  ExpiredTokenError,
} from '../../../libs/errors/oauth.errors';
import { UnauthorizedError } from '../../../libs/errors/http.errors';
import { Users } from '../../user_management/schema/users.schema';
import { assertDelegationStillHolds } from '../services/delegation-guard';

/**
 * A delegated token has to satisfy the rules that issued it on every request,
 * including here.
 *
 * AuthMiddleware re-checks them for `/api` and `/mcp`, and the Python
 * services follow because they ask `GET /api/v1/users/me/role`. The OIDC
 * endpoints go through this middleware instead, which verifies the signature
 * and that the stored row is unrevoked and nothing else — so without this,
 * `/oauth2/userinfo` would keep returning the person's name and email for the
 * rest of the five minutes after their allow-list row was deleted, after they
 * were promoted, or after the service account behind the token was switched
 * off. Refused as an invalid token so the answer keeps its RFC 6750 shape and
 * says why.
 */
async function assertDelegatedTokenStillHolds(
  payload: OAuthTokenPayload,
): Promise<void> {
  const actorId = payload.act?.sub;
  if (actorId === undefined) return;

  const subject = await Users.findOne({
    _id: { $eq: payload.userId },
    orgId: { $eq: payload.orgId },
    isDeleted: false,
  })
    .select('role kind isDisabled')
    .lean()
    .exec();

  if (!subject) {
    throw new InvalidTokenError(
      'This token acts for someone who no longer exists',
    );
  }

  try {
    await assertDelegationStillHolds(
      actorId,
      payload.userId,
      payload.orgId,
      subject,
    );
  } catch (error) {
    // Only the guard's own refusals become `invalid_token` with their reason.
    // Anything else — a failed read, a dropped connection — is rethrown so it
    // lands in the generic branch of `authenticate`, which logs it and answers
    // `Token validation failed`. Dressing a driver error up as an invalid
    // token would hand its text to the caller and skip the log, leaving an
    // outage looking like a withdrawn permission.
    if (error instanceof UnauthorizedError) {
      throw new InvalidTokenError(error.message);
    }
    throw error;
  }
}

/**
 * Build RFC 6750 compliant WWW-Authenticate header
 * @see https://datatracker.ietf.org/doc/html/rfc6750#section-3
 */
function buildWwwAuthenticateHeader(
  error?: string,
  errorDescription?: string,
  scope?: string,
  realm: string = 'oauth',
): string {
  const parts = [`Bearer realm="${realm}"`];

  if (error) {
    parts.push(`error="${error}"`);
  }
  if (errorDescription) {
    // Escape backslashes first, then quotes to prevent escape sequence injection
    const safeDescription = errorDescription
      .replace(/\\/g, '\\\\')
      .replace(/"/g, '\\"');
    parts.push(`error_description="${safeDescription}"`);
  }
  if (scope) {
    parts.push(`scope="${scope}"`);
  }

  return parts.join(', ');
}

export interface OAuthRequest extends Request {
  oauth?: {
    clientId: string;
    userId?: string;
    orgId: string;
    scopes: string[];
    payload: OAuthTokenPayload;
  };
}

@injectable()
export class OAuthAuthMiddleware {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
  ) {}

  /**
   * Middleware to authenticate OAuth bearer tokens
   * @see RFC 6750 - The OAuth 2.0 Authorization Framework: Bearer Token Usage
   */
  authenticate = async (
    req: OAuthRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const authHeader = req.headers.authorization;

      if (!authHeader?.startsWith('Bearer ')) {
        // RFC 6750 Section 3.1: If request lacks credentials, return 401 with WWW-Authenticate
        res.setHeader(
          'WWW-Authenticate',
          buildWwwAuthenticateHeader(
            'invalid_request',
            'Bearer token required',
          ),
        );
        res.status(401).json({
          error: 'invalid_request',
          error_description: 'Bearer token required',
        });
        return;
      }

      const token = authHeader.substring(7);
      const payload = await this.oauthTokenService.verifyAccessToken(token);
      const scopes = parseScopeString(payload.scope);
      if (scopes.length === 0) {
        throw new InvalidTokenError('Token carries no scopes');
      }

      await assertDelegatedTokenStillHolds(payload);

      // Attach OAuth info to request
      req.oauth = {
        clientId: payload.client_id,
        userId: payload.userId,
        orgId: payload.orgId,
        scopes,
        payload,
      };

      next();
    } catch (error) {
      // RFC 6750 Section 3.1: Always include WWW-Authenticate header on 401
      if (error instanceof ExpiredTokenError) {
        res.setHeader(
          'WWW-Authenticate',
          buildWwwAuthenticateHeader(
            'invalid_token',
            'The access token expired',
          ),
        );
        res.status(401).json({
          error: 'invalid_token',
          error_description: 'The access token expired',
        });
        return;
      }

      if (error instanceof InvalidTokenError) {
        res.setHeader(
          'WWW-Authenticate',
          buildWwwAuthenticateHeader('invalid_token', error.message),
        );
        res.status(401).json({
          error: 'invalid_token',
          error_description: error.message,
        });
        return;
      }

      this.logger.error('OAuth authentication error', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });

      res.setHeader(
        'WWW-Authenticate',
        buildWwwAuthenticateHeader('invalid_token', 'Token validation failed'),
      );
      res.status(401).json({
        error: 'invalid_token',
        error_description: 'Token validation failed',
      });
    }
  };

  /**
   * Middleware factory to check for required scopes
   * @see RFC 6750 Section 3.1 - insufficient_scope error
   */
  requireScopes = (...requiredScopes: string[]) => {
    return async (
      req: OAuthRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        if (!req.oauth) {
          res.setHeader(
            'WWW-Authenticate',
            buildWwwAuthenticateHeader('invalid_token', 'Not authenticated'),
          );
          res.status(401).json({
            error: 'invalid_token',
            error_description: 'Not authenticated',
          });
          return;
        }

        const hasAllScopes = this.scopeValidatorService.hasAllScopes(
          req.oauth.scopes,
          requiredScopes,
        );

        if (!hasAllScopes) {
          // RFC 6750 Section 3.1: 403 Forbidden with insufficient_scope error
          const scopeStr = requiredScopes.join(' ');
          res.setHeader(
            'WWW-Authenticate',
            buildWwwAuthenticateHeader(
              'insufficient_scope',
              `Required scopes: ${scopeStr}`,
              scopeStr,
            ),
          );
          res.status(403).json({
            error: 'insufficient_scope',
            error_description: `Required scopes: ${scopeStr}`,
            scope: scopeStr,
          });
          return;
        }

        next();
      } catch (error) {
        this.logger.error('Scope check error', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });

        res.status(500).json({
          error: 'server_error',
          error_description: 'Scope validation failed',
        });
      }
    };
  };

  /**
   * Middleware factory to check for any of the required scopes
   * @see RFC 6750 Section 3.1 - insufficient_scope error
   */
  requireAnyScope = (...requiredScopes: string[]) => {
    return async (
      req: OAuthRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        if (!req.oauth) {
          res.setHeader(
            'WWW-Authenticate',
            buildWwwAuthenticateHeader('invalid_token', 'Not authenticated'),
          );
          res.status(401).json({
            error: 'invalid_token',
            error_description: 'Not authenticated',
          });
          return;
        }

        const hasAnyScope = this.scopeValidatorService.hasAnyScope(
          req.oauth.scopes,
          requiredScopes,
        );

        if (!hasAnyScope) {
          // RFC 6750 Section 3.1: 403 Forbidden with insufficient_scope error
          const scopeStr = requiredScopes.join(' ');
          res.setHeader(
            'WWW-Authenticate',
            buildWwwAuthenticateHeader(
              'insufficient_scope',
              `One of these scopes required: ${scopeStr}`,
              scopeStr,
            ),
          );
          res.status(403).json({
            error: 'insufficient_scope',
            error_description: `One of these scopes required: ${scopeStr}`,
            scope: scopeStr,
          });
          return;
        }

        next();
      } catch (error) {
        this.logger.error('Scope check error', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });

        res.status(500).json({
          error: 'server_error',
          error_description: 'Scope validation failed',
        });
      }
    };
  };
}

// Export the helper for use in controller
export { buildWwwAuthenticateHeader };

/**
 * Create OAuth middleware factory function for use without DI
 */
export function createOAuthMiddleware(
  oauthTokenService: OAuthTokenService,
  scopeValidatorService: ScopeValidatorService,
  logger: Logger,
): OAuthAuthMiddleware {
  const middleware = new OAuthAuthMiddleware(
    logger,
    oauthTokenService,
    scopeValidatorService,
  );
  return middleware;
}

/**
 * Standalone scope check function for use in routes
 */
export function scopeCheck(
  requiredScopes: string[],
  oauthTokenService: OAuthTokenService,
  scopeValidatorService: ScopeValidatorService,
) {
  return async (
    req: OAuthRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      // First try OAuth token
      const authHeader = req.headers.authorization;
      if (authHeader?.startsWith('Bearer ')) {
        const token = authHeader.substring(7);

        try {
          const payload = await oauthTokenService.verifyAccessToken(token);
          const tokenScopes = parseScopeString(payload.scope);
          if (tokenScopes.length === 0) {
            res.status(401).json({
              error: 'invalid_token',
              error_description: 'Token carries no scopes',
            });
            return;
          }

          const hasAllScopes = scopeValidatorService.hasAllScopes(
            tokenScopes,
            requiredScopes,
          );

          if (!hasAllScopes) {
            res.status(403).json({
              error: 'insufficient_scope',
              error_description: `Required scopes: ${requiredScopes.join(' ')}`,
            });
            return;
          }

          try {
            await assertDelegatedTokenStillHolds(payload);
          } catch (error) {
            // Answered here rather than thrown: the catch below falls through
            // to the next middleware, and a token whose delegation has been
            // withdrawn must be refused rather than handed on to be
            // authenticated some other way.
            //
            // A failed read is refused the same way, because falling through
            // would be worse, but it does not borrow the refusal's wording:
            // only the guard's own messages are repeated back.
            res.status(401).json({
              error: 'invalid_token',
              error_description:
                error instanceof UnauthorizedError ||
                error instanceof InvalidTokenError
                  ? error.message
                  : 'Token validation failed',
            });
            return;
          }

          // Attach OAuth info
          req.oauth = {
            clientId: payload.client_id,
            userId: payload.userId,
            orgId: payload.orgId,
            scopes: tokenScopes,
            payload,
          };

          next();
          return;
        } catch (error) {
          // verifyAccessToken throws this before returning a payload, so the
          // empty-scope check above never runs for the real token service.
          // Other verification failures still fall through.
          if (
            error instanceof InvalidTokenError &&
            error.message === 'Token carries no scopes'
          ) {
            res.status(401).json({
              error: 'invalid_token',
              error_description: error.message,
            });
            return;
          }
        }
      }

      // If no OAuth token or invalid, let next middleware handle
      next();
    } catch (error) {
      next(error);
    }
  };
}

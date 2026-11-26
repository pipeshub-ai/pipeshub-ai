import { Response, NextFunction } from 'express';
import { ForbiddenError } from '../errors/http.errors';
import { AuthenticatedUserRequest } from './types';
import { OAuthScopeNames } from '../enums/oauth-scopes.enum';
import { clearScopeDenied, markScopeDenied } from './agent-call-recorder';
import {
  observeTokenRestriction,
  tokenRestrictionsEnforced,
} from '../utils/token-restrictions.utils';

/**
 * Middleware factory that enforces OAuth scope validation on a per-route basis.
 *
 * Must be placed AFTER authMiddleware.authenticate in the middleware chain.
 *
 * - For non-OAuth tokens (regular JWTs): passes through (no-op)
 * - For OAuth tokens: checks if token has at least ONE of the required scopes (OR logic)
 * - Throws ForbiddenError if insufficient scopes
 *
 * @example
 * router.get(
 *   '/',
 *   authMiddleware.authenticate,
 *   requireScopes(OAuthScopeNames.USER_READ),
 *   handler,
 * );
 */
export function requireScopes(...requiredScopes: OAuthScopeNames[]) {
  return (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ) => {
    try {
      const user = req.user;

      if (!user) {
        throw new ForbiddenError('Authentication required');
      }

      // Only enforce scopes for OAuth tokens; regular JWTs pass through
      if (!user.isOAuth) {
        return next();
      }

      const tokenScopes: string[] = user.oauthScopes || [];
      const hasScope = requiredScopes.some((scope) =>
        tokenScopes.includes(scope),
      );

      if (!hasScope) {
        markScopeDenied(req);
        throw new ForbiddenError(
          `Insufficient scope. Required: ${requiredScopes.join(' or ')}`,
        );
      }

      next();
    } catch (error) {
      next(error);
    }
  };
}

/**
 * `requireScopes` for a route tokens could already reach without one: refuses only when
 * TOKEN_RESTRICTIONS_MODE is `enforce`; otherwise the request proceeds and the gap is recorded.
 */
export function requireScopesWhenEnforced(
  ...requiredScopes: OAuthScopeNames[]
): (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => void {
  const check = requireScopes(...requiredScopes);
  return (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    if (tokenRestrictionsEnforced()) {
      check(req, res, next);
      return;
    }
    check(req, res, (err?: unknown) => {
      if (err) {
        clearScopeDenied(req);
        observeTokenRestriction(req, 'missing_scope');
      }
      next();
    });
  };
}

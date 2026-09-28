import { Response, NextFunction } from 'express';
import { ForbiddenError } from '../errors/http.errors';
import { AuthenticatedUserRequest } from './types';
import { OAuthScopeNames } from '../enums/oauth-scopes.enum';

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
 * The scopes of the token behind this request, or undefined for a session.
 *
 * Anything that mints or re-arms a credential for an OAuth/PAT caller must keep
 * the new credential within these, or a narrow token can mint itself a broader
 * one. Sessions are unbounded here for the same reason requireScopes lets them
 * through: they are the signed-in person, not a delegated grant.
 */
export function getCallerTokenScopes(
  user: Record<string, any> | undefined,
): string[] | undefined {
  if (!user?.isOAuth) {
    return undefined;
  }
  return Array.isArray(user.oauthScopes) ? user.oauthScopes : [];
}

import { Response, NextFunction } from 'express';
import { ForbiddenError } from '../errors/http.errors';
import { AuthenticatedUserRequest } from './types';
import { OAuthScopeNames } from '../enums/oauth-scopes.enum';
import { markScopeDenied } from './agent-call-recorder';

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
export function requireScopes(
  ...requiredScopes: OAuthScopeNames[]
): ((
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => void) & { requiredScopes: readonly string[] } {
  const check = (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): void => {
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
  // Read by the route scan, which checks what each route asks of a token.
  return Object.assign(check, {
    requiredScopes: [...requiredScopes] as readonly string[],
  });
}

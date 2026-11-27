import { Response, NextFunction } from 'express';
import { ForbiddenError, UnauthorizedError } from '../errors/http.errors';
import { AuthenticatedUserRequest } from './types';
import {
  observeTokenRestriction,
  tokenRestrictionsEnforced,
} from '../utils/token-restrictions.utils';

/**
 * Rejects callers authenticated with an OAuth access token or a personal
 * access token (both set `req.user.isOAuth`). Must run after
 * `authMiddleware.authenticate`.
 *
 * Use on endpoints that represent the user acting in person, such as OAuth
 * client management and the consent step that issues authorization codes.
 * If those accepted a bearer token a client already holds, that client could
 * register itself for wider scopes and self-consent, turning any issued
 * token into org-wide access without the user.
 */
export function requireSessionAuth(
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): void {
  if (!req.user) {
    next(new UnauthorizedError('Authentication required'));
    return;
  }
  if (req.user.isOAuth === true) {
    next(
      new ForbiddenError(
        'This endpoint requires an interactive user session; OAuth access tokens and personal access tokens are not accepted',
      ),
    );
    return;
  }
  next();
}

/**
 * `requireSessionAuth` for a route tokens could already reach: refuses only when
 * TOKEN_RESTRICTIONS_MODE is `enforce`; otherwise the request proceeds and the call is recorded.
 */
export function requireSessionAuthWhenEnforced(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): void {
  if (tokenRestrictionsEnforced()) {
    requireSessionAuth(req, res, next);
    return;
  }
  if (req.user?.isOAuth === true)
    observeTokenRestriction(req, 'session_only_route');
  next();
}

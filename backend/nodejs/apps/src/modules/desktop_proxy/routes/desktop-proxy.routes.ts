import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import {
  fetchLocalFsContent,
  pullLocalFsFileEvents,
} from '../controllers/desktop-proxy.controller';
import {
  LocalFsFetchContentSchema,
  LocalFsPullEventsSchema,
} from '../types/local-fs-pull.types';

/**
 * Service-to-service routes the connector service calls to reach a user's
 * desktop. Mounted under `/internal/` so the global rate limiter skips them —
 * a full sync issues one request per page and would otherwise be throttled.
 */
export function createDesktopProxyRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.post(
    '/internal/local-fs/file-events/pull',
    authMiddleware.scopedTokenValidator(TokenScopes.DESKTOP_COMMAND),
    ValidationMiddleware.validate(LocalFsPullEventsSchema),
    pullLocalFsFileEvents(container),
  );

  router.post(
    '/internal/local-fs/content',
    authMiddleware.scopedTokenValidator(TokenScopes.DESKTOP_COMMAND),
    ValidationMiddleware.validate(LocalFsFetchContentSchema),
    fetchLocalFsContent(container),
  );

  return router;
}

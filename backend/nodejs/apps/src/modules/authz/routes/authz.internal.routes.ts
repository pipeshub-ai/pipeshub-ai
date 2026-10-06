import { Router } from 'express';
import { Container } from 'inversify';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { COLLAB_TYPES } from '../../enterprise_search/services/collaboration/collab.types';
import { ChatContentCheckService } from '../chat-content-check.service';
import { checkChatContent } from '../controller/authz.internal.controller';
import { chatContentCheckSchema } from '../validators/authz.internal.validators';

/** Service-to-service routes of the authz module, mounted at `/api/v1/authz` beside the user-facing router. */
export function createAuthzInternalRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const service = container.get<ChatContentCheckService>(
    COLLAB_TYPES.ChatContentCheckService,
  );

  router.post(
    '/internal/check',
    authMiddleware.scopedTokenValidator(TokenScopes.AUTHZ_CHECK),
    ValidationMiddleware.validate(chatContentCheckSchema),
    checkChatContent(service),
  );

  return router;
}

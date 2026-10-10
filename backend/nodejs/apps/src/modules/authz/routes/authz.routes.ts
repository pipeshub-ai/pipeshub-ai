import {
  NextFunction,
  Request,
  RequestHandler,
  Response,
  Router,
} from 'express';
import { Container } from 'inversify';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { createKeyedRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { requireFlag } from '../../../libs/middlewares/require-flag.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { Logger } from '../../../libs/services/logger.service';
import { COLLAB_FLAG_KEYS } from '../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../configuration_manager/services/platform-feature-flags.service';
import { COLLAB_TYPES } from '../../enterprise_search/services/collaboration/collab.types';
import { ConversationGuards } from '../../enterprise_search/services/collaboration/http/conversation-guards';
import { AuthzController } from '../authz.controller';
import {
  explainSchema,
  previewSchema,
  refId,
} from '../validators/authz.validators';

export const AUTHZ_EXPLAIN_PER_MINUTE = 60;

/** The guard reads the chat id from `:conversationId`; here it comes from the body's `resource`. */
const bindResourceParam: RequestHandler = (
  req: Request,
  _res: Response,
  next: NextFunction,
): void => {
  req.params.conversationId = refId(
    (req.body as { resource: string }).resource,
  );
  next();
};

/** User-facing authorization routes, mounted at `/api/v1/authz`; every one answers 404 while the flag is off. */
export function createAuthzRouter(container: Container): Router {
  const router = Router();
  const flags = container.get<IFeatureFlags>(COLLAB_TYPES.FeatureFlags);
  const guards = container.get<ConversationGuards>(
    COLLAB_TYPES.ConversationGuards,
  );
  const controller = container.get<AuthzController>(
    COLLAB_TYPES.AuthzController,
  );
  const auth = container.get<AuthMiddleware>('AuthMiddleware');
  const limit = createKeyedRateLimiter(
    Logger.getInstance({ service: 'AuthzRoutes' }),
    {
      prefix: 'authz:explain',
      maxRequestsPerMinute: AUTHZ_EXPLAIN_PER_MINUTE,
      message: 'Too many requests. Please try again shortly.',
      code: 'RATE_LIMITED',
      shared: true,
    },
  );
  const gate: RequestHandler[] = [
    requireFlag(flags, COLLAB_FLAG_KEYS.collaborativeChats),
    // eslint-disable-next-line @typescript-eslint/unbound-method -- bound in the AuthMiddleware constructor
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    limit,
  ];

  router.get(
    '/explain',
    ...gate,
    ValidationMiddleware.validate(explainSchema),
    controller.explain,
  );
  router.post(
    '/explain/preview',
    ...gate,
    ValidationMiddleware.validate(previewSchema),
    bindResourceParam,
    guards.authorize('linkProject', 'chat'),
    controller.preview,
  );
  return router;
}

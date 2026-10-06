import { RequestHandler, Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { createKeyedRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { requireFlag } from '../../../libs/middlewares/require-flag.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { Logger } from '../../../libs/services/logger.service';
import { COLLAB_FLAG_KEYS } from '../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../configuration_manager/services/platform-feature-flags.service';
import { CollaboratorsController } from '../controller/collaborators.controller';
import { COLLAB_TYPES } from '../services/collaboration/collab.types';
import {
  ConversationGuards,
  GuardKind,
} from '../services/collaboration/http/conversation-guards';
import { collaborationSchemas } from '../validators/collaboration.validators';

export const COLLAB_MUTATE_PER_MINUTE = 20;
/**
 * Feed polls per user per minute. A focused window polls about 15/min (4 s, up to 18.75 with jitter) and
 * every other visible window at most 5/min (15 s idle cadence, up to 5 with jitter), so 120 covers
 * two focused devices plus 15 background windows with room for catch-up fetches. A 304 is one `_id` read.
 */
export const COLLAB_FEED_PER_MINUTE = 120;
const RATE_LIMITED = 'RATE_LIMITED';

interface CollaborationLimiters {
  mutate: RequestHandler;
  feed: RequestHandler;
}

const limiters = new WeakMap<Container, CollaborationLimiters>();

/**
 * One pair per container, so the chat and agent routers share a user's budget. The counters are
 * shared across replicas through the cache service (in-process when it is unavailable).
 */
export function collaborationLimiters(
  container: Container,
): CollaborationLimiters {
  let pair = limiters.get(container);
  if (!pair) {
    const logger = Logger.getInstance({ service: 'CollaborationRoutes' });
    pair = {
      mutate: createKeyedRateLimiter(logger, {
        prefix: 'collab:mutate',
        maxRequestsPerMinute: COLLAB_MUTATE_PER_MINUTE,
        message: 'Too many sharing changes. Please try again shortly.',
        code: RATE_LIMITED,
        shared: true,
      }),
      feed: createKeyedRateLimiter(logger, {
        prefix: 'collab:feed',
        maxRequestsPerMinute: COLLAB_FEED_PER_MINUTE,
        message: 'Too many requests. Please try again shortly.',
        code: RATE_LIMITED,
        shared: true,
      }),
    };
    limiters.set(container, pair);
  }
  return pair;
}

/**
 * Legacy `/share` and `/unshare` honour `accessLevel` with the flag on, so they then need the same
 * `conversation:share` scope as the collaborators routes (F-15); flag off keeps PH-01's `conversation:write`.
 */
export function legacyShareScope(container: Container): RequestHandler {
  const flags = container.get<IFeatureFlags>(COLLAB_TYPES.FeatureFlags);
  const share = requireScopes(OAuthScopeNames.CONVERSATION_SHARE);
  return (req, res, next): void => {
    flags
      .isEnabled(COLLAB_FLAG_KEYS.collaborativeChats)
      .then((on) => (on ? share(req, res, next) : next()), next);
  };
}

/**
 * Mounts the collaboration routes on the chat router (`/:conversationId/...`) or the agent router
 * (`/:agentKey/conversations/:conversationId/...`). Every route answers 404 while the flag is off.
 */
export function mountCollaborationRoutes(
  router: Router,
  container: Container,
  kind: GuardKind,
): void {
  const guards = container.get<ConversationGuards>(
    COLLAB_TYPES.ConversationGuards,
  );
  const flags = container.get<IFeatureFlags>(COLLAB_TYPES.FeatureFlags);
  const controller = container.get<CollaboratorsController>(
    COLLAB_TYPES.CollaboratorsController,
  );
  const auth = container.get<AuthMiddleware>('AuthMiddleware');
  const { mutate, feed } = collaborationLimiters(container);
  const schemas = collaborationSchemas[kind];
  const base =
    kind === 'chat'
      ? '/:conversationId'
      : '/:agentKey/conversations/:conversationId';

  const gate = (...scopes: OAuthScopeNames[]): RequestHandler[] => [
    requireFlag(flags, COLLAB_FLAG_KEYS.collaborativeChats),
    // eslint-disable-next-line @typescript-eslint/unbound-method -- bound in the AuthMiddleware constructor
    auth.authenticate,
    requireScopes(...scopes),
  ];
  const { CONVERSATION_READ, CONVERSATION_WRITE, CONVERSATION_SHARE } =
    OAuthScopeNames;

  router.get(
    `${base}/collaborators`,
    ...gate(CONVERSATION_READ),
    ValidationMiddleware.validate(schemas.list),
    guards.authorize('read', kind),
    controller.list,
  );
  router.put(
    `${base}/collaborators`,
    ...gate(CONVERSATION_SHARE),
    mutate,
    ValidationMiddleware.validate(schemas.put),
    guards.authorize('invite', kind),
    controller.upsert,
  );
  router.delete(
    `${base}/collaborators/:principalId`,
    ...gate(CONVERSATION_SHARE),
    mutate,
    ValidationMiddleware.validate(schemas.remove),
    guards.authorize('manageCollaborators', kind),
    controller.remove,
  );
  router.patch(
    `${base}/collaboration-settings`,
    ...gate(CONVERSATION_SHARE),
    mutate,
    ValidationMiddleware.validate(schemas.settings),
    guards.authorize('settings', kind),
    controller.updateSettings,
  );
  router.post(
    `${base}/transfer-ownership`,
    ...gate(CONVERSATION_SHARE),
    mutate,
    ValidationMiddleware.validate(schemas.transfer),
    guards.authorize('transfer', kind),
    controller.transferOwnership,
  );
  router.post(
    `${base}/leave`,
    ...gate(CONVERSATION_WRITE),
    mutate,
    ValidationMiddleware.validate(schemas.leave),
    guards.authorize('leave', kind),
    controller.leave,
  );
  router.get(
    `${base}/feed`,
    ...gate(CONVERSATION_READ),
    feed,
    ValidationMiddleware.validate(schemas.feed),
    guards.authorize('read', kind),
    controller.getFeed,
  );
  router.get(
    `${base}/readiness`,
    ...gate(CONVERSATION_READ),
    feed,
    ValidationMiddleware.validate(schemas.readiness),
    guards.authorize('read', kind),
    controller.getReadiness,
  );
}

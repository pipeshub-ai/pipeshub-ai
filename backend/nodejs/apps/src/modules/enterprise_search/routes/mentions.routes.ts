import { RequestHandler, Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireFlag } from '../../../libs/middlewares/require-flag.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { COLLAB_FLAG_KEYS } from '../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../configuration_manager/services/platform-feature-flags.service';
import { MentionsController } from '../controller/mentions.controller';
import { collaborationLimiters } from './collaboration.routes';
import { COLLAB_TYPES } from '../services/collaboration/collab.types';
import {
  ConversationGuards,
  GuardKind,
} from '../services/collaboration/http/conversation-guards';
import { orgMentionableSchemas, mentionSchemas } from '../validators/mention.validators';

/**
 * `GET /mentionables` (chat) or `GET /:agentKey/conversations/mentionables` (agent): the picker
 * before the chat exists. Mounted first on its router so `/:conversationId` does not claim it.
 */
export function mountNewChatMentionables(
  router: Router,
  container: Container,
  kind: GuardKind,
): void {
  const flags = container.get<IFeatureFlags>(COLLAB_TYPES.FeatureFlags);
  const controller = container.get<MentionsController>(
    COLLAB_TYPES.MentionsController,
  );
  const auth = container.get<AuthMiddleware>('AuthMiddleware');
  const { feed } = collaborationLimiters(container);
  router.get(
    kind === 'chat' ? '/mentionables' : '/:agentKey/conversations/mentionables',
    requireFlag(flags, COLLAB_FLAG_KEYS.collaborativeChats),
    requireFlag(flags, COLLAB_FLAG_KEYS.chatMentions),
    // eslint-disable-next-line @typescript-eslint/unbound-method -- bound in the AuthMiddleware constructor
    auth.authenticate,
    requireScopes(
      kind === 'chat'
        ? OAuthScopeNames.CONVERSATION_READ
        : OAuthScopeNames.AGENT_EXECUTE,
    ),
    feed,
    ValidationMiddleware.validate(orgMentionableSchemas[kind]),
    controller.listForNewChat,
  );
}

/**
 * Mounts the mentionables and notes routes on the chat router or the agent router. Both answer 404
 * while collaborative chats or mentions is off. The notes route has no `runLease()`: a note takes no lease.
 */
export function mountMentionRoutes(
  router: Router,
  container: Container,
  kind: GuardKind,
): void {
  const guards = container.get<ConversationGuards>(
    COLLAB_TYPES.ConversationGuards,
  );
  const flags = container.get<IFeatureFlags>(COLLAB_TYPES.FeatureFlags);
  const controller = container.get<MentionsController>(
    COLLAB_TYPES.MentionsController,
  );
  const auth = container.get<AuthMiddleware>('AuthMiddleware');
  const schemas = mentionSchemas[kind];
  const base =
    kind === 'chat'
      ? '/:conversationId'
      : '/:agentKey/conversations/:conversationId';
  const gate = (...scopes: OAuthScopeNames[]): RequestHandler[] => [
    requireFlag(flags, COLLAB_FLAG_KEYS.collaborativeChats),
    requireFlag(flags, COLLAB_FLAG_KEYS.chatMentions),
    // eslint-disable-next-line @typescript-eslint/unbound-method -- bound in the AuthMiddleware constructor
    auth.authenticate,
    requireScopes(...scopes),
  ];

  router.get(
    `${base}/mentionables`,
    ...gate(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(schemas.mentionables),
    guards.authorize('read', kind),
    controller.list,
  );
  router.post(
    `${base}/notes`,
    ...gate(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(schemas.notes),
    guards.authorize('send', kind),
    controller.postNote(kind),
  );
}

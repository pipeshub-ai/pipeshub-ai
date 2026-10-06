import { NextFunction, Router, Response } from 'express';
import { Container } from 'inversify';
import multer from 'multer';
import { createMulter } from '../../../libs/utils/multer.utils';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  archiveConversation,
  archiveSearch,
  deleteConversationById,
  deleteSearchById,
  deleteSearchHistory,
  getAllConversations,
  getConversationById,
  getSearchById,
  listAllArchivesConversation,
  regenerateAnswers,
  search,
  searchHistory,
  shareConversationById,
  shareSearch,
  unarchiveConversation,
  unarchiveSearch,
  unshareConversationById,
  unshareSearch,
  updateFeedback,
  updateTitle,
  streamChat,
  uploadChatAttachments,
  uploadChatAttachmentsInternal,
  deleteChatAttachment,
  addMessageStream,
  streamAgentConversation,
  streamAgentConversationInternal,
  addMessageStreamToAgentConversation,
  addMessageStreamToAgentConversationInternal,
  getAllAgentConversations,
  getAgentConversationById,
  deleteAgentConversationById,
  createAgent,
  checkAgentHandle,
  getAgent,
  deleteAgent,
  updateAgent,
  listAgents,
  getWebSearchProviderUsage,
  getModelUsage,
  regenerateAgentAnswers,
  cancelConversationStream,
  cancelAgentConversationStream,
  streamChatInternal,
  addMessageStreamInternal,
  updateAgentConversationTitle,
  updateAgentFeedback,
  archiveAgentConversation,
  unarchiveAgentConversation,
  listAllArchivesAgentConversation,
  listAllAgentsArchivedConversationsGrouped,
  searchArchivedConversations,
  setConversationProject,
  setConversationProjectVisibility,
} from '../controller/es_controller';
import {
  addMessage,
  addMessageToAgentConversation,
  createAgentConversation,
  createConversation,
} from '../controller/non-streaming-chat.controller';
import {
  getSpeechCapabilities,
  synthesizeSpeech,
  transcribeAudio,
} from '../controller/speech.controller';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  conversationIdParamsSchema,
  enterpriseSearchCreateSchema,
  enterpriseSearchStreamCreateSchema,
  enterpriseSearchSearchSchema,
  enterpriseSearchSearchHistorySchema,
  searchIdParamsSchema,
  addMessageParamsSchema,
  addMessageStreamParamsSchema,
  conversationShareParamsSchema,
  conversationUnshareParamsSchema,
  conversationTitleParamsSchema,
  conversationProjectLinkSchema,
  conversationProjectVisibilitySchema,
  agentConversationProjectLinkSchema,
  agentConversationProjectVisibilitySchema,
  regenerateAnswersParamsSchema,
  cancelConversationStreamParamsSchema,
  cancelAgentConversationStreamParamsSchema,
  updateFeedbackParamsSchema,
  searchShareParamsSchema,
  regenerateAgentAnswersParamsSchema,
  agentConversationTitleParamsSchema,
  agentConversationParamsSchema,
  deleteAgentConversationParamsSchema,
  updateAgentFeedbackParamsSchema,
  agentStreamCreateSchema,
  agentAddMessageParamsSchema,
  agentInternalStreamCreateSchema,
  agentInternalAddMessageParamsSchema,
  agentCreateConversationSchema,
  agentAddMessageSchema,
  getAllConversationsQuerySchema,
  getAllAgentConversationsQuerySchema,
  listAllArchivesConversationQuerySchema,
  listAllArchivesAgentConversationQuerySchema,
  listAllAgentsArchivedConversationsGroupedQuerySchema,
  searchArchivedConversationsQuerySchema,
  attachmentUploadSchema,
  attachmentRecordIdParamsSchema,
  agentAttachmentUploadSchema,
  agentAttachmentRecordIdParamsSchema,
  createAgentSchema,
  agentHandleAvailabilitySchema,
  updateAgentSchema,
  deleteAgentSchema,
  getAgentParamsSchema,
  getWebSearchProviderUsageRequestSchema,
  getModelUsageRequestSchema,
  listAgentsQuerySchema,
  getAgentConversationByIdSchema,
} from '../validators/es_validators';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { guardPathParams } from '../../../libs/middlewares/safe-path-params.middleware';
import { COLLAB_TYPES } from '../services/collaboration/collab.types';
import { ConversationGuards } from '../services/collaboration/http/conversation-guards';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import { hydrateScopedUser } from '../services/collaboration/http/hydrate-scoped-user.middleware';
import { IConversationCollaborationService } from '../services/collaboration/conversation-collaboration.service';
import {
  collaborationLimiters,
  legacyShareScope,
  mountCollaborationRoutes,
  sharingLimiter,
} from './collaboration.routes';
import { mountMentionRoutes, mountNewChatMentionables } from './mentions.routes';
import { createKeyedRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../libs/services/logger.service';
import { AgentDraftRefResolver } from '../services/collaboration/agent-draft/agent-draft-ref.service';
import { IFeatureFlags } from '../../configuration_manager/services/platform-feature-flags.service';

/** Resolved at router build so a container without the binding fails at boot, not on the first request. */
function requireConversationGuards(container: Container): ConversationGuards {
  return container.get<ConversationGuards>(COLLAB_TYPES.ConversationGuards);
}

/** Resolved at router build for the same reason as the guards. */
function requireTurnDeps(container: Container): ConversationTurnDeps {
  return container.get<ConversationTurnDeps>(COLLAB_TYPES.ConversationTurnDeps);
}
import { fillDefaultChatModel } from '../utils/default-chat-model';

/** Max bytes per file for chat attachment uploads (PDF/JPEG/PNG). Aligned with frontend, Slack, and Python. */
const CHAT_ATTACHMENT_UPLOAD_MAX_BYTES = 5 * 1024 * 1024;

export function createConversationalRouter(container: Container): Router {
  const router = Router();
  guardPathParams(router, 'recordId');
  const guards = requireConversationGuards(container);
  const turnDeps = requireTurnDeps(container);
  const collaboration = container.get<IConversationCollaborationService>(
    COLLAB_TYPES.CollaborationService,
  );
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  mountNewChatMentionables(router, container, 'chat');
  const shareLimit = sharingLimiter(container);
  let appConfig = container.get<AppConfig>('AppConfig');
  const defaultChatModel = fillDefaultChatModel(
    container.isBound('KeyValueStoreService')
      ? container.get<KeyValueStoreService>('KeyValueStoreService')
      : undefined,
  );
  const chatPdfUpload = createMulter({
    storage: multer.memoryStorage(),
    limits: { fileSize: CHAT_ATTACHMENT_UPLOAD_MAX_BYTES, files: 10 },
  });

  const internalAttachmentUpload = createMulter({
    storage: multer.memoryStorage(),
    limits: { fileSize: CHAT_ATTACHMENT_UPLOAD_MAX_BYTES, files: 10 },
  });
  /**
   * @route POST /api/v1/conversations
   * @desc Create a new conversation with initial query
   * @access Private
   * @body {
   *   query: string
   * }
   */
  router.post(
    '/create',
    authMiddleware.authenticate,
    // Same scope as /stream and as the query service's /chat, which answers the first message.
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(enterpriseSearchCreateSchema),
    defaultChatModel,
    shareLimit,
    guards.caller(),
    createConversation(appConfig, turnDeps),
  );

  /**

   * @route POST /api/v1/conversations
   * @desc Create a new conversation with initial query
   * @access Private
   * @body {
   *   query: string
   * }
   */

  router.post(
    '/internal/create',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    hydrateScopedUser(appConfig),
    ValidationMiddleware.validate(enterpriseSearchCreateSchema),
    defaultChatModel,
    shareLimit,
    guards.caller(),
    createConversation(appConfig, turnDeps),
  );

  /**
   * @route POST /api/v1/conversations/stream
   * @desc Stream chat events from AI backend
   * @access Private
   * @body {
   *   query: string
   *   previousConversations: array
   *   filters: object
   * }
   */
  router.post(
    '/attachments/upload',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    chatPdfUpload.array('files'),
    ValidationMiddleware.validate(attachmentUploadSchema),
    uploadChatAttachments(appConfig),
  );

  router.post(
    '/internal/attachments/upload',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    internalAttachmentUpload.array('files'),
    ValidationMiddleware.validate(attachmentUploadSchema),
    uploadChatAttachmentsInternal(appConfig),
  );

  /**
   * @route DELETE /api/v1/conversations/attachments/:recordId
   * @desc  Delete a previously uploaded chat attachment (fire-and-forget from the UI).
   *        The frontend removes the chip immediately; this call cleans up server-side
   *        graph nodes. Failures are silently swallowed on the client.
   */
  router.delete(
    '/attachments/:recordId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(attachmentRecordIdParamsSchema),
    deleteChatAttachment(appConfig),
  );

  router.post(
    '/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(enterpriseSearchStreamCreateSchema),
    defaultChatModel,
    shareLimit,
    guards.caller(),
    streamChat(appConfig, turnDeps),
  );

  router.post(
    '/internal/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    hydrateScopedUser(appConfig),
    ValidationMiddleware.validate(enterpriseSearchCreateSchema),
    defaultChatModel,
    shareLimit,
    guards.caller(),
    streamChatInternal(appConfig, turnDeps),
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/messages
   * @desc Add a new message to existing conversation
   * @access Private
   * @param {string} conversationId - Conversation ID
   * @body {
   *   query: string
   * }
   */
  router.post(
    '/:conversationId/messages',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(addMessageParamsSchema),
    defaultChatModel,
    guards.authorize('send', 'chat'),
    guards.runLease('chat'),
    addMessage(appConfig, turnDeps),
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/messages
   * @desc Add a new message to existing conversation
   * @access Private
   * @param {string} conversationId - Conversation ID
   * @body {
   *   query: string
   * }
   */

  router.post(
    '/internal/:conversationId/messages',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    hydrateScopedUser(appConfig),
    ValidationMiddleware.validate(addMessageParamsSchema),
    defaultChatModel,
    guards.authorize('send', 'chat'),
    guards.runLease('chat'),
    addMessage(appConfig, turnDeps),
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/messages/stream
   * @desc Stream message events from AI backend
   * @access Private
   * @param {string} conversationId - Conversation ID
   * @body {
   *   query: string
   * }
   */
  router.post(
    '/:conversationId/messages/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(addMessageStreamParamsSchema),
    defaultChatModel,
    guards.authorize('send', 'chat'),
    guards.runLease('chat'),
    addMessageStream(appConfig, turnDeps),
  );

  router.post(
    '/internal/:conversationId/messages/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    hydrateScopedUser(appConfig),
    ValidationMiddleware.validate(addMessageParamsSchema),
    defaultChatModel,
    guards.authorize('send', 'chat'),
    guards.runLease('chat'),
    addMessageStreamInternal(appConfig, turnDeps),
  );

  /**
   * @route GET /api/v1/conversations/
   * @desc Get all conversations for a userId
   * @access Private
   * @param {string} conversationId - Conversation ID
   */
  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(getAllConversationsQuerySchema),
    guards.listScope('chat', {
      includeOwned: (req) => req.query.source !== 'shared',
      includeShared: (req) => req.query.source === 'shared',
      archived: 'exclude',
    }),
    getAllConversations,
  );

  /**
   * @route GET /api/v1/conversations/:conversationId
   * @desc Get conversation by ID
   * @access Private
   * @param {string} conversationId - Conversation ID
   */
  router.get(
    '/:conversationId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(conversationIdParamsSchema),
    guards.authorize('read', 'chat'),
    getConversationById(appConfig, turnDeps.users),
  );

  /**
   * @route DELETE /api/v1/conversations/:conversationId
   * @desc Delete conversation by ID
   * @access Private
   * @param {string} conversationId - Conversation ID
   */
  router.delete(
    '/:conversationId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationIdParamsSchema),
    guards.authorize('delete', 'chat'),
    deleteConversationById,
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/share
   * @desc Share conversation by ID
   * @access Private
   * @param {string} conversationId - Conversation ID
   */
  router.post(
    '/:conversationId/share',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    legacyShareScope(container),
    collaborationLimiters(container).mutate,
    ValidationMiddleware.validate(conversationShareParamsSchema),
    guards.authorize('manageCollaborators', 'chat'),
    shareConversationById(appConfig, collaboration),
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/unshare
   * @desc Remove sharing access for specific users
   * @access Private
   * @param {string} conversationId - Conversation ID
   * @body {
   *   userIds: string[] - Array of user IDs to unshare with
   * }
   */
  router.post(
    '/:conversationId/unshare',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    legacyShareScope(container),
    collaborationLimiters(container).mutate,
    ValidationMiddleware.validate(conversationUnshareParamsSchema),
    guards.authorize('manageCollaborators', 'chat'),
    unshareConversationById(appConfig, collaboration),
  );

  /**
   * @route PUT /api/v1/conversations/:conversationId/project
   * @desc Link (or, with `projectId: null`, unlink) a conversation to a project. Owner-only (linkProject guard).
   */
  router.put(
    '/:conversationId/project',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationProjectLinkSchema),
    guards.authorize('linkProject', 'chat'),
    setConversationProject,
  );

  /**
   * @route PATCH /api/v1/conversations/:conversationId/project-visibility
   * @desc Override whether this project-linked conversation is visible to other project members.
   */
  router.patch(
    '/:conversationId/project-visibility',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationProjectVisibilitySchema),
    guards.authorize('linkProject', 'chat'),
    setConversationProjectVisibility,
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/message/:messageId/regenerate
   * @desc Regenerate message by ID
   * @access Private
   * @param {string} conversationId - Conversation ID
   * @param {string} messageId - Message ID
   */
  router.post(
    '/:conversationId/message/:messageId/regenerate',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(regenerateAnswersParamsSchema),
    defaultChatModel,
    guards.authorize('regenerate', 'chat'),
    guards.runLease('chat', { op: 'regenerate' }),
    regenerateAnswers(appConfig, turnDeps),
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/cancel
   * @desc Cooperatively stop an in-flight assistant chat stream
   * @access Private
   * @param {string} conversationId - Conversation ID
   * @body { runId: string (UUID) }
   */
  router.post(
    '/:conversationId/cancel',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(cancelConversationStreamParamsSchema),
    guards.authorize('cancel', 'chat'),
    cancelConversationStream(appConfig),
  );

  /**
   * @route PATCH /api/v1/conversations/:conversationId/title
   * @desc Update title for a conversation
   * @access Private
   * @param {string} conversationId - Conversation ID
   */
  router.patch(
    '/:conversationId/title',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationTitleParamsSchema),
    guards.authorize('rename', 'chat'),
    updateTitle,
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/message/:messageId/feedback
   * @desc Feedback message by ID
   * @access Private
   * @param {string} conversationId - Conversation ID
   * @param {string} messageId - Message ID
   */
  router.post(
    '/:conversationId/message/:messageId/feedback',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(updateFeedbackParamsSchema),
    guards.authorize('feedback', 'chat'),
    updateFeedback,
  );

  /**
   * @route PATCH /api/v1/conversations/:conversationId/
   * @desc Archive Conversation by Conversation ID
   * @access Private
   * @param {string} conversationId - Conversation ID
   */
  router.patch(
    '/:conversationId/archive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationIdParamsSchema),
    guards.authorize('archiveSelf', 'chat'),
    archiveConversation,
  );

  /**
   * @route PATCH /api/v1/conversations/:conversationId/
   * @desc Archive Conversation by Conversation ID
   * @access Private
   * @param {string} conversationId - Conversation ID
   */
  router.patch(
    '/:conversationId/unarchive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationIdParamsSchema),
    guards.authorize('archiveSelf', 'chat'),
    unarchiveConversation,
  );

  /**
   * @route PATCH /api/v1/conversations/:conversationId/
   * @desc Archive Conversation by Conversation ID
   * @access Private
   * @param {string} conversationId - Conversation ID
   */
  router.get(
    '/show/archives',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(listAllArchivesConversationQuerySchema),
    guards.listScope('chat', {
      includeProjects: (_req, collab) => collab,
      archived: 'only',
    }),
    listAllArchivesConversation,
  );

  /**
   * @route GET /api/v1/conversations/show/archives/search
   * @desc Search across all archived conversations (assistant + agent)
   * @access Private
   * @query {string} search - Search term (required)
   * @query {number} page - Page number (default: 1)
   * @query {number} limit - Items per page (default: 20, max: 100)
   */
  router.get(
    '/show/archives/search',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(searchArchivedConversationsQuerySchema),
    guards.listScope('chat', {
      includeProjects: (_req, collab) => collab,
      archived: 'only',
    }),
    searchArchivedConversations(appConfig),
  );

  mountCollaborationRoutes(router, container, 'chat');
  mountMentionRoutes(router, container, 'chat');

  return router;
}

export function createSemanticSearchRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  let appConfig = container.get<AppConfig>('AppConfig');

  router.post(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    ValidationMiddleware.validate(enterpriseSearchSearchSchema),
    search(appConfig),
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_READ),
    ValidationMiddleware.validate(enterpriseSearchSearchHistorySchema),
    searchHistory,
  );

  router.get(
    '/:searchId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_READ),
    ValidationMiddleware.validate(searchIdParamsSchema),
    getSearchById,
  );

  router.delete(
    '/:searchId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_DELETE),
    ValidationMiddleware.validate(searchIdParamsSchema),
    deleteSearchById,
  );

  router.delete(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_DELETE),
    deleteSearchHistory,
  );

  router.patch(
    '/:searchId/share',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    ValidationMiddleware.validate(searchShareParamsSchema),
    shareSearch(appConfig),
  );

  router.patch(
    '/:searchId/unshare',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    ValidationMiddleware.validate(searchShareParamsSchema),
    unshareSearch(appConfig),
  );

  router.patch(
    '/:searchId/archive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    ValidationMiddleware.validate(searchIdParamsSchema),
    archiveSearch,
  );

  router.patch(
    '/:searchId/unarchive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    ValidationMiddleware.validate(searchIdParamsSchema),
    unarchiveSearch,
  );

  router.post(
    '/updateAppConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        appConfig = await loadAppConfig();

        container
          .rebind<AppConfig>('AppConfig')
          .toDynamicValue(() => appConfig);

        res.status(200).json({
          message: 'User configuration updated successfully',
        });
        return;
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}

export function createAgentConversationalRouter(container: Container): Router {
  const router = Router();
  guardPathParams(router, 'agentKey', 'recordId', 'provider', 'model_key');
  const guards = requireConversationGuards(container);
  const turnDeps = requireTurnDeps(container);
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  mountNewChatMentionables(router, container, 'agent');
  const shareLimit = sharingLimiter(container);
  let appConfig = container.get<AppConfig>('AppConfig');
  const keyValueStoreService = container.isBound('KeyValueStoreService')
    ? container.get<KeyValueStoreService>('KeyValueStoreService')
    : undefined;

  const draftRefs = new AgentDraftRefResolver(
    guards,
    container.get<IFeatureFlags>(COLLAB_TYPES.FeatureFlags),
  );

  const agentAttachmentUpload = createMulter({
    storage: multer.memoryStorage(),
    limits: { fileSize: CHAT_ATTACHMENT_UPLOAD_MAX_BYTES, files: 10 },
  });

  const handleAvailabilityLimiter = createKeyedRateLimiter(
    Logger.getInstance({ service: 'AgentRoutes' }),
    {
      prefix: 'agents:handle',
      maxRequestsPerMinute: 60,
      message: 'Too many handle checks. Please try again later.',
      code: 'RATE_LIMITED',
      shared: true,
    },
  );

  /**
   * @route GET /api/v1/agents/conversations/show/archives
   * @desc List all archived agent conversations grouped by agent for the current user.
   *       Must be registered before the /:agentKey wildcard routes.
   */
  router.get(
    '/conversations/show/archives',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(listAllAgentsArchivedConversationsGroupedQuerySchema),
    shareLimit,
    guards.caller(),
    listAllAgentsArchivedConversationsGrouped(appConfig),
  );

  router.post(
    '/:agentKey/conversations',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(agentCreateConversationSchema),
    shareLimit,
    guards.caller(),
    createAgentConversation(appConfig, turnDeps),
  );

  router.post(
    '/:agentKey/conversations/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(agentStreamCreateSchema),
    shareLimit,
    guards.caller(),
    streamAgentConversation(appConfig, turnDeps),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/messages',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(agentAddMessageSchema),
    guards.authorize('send', 'agent'),
    guards.runLease('agent'),
    addMessageToAgentConversation(appConfig, turnDeps),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/messages/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(agentAddMessageParamsSchema),
    guards.authorize('send', 'agent'),
    guards.runLease('agent'),
    addMessageStreamToAgentConversation(appConfig, turnDeps),
  );

  router.post(
    '/:agentKey/conversations/internal/:conversationId/messages/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    hydrateScopedUser(appConfig, keyValueStoreService),
    // requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(agentInternalAddMessageParamsSchema),
    guards.authorize('send', 'agent'),
    guards.runLease('agent'),
    addMessageStreamToAgentConversationInternal(
      appConfig,
      turnDeps,
      keyValueStoreService,
    ),
  );

  router.post(
    '/:agentKey/conversations/internal/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    hydrateScopedUser(appConfig, keyValueStoreService),
    // requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(agentInternalStreamCreateSchema),
    shareLimit,
    guards.caller(),
    streamAgentConversationInternal(appConfig, turnDeps, keyValueStoreService),
  );

  router.post(
    '/:agentKey/conversations/internal/attachments/upload',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    agentAttachmentUpload.array('files'),
    ValidationMiddleware.validate(agentAttachmentUploadSchema),
    uploadChatAttachmentsInternal(appConfig, keyValueStoreService),
  );

  router.post(
    '/:agentKey/conversations/attachments/upload',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    agentAttachmentUpload.array('files'),
    ValidationMiddleware.validate(agentAttachmentUploadSchema),
    uploadChatAttachments(appConfig),
  );

  /**
   * @route DELETE /api/v1/agents/:agentKey/conversations/attachments/:recordId
   * @desc  Delete a previously uploaded agent-chat attachment (fire-and-forget from the UI).
   */
  router.delete(
    '/:agentKey/conversations/attachments/:recordId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(agentAttachmentRecordIdParamsSchema),
    deleteChatAttachment(appConfig),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/message/:messageId/regenerate',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(regenerateAgentAnswersParamsSchema),
    guards.authorize('regenerate', 'agent'),
    guards.runLease('agent', { op: 'regenerate' }),
    regenerateAgentAnswers(appConfig, turnDeps),
  );

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/cancel
   * @desc Cooperatively stop an in-flight agent chat stream
   * @access Private
   * @param {string} agentKey - Agent key
   * @param {string} conversationId - Conversation ID
   * @body { runId: string (UUID) }
   */
  router.post(
    '/:agentKey/conversations/:conversationId/cancel',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(cancelAgentConversationStreamParamsSchema),
    guards.authorize('cancel', 'agent'),
    cancelAgentConversationStream(appConfig),
  );

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/feedback
   * @desc Submit feedback for an agent conversation message
   */
  router.post(
    '/:agentKey/conversations/:conversationId/message/:messageId/feedback',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(updateAgentFeedbackParamsSchema),
    guards.authorize('feedback', 'agent'),
    updateAgentFeedback,
  );

  router.get(
    '/:agentKey/conversations',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(getAllAgentConversationsQuerySchema),
    guards.listScope('agent', {
      shareRows: 'never',
      sharedWithMeList: true,
      archived: 'exclude',
    }),
    getAllAgentConversations,
  );

  router.get(
    '/:agentKey/conversations/:conversationId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(getAgentConversationByIdSchema),
    guards.authorize('read', 'agent'),
    getAgentConversationById,
  );

  router.delete(
    '/:agentKey/conversations/:conversationId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(deleteAgentConversationParamsSchema),
    guards.authorize('delete', 'agent'),
    deleteAgentConversationById,
  );

  /**
   * @route PATCH /api/v1/agents/:agentKey/conversations/:conversationId/title
   * @desc Update title for an agent conversation
   */
  router.patch(
    '/:agentKey/conversations/:conversationId/title',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(agentConversationTitleParamsSchema),
    guards.authorize('rename', 'agent'),
    updateAgentConversationTitle,
  );

  /**
   * @route PUT /api/v1/agents/:agentKey/conversations/:conversationId/project
   * @desc Link (or unlink) an agent conversation to a project. Owner-only (linkProject guard).
   */
  router.put(
    '/:agentKey/conversations/:conversationId/project',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(agentConversationProjectLinkSchema),
    guards.authorize('linkProject', 'agent'),
    setConversationProject,
  );

  /**
   * @route PATCH /api/v1/agents/:agentKey/conversations/:conversationId/project-visibility
   * @desc Override whether this project-linked agent conversation is visible to other project members.
   */
  router.patch(
    '/:agentKey/conversations/:conversationId/project-visibility',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(agentConversationProjectVisibilitySchema),
    guards.authorize('linkProject', 'agent'),
    setConversationProjectVisibility,
  );

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/archive
   * @desc Archive an agent conversation
   */
  router.post(
    '/:agentKey/conversations/:conversationId/archive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(agentConversationParamsSchema),
    guards.authorize('archiveSelf', 'agent'),
    archiveAgentConversation,
  );

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/unarchive
   * @desc Unarchive an agent conversation
   */
  router.post(
    '/:agentKey/conversations/:conversationId/unarchive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(agentConversationParamsSchema),
    guards.authorize('archiveSelf', 'agent'),
    unarchiveAgentConversation,
  );

  /**
   * @route GET /api/v1/agents/:agentKey/conversations/show/archives
   * @desc List all archived agent conversations
   */
  router.get(
    '/:agentKey/conversations/show/archives',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(listAllArchivesAgentConversationQuerySchema),
    guards.listScope('agent', {
      includeShared: (_req, collab) => collab,
      includeProjects: (_req, collab) => collab,
      archived: 'only',
    }),
    listAllArchivesAgentConversation(),
  ); 

  router.post(
    '/create',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(createAgentSchema),
    createAgent(appConfig, draftRefs),
  );

  router.get(
    '/handle-availability',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    handleAvailabilityLimiter,
    ValidationMiddleware.validate(agentHandleAvailabilitySchema),
    checkAgentHandle(appConfig),
  );

  router.get(
    '/:agentKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(getAgentParamsSchema),
    getAgent(appConfig),
  );

  router.put(
    '/:agentKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(updateAgentSchema),
    updateAgent(appConfig),
  );

  router.delete(
    '/:agentKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(deleteAgentSchema),
    deleteAgent(appConfig),
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(listAgentsQuerySchema),
    listAgents(appConfig),
  );

  router.get(
    '/web-search-usage/:provider',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(getWebSearchProviderUsageRequestSchema),
    getWebSearchProviderUsage(appConfig),
  );

  router.get(
    '/model-usage/:model_key',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(getModelUsageRequestSchema),
    getModelUsage(appConfig),
  );

  mountCollaborationRoutes(router, container, 'agent');
  mountMentionRoutes(router, container, 'agent');

  return router;
}

// Matches the Python backend's MAX_STT_AUDIO_BYTES (25 MB) so we reject
// oversized uploads at the node proxy instead of buffering 100 MB into memory
// before the AI backend refuses it.
const MAX_STT_AUDIO_BYTES = 25 * 1024 * 1024;

/**
 * Routes mounted at `/api/v1/chat` that proxy the chat UI's speech endpoints
 * to the Python AI backend:
 *
 *   - GET  /speech/capabilities  → discover configured TTS/STT providers
 *   - POST /speak                → text-to-speech (binary audio response)
 *   - POST /transcribe           → speech-to-text (multipart audio upload)
 *
 * Without this router the frontend's capability probe 404s and the UI falls
 * back to the browser's Web Speech API, which is why a configured TTS model
 * on the admin page would otherwise appear to have no effect.
 */
export function createChatSpeechRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');

  const audioUpload = createMulter({
    storage: multer.memoryStorage(),
    limits: { fileSize: MAX_STT_AUDIO_BYTES, files: 1 },
  });

  router.get(
    '/speech/capabilities',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    getSpeechCapabilities(appConfig),
  );

  router.post(
    '/speak',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    synthesizeSpeech(appConfig),
  );

  router.post(
    '/transcribe',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    audioUpload.single('file'),
    transcribeAudio(appConfig),
  );

  return router;
}

import { Router } from 'express';
import { Container } from 'inversify';
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
  addMessageStream,
  streamAgentConversation,
  streamAgentConversationInternal,
  addMessageStreamToAgentConversation,
  addMessageStreamToAgentConversationInternal,
  getAllAgentConversations,
  getAgentConversationById,
  deleteAgentConversationById,
  createAgent,
  getAgent,
  deleteAgent,
  updateAgent,
  listAgents,
  regenerateAgentAnswers,
  streamChatInternal,
  addMessageStreamInternal,
} from '../controller/es_controller';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  conversationIdParamsSchema,
  enterpriseSearchCreateSchema,
  enterpriseSearchSearchSchema,
  enterpriseSearchSearchHistorySchema,
  searchIdParamsSchema,
  addMessageParamsSchema,
  conversationShareParamsSchema,
  conversationTitleParamsSchema,
  regenerateAnswersParamsSchema,
  updateFeedbackParamsSchema,
  searchShareParamsSchema,
  regenerateAgentAnswersParamsSchema,
} from '../validators/es_validators'; 
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';

export function createConversationalRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  let appConfig = container.get<AppConfig>('AppConfig');

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
    '/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchCreateSchema),
    streamChat(appConfig),
  );

  router.post(
    '/internal/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchCreateSchema),
    streamChatInternal(appConfig),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(addMessageParamsSchema),
    addMessageStream(appConfig),
  );

  router.post(
    '/internal/:conversationId/messages/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(addMessageParamsSchema),
    addMessageStreamInternal(appConfig),
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
    metricsMiddleware(container),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(conversationIdParamsSchema),
    getConversationById,
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(conversationIdParamsSchema),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(conversationShareParamsSchema),
    shareConversationById(appConfig),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(conversationShareParamsSchema),
    unshareConversationById,
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(regenerateAnswersParamsSchema),
    regenerateAnswers(appConfig),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(conversationTitleParamsSchema),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateFeedbackParamsSchema),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(conversationIdParamsSchema),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(conversationIdParamsSchema),
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
    metricsMiddleware(container),
    listAllArchivesConversation,
  );

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
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchSearchSchema),
    search(appConfig),
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchSearchHistorySchema),
    searchHistory,
  );

  router.get(
    '/:searchId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchIdParamsSchema),
    getSearchById,
  );

  router.delete(
    '/:searchId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchIdParamsSchema),
    deleteSearchById,
  );

  router.delete(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_DELETE),
    metricsMiddleware(container),
    deleteSearchHistory,
  );

  router.patch(
    '/:searchId/share',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchShareParamsSchema),
    shareSearch(appConfig),
  );

  router.patch(
    '/:searchId/unshare',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchShareParamsSchema),
    unshareSearch(appConfig),
  );

  router.patch(
    '/:searchId/archive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchIdParamsSchema),
    archiveSearch,
  );

  router.patch(
    '/:searchId/unarchive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchIdParamsSchema),
    unarchiveSearch,
  );

  return router;
}

export function createAgentConversationalRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  let appConfig = container.get<AppConfig>('AppConfig');

  router.post(
    '/:agentKey/conversations/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    streamAgentConversation(appConfig),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/messages/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    addMessageStreamToAgentConversation(appConfig),
  );

  router.post(
    '/:agentKey/conversations/internal/:conversationId/messages/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    // requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    addMessageStreamToAgentConversationInternal(appConfig),
  );

  router.post(
    '/:agentKey/conversations/internal/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    // requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    streamAgentConversationInternal(appConfig),
  );


    router.post(
      '/:agentKey/conversations/:conversationId/message/:messageId/regenerate',
      authMiddleware.authenticate,
      requireScopes(OAuthScopeNames.AGENT_EXECUTE),
      metricsMiddleware(container),
      ValidationMiddleware.validate(regenerateAgentAnswersParamsSchema),
      regenerateAgentAnswers(appConfig),
    );
  

  router.get(
    '/:agentKey/conversations',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    getAllAgentConversations,
  );

  router.get(
    '/:agentKey/conversations/:conversationId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    getAgentConversationById,
  );

  router.delete(
    '/:agentKey/conversations/:conversationId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    deleteAgentConversationById,
  );


  router.post(
    '/create',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    createAgent(appConfig),
  );

  router.get(
    '/:agentKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    getAgent(appConfig),
  );

  router.put(
    '/:agentKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    updateAgent(appConfig),
  );

  router.delete(
    '/:agentKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    deleteAgent(appConfig),
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    listAgents(appConfig),
  );

  return router;
} 


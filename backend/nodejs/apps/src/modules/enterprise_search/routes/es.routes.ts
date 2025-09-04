import { NextFunction, Router, Response } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  addMessage,
  archiveConversation,
  archiveSearch,
  createConversation,
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
  createAgentConversation,
  streamAgentConversation,
  addMessageToAgentConversation,
  addMessageStreamToAgentConversation,
  getAllAgentConversations,
  getAgentConversationById,
  deleteAgentConversationById,
  createAgentTemplate,
  getAgentTemplate,
  deleteAgentTemplate,
  listAgentTemplates,
  createAgent,
  getAgent,
  deleteAgent,
  updateAgent,
  updateAgentTemplate,
  listAgents,
  getAvailableTools,
  shareAgent,
  unshareAgent,
  updateAgentPermissions,
  getAgentPermissions,
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
} from '../validators/es_validators'; 
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';

export function createConversationalRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  let appConfig = container.get<AppConfig>('AppConfig');
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchCreateSchema),
    createConversation(appConfig),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchCreateSchema),
    createConversation(appConfig),
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
    '/stream',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchCreateSchema),
    streamChat(appConfig),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(addMessageParamsSchema),
    addMessage(appConfig),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(addMessageParamsSchema),
    addMessage(appConfig),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(addMessageParamsSchema),
    addMessageStream(appConfig),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchSearchSchema),
    search(appConfig),
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(enterpriseSearchSearchHistorySchema),
    searchHistory,
  );

  router.get(
    '/:searchId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchIdParamsSchema),
    getSearchById,
  );

  router.delete(
    '/:searchId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchIdParamsSchema),
    deleteSearchById,
  );

  router.delete(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    deleteSearchHistory,
  );

  router.patch(
    '/:searchId/share',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchShareParamsSchema),
    shareSearch(appConfig),
  );

  router.patch(
    '/:searchId/unshare',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchShareParamsSchema),
    unshareSearch(appConfig),
  );

  router.patch(
    '/:searchId/archive',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(searchIdParamsSchema),
    archiveSearch,
  );

  router.patch(
    '/:searchId/unarchive',
    authMiddleware.authenticate,
    metricsMiddleware(container),
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
          config: appConfig,
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
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  let appConfig = container.get<AppConfig>('AppConfig');

  router.post(
    '/:agentKey/conversations',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    createAgentConversation(appConfig),
  );

  router.post(
    '/:agentKey/conversations/stream',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    streamAgentConversation(appConfig),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/messages',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    addMessageToAgentConversation(appConfig),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/messages/stream',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    addMessageStreamToAgentConversation(appConfig),
  );

  router.get(
    '/:agentKey/conversations',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getAllAgentConversations,
  );

  router.get(
    '/:agentKey/conversations/:conversationId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getAgentConversationById,
  );

  router.delete(
    '/:agentKey/conversations/:conversationId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    deleteAgentConversationById,
  );

  router.post(
    '/template',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    createAgentTemplate(appConfig),
  );

  router.get(
    '/template/:templateId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getAgentTemplate(appConfig),
  );

  router.put(
    '/template/:templateId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    updateAgentTemplate(appConfig),
  );

  router.delete(
    '/template/:templateId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    deleteAgentTemplate(appConfig),
  );

  router.get(
    '/template',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    listAgentTemplates(appConfig),
  );

  router.post(
    '/create',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    createAgent(appConfig),
  );

  router.get(
    '/:agentKey',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getAgent(appConfig),
  );

  router.put(
    '/:agentKey',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    updateAgent(appConfig),
  );

  router.delete(
    '/:agentKey',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    deleteAgent(appConfig),
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    listAgents(appConfig),
  );

  router.get(
    '/tools/list',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getAvailableTools(appConfig),
  );

  router.get(
    '/:agentKey/permissions',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getAgentPermissions(appConfig),
  );

  router.post(
    '/:agentKey/share',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    shareAgent(appConfig),
  );

  router.post(
    '/:agentKey/unshare',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    unshareAgent(appConfig),
  );

  router.put(
    '/:agentKey/permissions',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    updateAgentPermissions(appConfig),
  );

  return router;
} 


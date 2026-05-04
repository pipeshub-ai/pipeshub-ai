import { NextFunction, Router, Response } from 'express';
import { Container } from 'inversify';
import multer from 'multer';
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
  streamAgentConversationInternal,
  addMessageToAgentConversation,
  addMessageStreamToAgentConversation,
  addMessageStreamToAgentConversationInternal,
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
  getWebSearchProviderUsage,
  getModelUsage,
  getAvailableTools,
  shareAgent,
  unshareAgent,
  updateAgentPermissions,
  getAgentPermissions,
  regenerateAgentAnswers,
  streamChatInternal,
  addMessageStreamInternal,
  updateAgentConversationTitle,
  updateAgentFeedback,
  archiveAgentConversation,
  unarchiveAgentConversation,
  listAllArchivesAgentConversation,
  listAllAgentsArchivedConversationsGrouped,
  searchArchivedConversations,
} from '../controller/es_controller';
import {
  getSpeechCapabilities,
  synthesizeSpeech,
  transcribeAudio,
} from '../controller/speech.controller';
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
  agentConversationTitleParamsSchema,
  agentConversationParamsSchema,
  updateAgentFeedbackParamsSchema,
} from '../validators/es_validators'; 
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';

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
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
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
    metricsMiddleware(container),
    searchArchivedConversations(appConfig),
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
  const keyValueStoreService = container.isBound('KeyValueStoreService')
    ? container.get<KeyValueStoreService>('KeyValueStoreService')
    : undefined;

  /**
   * @route GET /api/v1/agents/conversations/show/archives
   * @desc List all archived agent conversations grouped by agent for the current user.
   *       Must be registered before the /:agentKey wildcard routes.
   */
  router.get(
    '/conversations/show/archives',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    listAllAgentsArchivedConversationsGrouped(appConfig),
  );

  router.post(
    '/:agentKey/conversations',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    createAgentConversation(appConfig),
  );

  router.post(
    '/:agentKey/conversations/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    streamAgentConversation(appConfig),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/messages',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    addMessageToAgentConversation(appConfig),
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
    addMessageStreamToAgentConversationInternal(appConfig, keyValueStoreService),
  );

  router.post(
    '/:agentKey/conversations/internal/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    // requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    streamAgentConversationInternal(appConfig, keyValueStoreService),
  );


    router.post(
      '/:agentKey/conversations/:conversationId/message/:messageId/regenerate',
      authMiddleware.authenticate,
      requireScopes(OAuthScopeNames.AGENT_EXECUTE),
      metricsMiddleware(container),
      ValidationMiddleware.validate(regenerateAgentAnswersParamsSchema),
      regenerateAgentAnswers(appConfig),
    );

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/feedback
   * @desc Submit feedback for an agent conversation message
   */
  router.post(
    '/:agentKey/conversations/:conversationId/message/:messageId/feedback',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateAgentFeedbackParamsSchema),
    updateAgentFeedback,
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

  /**
   * @route PATCH /api/v1/agents/:agentKey/conversations/:conversationId/title
   * @desc Update title for an agent conversation
   */
  router.patch(
    '/:agentKey/conversations/:conversationId/title',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(agentConversationTitleParamsSchema),
    updateAgentConversationTitle,
  );

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/archive
   * @desc Archive an agent conversation
   */
  router.post(
    '/:agentKey/conversations/:conversationId/archive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(agentConversationParamsSchema),
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
    metricsMiddleware(container),
    ValidationMiddleware.validate(agentConversationParamsSchema),
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
    metricsMiddleware(container),
    listAllArchivesAgentConversation(),
  );

  router.post(
    '/template',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    createAgentTemplate(appConfig),
  );

  router.get(
    '/template/:templateId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    getAgentTemplate(appConfig),
  );

  router.put(
    '/template/:templateId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    updateAgentTemplate(appConfig),
  );

  router.delete(
    '/template/:templateId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    deleteAgentTemplate(appConfig),
  );

  router.get(
    '/template',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    listAgentTemplates(appConfig),
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

  router.get(
    '/web-search-usage/:provider',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    getWebSearchProviderUsage(appConfig),
  );

  router.get(
    '/model-usage/:model_key',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    getModelUsage(appConfig),
  );

  router.get(
    '/tools/list',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    getAvailableTools(appConfig),
  );

  router.get(
    '/:agentKey/permissions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    metricsMiddleware(container),
    getAgentPermissions(appConfig),
  );

  router.post(
    '/:agentKey/share',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    shareAgent(appConfig),
  );

  router.post(
    '/:agentKey/unshare',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    unshareAgent(appConfig),
  );

  router.put(
    '/:agentKey/permissions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    metricsMiddleware(container),
    updateAgentPermissions(appConfig),
  );

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

  const audioUpload = multer({
    storage: multer.memoryStorage(),
    limits: { fileSize: MAX_STT_AUDIO_BYTES, files: 1 },
  });

  router.get(
    '/speech/capabilities',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    metricsMiddleware(container),
    getSpeechCapabilities(appConfig),
  );

  router.post(
    '/speak',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    metricsMiddleware(container),
    synthesizeSpeech(appConfig),
  );

  router.post(
    '/transcribe',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    metricsMiddleware(container),
    audioUpload.single('file'),
    transcribeAudio(appConfig),
  );

  return router;
}

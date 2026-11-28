import { requireChatCompareEnabled } from '../utils/chat-compare-feature';
import {
  chooseCompare,
  createCompareBranchHandler,
  type CompareArtifacts,
  listCompareBranches,
  purgeBranchArtifacts,
} from '../controller/compare.controller';
import { NextFunction, Router, Response } from 'express';
import { prometheusChatPathMetrics } from '../../agent-harness/adapters/telemetry/chat-path-metrics';
import { legacyTurnMetrics } from '../../agent-harness/transport/legacy-turn-metrics';
import { scanAfterAuthentication } from '../../agent-harness/transport/pasted-secret-guard';
import { agentEngineCheck } from '../../agent-harness/transport/model-account-routes';
import {
  agentErasureQueue,
  agentDataEraser,
  createActiveRunMiddleware,
  createConversationRunStatesMiddleware,
  createChatCancelMiddleware,
  createChatTurnMiddleware,
  createCollaboratorTurns,
  runFeedback,
  byOrgErasure,
  sessionErasures,
  sessionRunStopper,
  sessionForks,
  sessionParticipants,
  sharedArtifacts,
  sharedCitations,
} from '../../agent-harness/composition';
import {
  slackSurface,
  webSurface,
} from '../../agent-harness/transport/chat-turn-route';
import { Container } from 'inversify';
import { createSessionRoles } from '../../../libs/repositories/agent-harness/session-roles';
import { refuseNonOwnerTurns, storedAsker } from '../utils/turn-role-guard';
import { forkConversation as forkConversationPrivately } from '../controller/session-fork.controller';
import {
  grepRecords,
  searchEntities,
  entityNeighbors,
  entityCapabilities,
  entityRecords,
  coMentionedEntities,
  recordsByPerson,
  recordsByValue,
  recordsByEntities,
} from '../controller/exact_search.controller';
import multer from 'multer';
import { createMulter } from '../../../libs/utils/multer.utils';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  archiveConversation,
  archiveSearch,
  deleteConversationByIdWith,
  deleteSearchById,
  deleteSearchHistory,
  getAllConversations,
  getConversationById,
  getSearchById,
  listAllArchivesConversation,
  pinConversation,
  regenerateAnswers,
  search,
  retrieve,
  searchHistory,
  shareConversationById,
  shareSearch,
  unarchiveConversation,
  unarchiveSearch,
  unpinConversation,
  unshareConversationById,
  unshareSearch,
  updateFeedbackWith,
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
  deleteAgentConversationByIdWith,
  createAgent,
  getAgent,
  deleteAgent,
  updateAgent,
  agentVersions,
  listAgents,
  getWebSearchProviderUsage,
  getModelUsage,
  regenerateAgentAnswers,
  cancelConversationStream,
  cancelAgentConversationStream,
  streamChatInternal,
  addMessageStreamInternal,
  hydrateScopedRequestAsUser,
  updateAgentConversationTitle,
  updateAgentFeedbackWith,
  archiveAgentConversation,
  unarchiveAgentConversation,
  listAllArchivesAgentConversation,
  listAllAgentsArchivedConversationsGrouped,
  searchArchivedConversations,
  setConversationProject,
  setConversationProjectVisibility,
  forkConversation,
  forkAgentConversation,
  shareAgentConversation,
  forkArtifactCopier,
  unshareAgentConversation,
} from '../controller/es_controller';
import {
  deleteArtifactById,
  deleteConversationArtifacts,
} from '../../artifacts/services/conversation-artifacts.service';
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
  slackStreamCreateSchema,
  slackAddMessageStreamSchema,
  enterpriseSearchStreamCreateSchema,
  enterpriseSearchSearchSchema,
  enterpriseSearchRetrieveSchema,
  enterpriseSearchEntitiesSchema,
  enterpriseSearchGrepSchema,
  enterpriseSearchEntityNeighborsSchema,
  enterpriseSearchEntityRecordsSchema,
  enterpriseSearchCoMentionedSchema,
  enterpriseSearchRecordsByPersonSchema,
  enterpriseSearchRecordsByValueSchema,
  enterpriseSearchRecordsByEntitiesSchema,
  enterpriseSearchSearchHistorySchema,
  searchIdParamsSchema,
  addMessageParamsSchema,
  addMessageStreamParamsSchema,
  conversationShareParamsSchema,
  conversationUnshareParamsSchema,
  conversationForkSchema,
  agentConversationForkSchema,
  conversationTitleParamsSchema,
  conversationProjectLinkSchema,
  conversationProjectVisibilitySchema,
  agentConversationProjectLinkSchema,
  agentConversationProjectVisibilitySchema,
  regenerateAnswersParamsSchema,
  compareBranchParamsSchema,
  compareListParamsSchema,
  compareChooseParamsSchema,
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
  withBodyFields,
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
  updateAgentSchema,
  listAgentVersionsSchema,
  getAgentVersionSchema,
  publishAgentVersionSchema,
  rollbackAgentVersionSchema,
  deleteAgentSchema,
  getAgentParamsSchema,
  getWebSearchProviderUsageRequestSchema,
  getModelUsageRequestSchema,
  listAgentsQuerySchema,
  getAgentConversationByIdSchema,
  forkConversationParamsSchema,
  forkAgentConversationParamsSchema,
  agentConversationShareParamsSchema,
  agentConversationUnshareParamsSchema,
} from '../validators/es_validators';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { guardPathParams } from '../../../libs/middlewares/safe-path-params.middleware';
import { CONVERSATION_TYPES } from '../services/conversation/conversation.types';
import { IConversationGuards } from '../services/conversation/http/conversation-guards';
import { ConversationTurnDeps } from '../services/conversation/turn/turn-deps';
import { hydrateScopedUser } from '../services/conversation/http/hydrate-scoped-user.middleware';
import { conversationExtensions } from '../services/conversation/conversation-extensions';

/** Resolved at router build so a container without the binding fails at boot, not on the first request. */
function requireConversationGuards(container: Container): IConversationGuards {
  return container.get<IConversationGuards>(
    CONVERSATION_TYPES.ConversationGuards,
  );
}

/** Resolved at router build for the same reason as the guards. */
function requireTurnDeps(container: Container): ConversationTurnDeps {
  return container.get<ConversationTurnDeps>(
    CONVERSATION_TYPES.ConversationTurnDeps,
  );
}
import { fillDefaultChatModel } from '../utils/default-chat-model';
import {
  expandChatCommand,
  expandViaSkillsService,
  pipeshubCommandsFor,
  regenerateChatCommand,
  storedRegenerateCommand,
} from '../utils/chat-command';
import { pipeshubPromptCommands } from '../../agent-harness/application/pipeshub-prompt-commands';
import type { SessionWorkspaceService } from '../../agent-harness/application/session-workspace-service';
import { AGENT_HARNESS_SESSION_WORKSPACES } from '../../agent-harness/binders/tokens';
import { rejectClientConversationHistory } from '../utils/client-history-guard';

/** Max bytes per file for chat attachment uploads (PDF/JPEG/PNG). Aligned with frontend, Slack, and Python. */
const CHAT_ATTACHMENT_UPLOAD_MAX_BYTES = 5 * 1024 * 1024;

/** `/review` and `/init`, PipesHub's own prompt commands (SLASH2), expanded before SLASH1's user commands. */
const pipeshubCommandsOf = (
  container: Container,
): ReturnType<typeof pipeshubCommandsFor> =>
  pipeshubCommandsFor(
    pipeshubPromptCommands(() =>
      container.isBound(AGENT_HARNESS_SESSION_WORKSPACES)
        ? container.get<SessionWorkspaceService>(
            AGENT_HARNESS_SESSION_WORKSPACES,
          )
        : undefined,
    ),
  );

/** A regenerate of a `/review` answer reviews the workspace again (LC2FIX): its message keeps only what was typed. */
const regenerateCommandOf = (
  container: Container,
): ReturnType<typeof regenerateChatCommand> =>
  regenerateChatCommand(storedRegenerateCommand, pipeshubCommandsOf(container));

export function createConversationalRouter(container: Container): Router {
  const router = Router();
  guardPathParams(router, 'recordId');
  const guards = requireConversationGuards(container);
  const turnDeps = requireTurnDeps(container);
  const extensions = conversationExtensions().routes;
  // SEC-CR-07: a pasted secret never reaches validation, routing or storage; SEC-C-01: nor is an anonymous body scanned.
  const authMiddleware = scanAfterAuthentication(
    container.get<AuthMiddleware>('AuthMiddleware'),
  );
  extensions.mountFirst(router, container, 'chat');
  const firstSend = extensions.firstSend(container);
  const appConfig = container.get<AppConfig>('AppConfig');
  const compareArtifacts: CompareArtifacts = {
    copier: (req) => forkArtifactCopier(appConfig, req),
    remove: (id, req) => deleteConversationArtifacts(appConfig, id, req),
    removeOne: (id, req) => deleteArtifactById(appConfig, id, req),
  };
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
  const turnRoleGuard = refuseNonOwnerTurns(createSessionRoles());
  // R38AUTHZ-FU-2: a collaborator the org lets send turns reaches the harness, which admits their turn as them; one
  // the harness leaves to today's loop is refused there, since that loop serves only the conversation's owner.
  const collaboratorTurns = container.isBound('KeyValueStoreService')
    ? createCollaboratorTurns(
        container.get<KeyValueStoreService>('KeyValueStoreService'),
      )
    : undefined;
  const harnessTurnRoleGuard = refuseNonOwnerTurns(
    createSessionRoles(),
    collaboratorTurns,
  );
  // A regenerate is such a collaborator's turn too, of an answer to their own question only.
  const harnessRegenerateRoleGuard = refuseNonOwnerTurns(
    createSessionRoles(),
    collaboratorTurns,
    storedAsker,
  );
  // Turns today's loop serves, counted beside the durable path's (DUR3).
  const legacyChatTurn = legacyTurnMetrics(
    'assistant',
    prometheusChatPathMetrics,
  );
  // Every turn, web, API/PAT or MCP, is routed by the server in this one request (LAT1).
  const chatTurn = createChatTurnMiddleware(container, {
    kind: 'chat',
    surface: webSurface,
  });
  // A /command becomes the turn's text before validation and routing (SLASH1).
  const chatCommand = expandChatCommand(
    expandViaSkillsService(appConfig),
    pipeshubCommandsOf(container),
  );
  const chatRegenerate = createChatTurnMiddleware(container, {
    kind: 'chat',
    surface: webSurface,
    turn: 'regenerate',
  });
  // A run id naming a running durable turn stops it; any other is today's cancel (LAT2).
  const chatCancel = createChatCancelMiddleware(container, { kind: 'chat' });
  const slackChatRun = createChatTurnMiddleware(container, {
    kind: 'chat',
    surface: slackSurface,
    identify: (req) =>
      hydrateScopedRequestAsUser(req as AuthenticatedServiceRequest, appConfig),
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
    rejectClientConversationHistory,
    ValidationMiddleware.validate(
      withBodyFields(
        enterpriseSearchCreateSchema,
        extensions.requestFields('firstSend'),
      ),
    ),
    defaultChatModel,
    ...firstSend,
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
    rejectClientConversationHistory,
    hydrateScopedUser(appConfig),
    ValidationMiddleware.validate(
      withBodyFields(
        enterpriseSearchCreateSchema,
        extensions.requestFields('firstSend'),
      ),
    ),
    defaultChatModel,
    ...firstSend,
    guards.caller(),
    createConversation(appConfig, turnDeps),
  );

  /**
   * @route POST /api/v1/conversations/stream
   * @desc Stream chat events from AI backend
   * @access Private
   * @body {
   *   query: string
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
    rejectClientConversationHistory,
    chatCommand,
    ValidationMiddleware.validate(
      withBodyFields(
        enterpriseSearchStreamCreateSchema,
        extensions.requestFields('firstSend'),
      ),
    ),
    defaultChatModel,
    ...firstSend,
    guards.caller(),
    chatTurn,
    legacyChatTurn,
    streamChat(appConfig, turnDeps),
  );

  router.post(
    '/internal/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    rejectClientConversationHistory,
    hydrateScopedUser(appConfig),
    ValidationMiddleware.validate(
      withBodyFields(
        slackStreamCreateSchema,
        extensions.requestFields('firstSend'),
      ),
    ),
    defaultChatModel,
    ...firstSend,
    guards.caller(),
    slackChatRun,
    legacyChatTurn,
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
    ValidationMiddleware.validate(
      withBodyFields(
        addMessageParamsSchema,
        extensions.requestFields('followUp'),
      ),
    ),
    turnRoleGuard,
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
    ValidationMiddleware.validate(
      withBodyFields(
        addMessageParamsSchema,
        extensions.requestFields('followUp'),
      ),
    ),
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
    chatCommand,
    ValidationMiddleware.validate(
      withBodyFields(
        addMessageStreamParamsSchema,
        extensions.requestFields('followUp'),
      ),
    ),
    harnessTurnRoleGuard,
    defaultChatModel,
    chatTurn,
    turnRoleGuard,
    legacyChatTurn,
    guards.authorize('send', 'chat'),
    guards.runLease('chat'),
    addMessageStream(appConfig, turnDeps),
  );

  router.post(
    '/internal/:conversationId/messages/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    hydrateScopedUser(appConfig),
    ValidationMiddleware.validate(
      withBodyFields(
        slackAddMessageStreamSchema,
        extensions.requestFields('followUp'),
      ),
    ),
    defaultChatModel,
    slackChatRun,
    legacyChatTurn,
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
    // Chats shared with the user include agent chats; the owned list stays plain chats (see getAllConversations).
    guards.listScope('any', {
      includeOwned: (req) => req.query.source !== 'shared',
      includeShared: (req) => req.query.source === 'shared',
      archived: 'exclude',
    }),
    createConversationRunStatesMiddleware(container),
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
    createActiveRunMiddleware(container, 'chat'),
    getConversationById(appConfig, turnDeps.users, {
      citations: sharedCitations(container),
      artifacts: sharedArtifacts(container),
    }),
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
    // After the guard: a caller who may not change the chat never reaches its Compare branches.
    purgeBranchArtifacts(compareArtifacts),
    byOrgErasure(sessionErasures(container), (erasures) =>
      deleteConversationByIdWith(
        appConfig,
        erasures,
        sessionRunStopper(container),
      ),
    ),
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
    ValidationMiddleware.validate(conversationShareParamsSchema),
    guards.authorize('manageSharing', 'chat'),
    ...extensions.share(container),
    shareConversationById(
      appConfig,
      extensions.sharing(container),
      sessionParticipants(container),
    ),
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/fork
   * @desc Continue a conversation privately (SS3): a copy owned by the caller
   * holding their own turns and a placeholder for everyone else's
   * @access Private (any participant)
   */
  router.post(
    '/:conversationId/fork',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationForkSchema),
    forkConversationPrivately(sessionForks(container)),
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
    ValidationMiddleware.validate(conversationUnshareParamsSchema),
    guards.authorize('manageSharing', 'chat'),
    ...extensions.share(container),
    unshareConversationById(
      appConfig,
      extensions.sharing(container),
      sessionParticipants(container),
    ),
  );

  /**
   * @route PUT /api/v1/conversations/:conversationId/project
   * @desc Link (or, with `projectId: null`, unlink) a conversation to a project. Owner-only (linkProject guard).
   */
  router.put(
    '/:conversationId/project',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    // A second requireScopes is an AND: moving or sharing a chat changes the project.
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(conversationProjectLinkSchema),
    guards.authorize('linkProject', 'chat'),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) =>
      setConversationProject(req, res, next, appConfig),
  );

  /**
   * @route PATCH /api/v1/conversations/:conversationId/project-visibility
   * @desc Override whether this project-linked conversation is visible to other project members.
   */
  router.patch(
    '/:conversationId/project-visibility',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    // A second requireScopes is an AND: moving or sharing a chat changes the project.
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
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
    harnessRegenerateRoleGuard,
    defaultChatModel,
    regenerateCommandOf(container),
    chatRegenerate,
    turnRoleGuard,
    legacyChatTurn,
    guards.authorize('regenerate', 'chat'),
    guards.runLease('chat', { op: 'regenerate' }),
    regenerateAnswers(appConfig, turnDeps),
  );

  const authenticate = authMiddleware.authenticate;

  /**
   * @route POST /api/v1/conversations/:conversationId/compare/branch
   * @desc Compare models: create the hidden branch conversation that re-asks a saved question with a second model
   * @access Private
   */
  router.post(
    '/:conversationId/compare/branch',
    authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    requireChatCompareEnabled,
    ValidationMiddleware.validate(compareBranchParamsSchema),
    // Reading is enough to reach the chat; the handler compares only on the caller's own.
    guards.authorize('read', 'chat'),
    createCompareBranchHandler(compareArtifacts),
  );

  /**
   * @route GET /api/v1/conversations/:conversationId/compare
   * @desc Compare models: the hidden branches of this conversation, to restore the compare view
   * @access Private
   */
  router.get(
    '/:conversationId/compare',
    authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    requireChatCompareEnabled,
    ValidationMiddleware.validate(compareListParamsSchema),
    guards.authorize('read', 'chat'),
    listCompareBranches(),
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/compare/choose
   * @desc Compare models: keep the conversation's own answer (`primary`) or move a branch's turn in; the branches are deleted
   * @access Private
   */
  router.post(
    '/:conversationId/compare/choose',
    authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    requireChatCompareEnabled,
    ValidationMiddleware.validate(compareChooseParamsSchema),
    guards.authorize('read', 'chat'),
    chooseCompare(compareArtifacts),
  );

  /**
   * @route POST /api/v1/conversations/:conversationId/message/:messageId/fork
   * @desc Copy this conversation into a new chat, including every message up to messageId
   */
  router.post(
    '/:conversationId/message/:messageId/fork',
    authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_CHAT),
    ValidationMiddleware.validate(forkConversationParamsSchema),
    guards.authorize('fork', 'chat'),
    forkConversation(appConfig),
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
    chatCancel,
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
    updateFeedbackWith(runFeedback(container)),
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
    // After the guard: a caller who may not change the chat never reaches its Compare branches.
    purgeBranchArtifacts(compareArtifacts),
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
   * @route PATCH /api/v1/conversations/:conversationId/pin
   * @desc Pin a conversation to the top of the owner's list
   * @access Private (owner only)
   * @param {string} conversationId - Conversation ID
   */
  router.patch(
    '/:conversationId/pin',
    authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationIdParamsSchema),
    // Reading is enough to reach the chat; the handler pins only the caller's own.
    guards.authorize('read', 'chat'),
    pinConversation,
  );

  /**
   * @route PATCH /api/v1/conversations/:conversationId/unpin
   * @desc Remove a conversation's pin
   * @access Private (owner only)
   * @param {string} conversationId - Conversation ID
   */
  router.patch(
    '/:conversationId/unpin',
    authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_WRITE),
    ValidationMiddleware.validate(conversationIdParamsSchema),
    guards.authorize('read', 'chat'),
    unpinConversation,
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
      includeProjects: (_req, extended) => extended,
      archived: 'only',
    }),
    listAllArchivesConversation(appConfig),
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
      includeProjects: (_req, extended) => extended,
      archived: 'only',
    }),
    searchArchivedConversations(appConfig),
  );

  extensions.mountLast(router, container, 'chat');

  return router;
}

/** POST /api/v1/retrieve. Same scope as running a search; nothing is stored. */
export function createRetrieveRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');
  router.post(
    '/',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.SEMANTIC_WRITE),
    ValidationMiddleware.validate(enterpriseSearchRetrieveSchema),
    retrieve(appConfig),
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
    requireScopes(
      OAuthScopeNames.SEMANTIC_READ,
      OAuthScopeNames.SEMANTIC_WRITE,
    ),
    ValidationMiddleware.validate(enterpriseSearchSearchSchema),
    search(appConfig),
  );

  // Exact-text search and the entity graph. These sit above `/:searchId` and are POST, so no search id can be read as one of these paths.
  const entityRoute = (
    path: string,
    schema: Parameters<typeof ValidationMiddleware.validate>[0],
    handler: ReturnType<typeof searchEntities>,
  ): void => {
    router.post(
      path,
      // eslint-disable-next-line @typescript-eslint/unbound-method -- used as a method reference by every route in this file
      authMiddleware.authenticate,
      requireScopes(
        OAuthScopeNames.SEMANTIC_READ,
        OAuthScopeNames.SEMANTIC_WRITE,
      ),
      ValidationMiddleware.validate(schema),
      handler,
    );
  };
  entityRoute('/grep', enterpriseSearchGrepSchema, grepRecords(appConfig));
  entityRoute(
    '/entities',
    enterpriseSearchEntitiesSchema,
    searchEntities(appConfig),
  );
  entityRoute(
    '/entities/neighbors',
    enterpriseSearchEntityNeighborsSchema,
    entityNeighbors(appConfig),
  );
  // The assistant's entity tools (what the MCP entity tools call); see the AI service's entity_tools routes.
  router.get(
    '/entities/capabilities',
    // eslint-disable-next-line @typescript-eslint/unbound-method -- used as a method reference by every route in this file
    authMiddleware.authenticate,
    requireScopes(
      OAuthScopeNames.SEMANTIC_READ,
      OAuthScopeNames.SEMANTIC_WRITE,
    ),
    entityCapabilities(appConfig),
  );
  entityRoute(
    '/entities/records',
    enterpriseSearchEntityRecordsSchema,
    entityRecords(appConfig),
  );
  entityRoute(
    '/entities/co-mentioned',
    enterpriseSearchCoMentionedSchema,
    coMentionedEntities(appConfig),
  );
  entityRoute(
    '/records/by-person',
    enterpriseSearchRecordsByPersonSchema,
    recordsByPerson(appConfig),
  );
  entityRoute(
    '/records/by-value',
    enterpriseSearchRecordsByValueSchema,
    recordsByValue(appConfig),
  );
  entityRoute(
    '/records/by-entities',
    enterpriseSearchRecordsByEntitiesSchema,
    recordsByEntities(appConfig),
  );

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_READ),
    ValidationMiddleware.validate(enterpriseSearchSearchHistorySchema),
    searchHistory(appConfig),
  );

  router.get(
    '/:searchId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SEMANTIC_READ),
    ValidationMiddleware.validate(searchIdParamsSchema),
    getSearchById(appConfig),
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
  const extensions = conversationExtensions().routes;
  // SEC-CR-07: a pasted secret never reaches validation, routing or storage; SEC-C-01: nor is an anonymous body scanned.
  const authMiddleware = scanAfterAuthentication(
    container.get<AuthMiddleware>('AuthMiddleware'),
  );
  extensions.mountFirst(router, container, 'agent');
  const firstSend = extensions.firstSend(container);
  const appConfig = container.get<AppConfig>('AppConfig');
  const keyValueStoreService = container.isBound('KeyValueStoreService')
    ? container.get<KeyValueStoreService>('KeyValueStoreService')
    : undefined;

  const agentAttachmentUpload = createMulter({
    storage: multer.memoryStorage(),
    limits: { fileSize: CHAT_ATTACHMENT_UPLOAD_MAX_BYTES, files: 10 },
  });
  const turnRoleGuard = refuseNonOwnerTurns(createSessionRoles());
  const legacyAgentTurn = legacyTurnMetrics('agent', prometheusChatPathMetrics);
  // Every turn, web, API/PAT or MCP, is routed by the server in this one request (LAT1).
  const agentTurn = createChatTurnMiddleware(container, {
    kind: 'agent',
    surface: webSurface,
  });
  const agentCommand = expandChatCommand(
    expandViaSkillsService(appConfig),
    pipeshubCommandsOf(container),
  );
  const agentRegenerate = createChatTurnMiddleware(container, {
    kind: 'agent',
    surface: webSurface,
    turn: 'regenerate',
  });
  const agentCancel = createChatCancelMiddleware(container, { kind: 'agent' });
  const slackAgentRun = createChatTurnMiddleware(container, {
    kind: 'agent',
    surface: slackSurface,
    identify: (req) =>
      hydrateScopedRequestAsUser(
        req as AuthenticatedServiceRequest,
        appConfig,
        keyValueStoreService,
      ),
  });

  /**
   * @route GET /api/v1/agents/conversations/show/archives
   * @desc List all archived agent conversations grouped by agent for the current user.
   *       Must be registered before the /:agentKey wildcard routes.
   */
  router.get(
    '/conversations/show/archives',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(
      listAllAgentsArchivedConversationsGroupedQuerySchema,
    ),
    guards.caller(),
    listAllAgentsArchivedConversationsGrouped(appConfig),
  );

  router.post(
    '/:agentKey/conversations',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    rejectClientConversationHistory,
    ValidationMiddleware.validate(
      withBodyFields(
        agentCreateConversationSchema,
        extensions.requestFields('firstSend'),
      ),
    ),
    ...firstSend,
    guards.caller(),
    createAgentConversation(appConfig, turnDeps),
  );

  router.post(
    '/:agentKey/conversations/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    rejectClientConversationHistory,
    agentCommand,
    ValidationMiddleware.validate(
      withBodyFields(
        agentStreamCreateSchema,
        extensions.requestFields('firstSend'),
      ),
    ),
    ...firstSend,
    guards.caller(),
    agentTurn,
    legacyAgentTurn,
    streamAgentConversation(appConfig, turnDeps),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/messages',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(
      withBodyFields(
        agentAddMessageSchema,
        extensions.requestFields('followUp'),
      ),
    ),
    turnRoleGuard,
    guards.authorize('send', 'agent'),
    guards.runLease('agent'),
    addMessageToAgentConversation(appConfig, turnDeps),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/messages/stream',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    agentCommand,
    ValidationMiddleware.validate(
      withBodyFields(
        agentAddMessageParamsSchema,
        extensions.requestFields('followUp'),
      ),
    ),
    turnRoleGuard,
    agentTurn,
    legacyAgentTurn,
    guards.authorize('send', 'agent'),
    guards.runLease('agent'),
    addMessageStreamToAgentConversation(appConfig, turnDeps),
  );

  // Slack bot entry points. They carry a scoped token, not an OAuth/PAT token, so
  // the scoped-token scope is the gate; requireScopes has no user to check here.
  router.post(
    '/:agentKey/conversations/internal/:conversationId/messages/stream',
    authMiddleware.scopedTokenValidator(TokenScopes.CONVERSATION_CREATE),
    hydrateScopedUser(appConfig, keyValueStoreService),
    // requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(
      withBodyFields(
        agentInternalAddMessageParamsSchema,
        extensions.requestFields('followUp'),
      ),
    ),
    slackAgentRun,
    legacyAgentTurn,
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
    rejectClientConversationHistory,
    hydrateScopedUser(appConfig, keyValueStoreService),
    // requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(
      withBodyFields(
        agentInternalStreamCreateSchema,
        extensions.requestFields('firstSend'),
      ),
    ),
    ...firstSend,
    guards.caller(),
    slackAgentRun,
    legacyAgentTurn,
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
    // eslint-disable-next-line @typescript-eslint/unbound-method -- AuthMiddleware binds authenticate in its constructor
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(regenerateAgentAnswersParamsSchema),
    turnRoleGuard,
    regenerateCommandOf(container),
    agentRegenerate,
    legacyAgentTurn,
    guards.authorize('regenerate', 'agent'),
    guards.runLease('agent', { op: 'regenerate' }),
    regenerateAgentAnswers(appConfig, turnDeps),
  );

  const authenticate = authMiddleware.authenticate;

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/fork
   * @desc Copy this agent conversation into a new chat, including every message up to messageId
   */
  router.post(
    '/:agentKey/conversations/:conversationId/message/:messageId/fork',
    authenticate,
    requireScopes(OAuthScopeNames.AGENT_EXECUTE),
    ValidationMiddleware.validate(forkAgentConversationParamsSchema),
    guards.authorize('fork', 'agent'),
    forkAgentConversation(appConfig),
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
    agentCancel,
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
    updateAgentFeedbackWith(runFeedback(container)),
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
    createConversationRunStatesMiddleware(container),
    getAllAgentConversations,
  );

  router.get(
    '/:agentKey/conversations/:conversationId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(getAgentConversationByIdSchema),
    guards.authorize('read', 'agent'),
    createActiveRunMiddleware(container, 'agent'),
    getAgentConversationById(appConfig, {
      citations: sharedCitations(container),
      artifacts: sharedArtifacts(container),
    }),
  );

  router.post(
    '/:agentKey/conversations/:conversationId/fork',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(agentConversationForkSchema),
    forkConversationPrivately(sessionForks(container)),
  );

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/share
   * @desc Share an agent conversation with other users in the org. Read-only and owner only
   * unless collaborative chats are on, where it follows the chat's sharing rules.
   * @body { userIds: string[], accessLevel?: 'read' }
   */
  router.post(
    '/:agentKey/conversations/:conversationId/share',
    // eslint-disable-next-line @typescript-eslint/unbound-method -- AuthMiddleware binds authenticate in its constructor
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(agentConversationShareParamsSchema),
    guards.authorize('manageSharing', 'agent'),
    ...extensions.share(container),
    shareAgentConversation(
      appConfig,
      extensions.sharing(container),
      sessionParticipants(container),
    ),
  );

  /**
   * @route POST /api/v1/agents/:agentKey/conversations/:conversationId/unshare
   * @desc Remove access to an agent conversation for specific users.
   * @body { userIds: string[] }
   */
  router.post(
    '/:agentKey/conversations/:conversationId/unshare',
    // eslint-disable-next-line @typescript-eslint/unbound-method -- AuthMiddleware binds authenticate in its constructor
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(agentConversationUnshareParamsSchema),
    guards.authorize('manageSharing', 'agent'),
    ...extensions.share(container),
    unshareAgentConversation(
      appConfig,
      extensions.sharing(container),
      sessionParticipants(container),
    ),
  );

  router.delete(
    '/:agentKey/conversations/:conversationId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(deleteAgentConversationParamsSchema),
    guards.authorize('delete', 'agent'),
    byOrgErasure(sessionErasures(container), (erasures) =>
      deleteAgentConversationByIdWith(
        appConfig,
        erasures,
        sessionRunStopper(container),
      ),
    ),
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
    // A second requireScopes is an AND: moving or sharing a chat changes the project.
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
    ValidationMiddleware.validate(agentConversationProjectLinkSchema),
    guards.authorize('linkProject', 'agent'),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) =>
      setConversationProject(req, res, next, appConfig),
  );

  /**
   * @route PATCH /api/v1/agents/:agentKey/conversations/:conversationId/project-visibility
   * @desc Override whether this project-linked agent conversation is visible to other project members.
   */
  router.patch(
    '/:agentKey/conversations/:conversationId/project-visibility',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    // A second requireScopes is an AND: moving or sharing a chat changes the project.
    requireScopes(OAuthScopeNames.PROJECT_WRITE),
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
      includeShared: (_req, extended) => extended,
      includeProjects: (_req, extended) => extended,
      archived: 'only',
    }),
    listAllArchivesAgentConversation(),
  );

  router.post(
    '/create',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(
      withBodyFields(
        createAgentSchema,
        extensions.requestFields('agentCreate'),
      ),
    ),
    agentEngineCheck(container),
    extensions.createAgent(container, appConfig) ?? createAgent(appConfig),
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
    ValidationMiddleware.validate(
      withBodyFields(
        updateAgentSchema,
        extensions.requestFields('agentUpdate'),
      ),
    ),
    agentEngineCheck(container),
    updateAgent(appConfig),
  );

  // OWD-14: an agent's immutable published versions; the agent itself is the draft.
  router.get(
    '/:agentKey/versions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(listAgentVersionsSchema),
    agentVersions(appConfig, 'list'),
  );

  router.get(
    '/:agentKey/versions/:version',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_READ),
    ValidationMiddleware.validate(getAgentVersionSchema),
    agentVersions(appConfig, 'get'),
  );

  router.post(
    '/:agentKey/versions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(publishAgentVersionSchema),
    agentVersions(appConfig, 'publish'),
  );

  router.post(
    '/:agentKey/versions/:version/rollback',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(rollbackAgentVersionSchema),
    agentVersions(appConfig, 'rollback'),
  );

  router.delete(
    '/:agentKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.AGENT_WRITE),
    ValidationMiddleware.validate(deleteAgentSchema),
    deleteAgent(
      appConfig,
      agentDataEraser(container),
      agentErasureQueue(container),
    ),
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

  extensions.mountLast(router, container, 'agent');

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

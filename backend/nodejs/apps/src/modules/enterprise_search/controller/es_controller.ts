import { toWireMentions } from '../services/collaboration/turn/mention-refs';
import { guestAgentRefs, rosterFor } from '../utils/follow-up-turn';
import { turnGuestAgentOf } from '../services/collaboration/mentions/turn-mentions';
import {
  buildMessageSortOptions,
  deleteAgentConversation,
  replaceMessageWithError,
  initializeSSEResponse,
  sendSSEErrorEvent,
  sendSSECompleteEvent,
  handleRegenerationStreamData,
  handleRegenerationSuccess,
  staleAskUserQuestionToolCallIds,
  handleRegenerationError,
} from './../utils/utils';
import sharp from 'sharp';
import { Response, NextFunction } from 'express';
import mongoose, { ClientSession, Types } from 'mongoose';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  handleBackendError,
  SERVICE_UNAVAILABLE_MESSAGE,
} from '../../../libs/errors/backend-error';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { mapAgentHandleError } from '../utils/agent-handle-error';
import {
  AIServiceResponse,
  IAgentConversation,
  IAIResponse,
  IChatSessionDocument,
  IMessage,
  IMessageCitation,
  IMessageDocument,
} from '../types/conversation.interfaces';
import { IConversation } from '../types/conversation.interfaces';
import { ChatSession } from '../schema/chat.session.schema';
import { ChatSessionMessage } from '../schema/chat.session.message.schema';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  addComputedFields,
  attachSharedBy,
  attachSharedByIfRecipient,
  buildFiltersMetadata,
  buildConversationResponse,
  buildFilter,
  buildListQuery,
  buildMessageFilter,
  buildPaginationMetadata,
  buildSortOptions,
  extractModelInfo,
  formatPreviousConversations,
  getPaginationParams,
  olderMessagesWindow,
  sortMessages,
  getMessages,
  attachMessages,
  turnViewerId,
  appendMessageFeedback,
  composeListFilter,
  findSessionIdsMatchingContent,
  listSearchClause,
  validateAndEscapeSearch,
  withoutSharedWith,
  withoutErrorStacks,
  savePartialConversation,
  persistableSharedWith,
  userSharedWithRows,
  sharedWithUserId,
} from '../utils/utils';
import { conversationEventProducers } from '../services/collaboration/notify/conversation-event-producers';
import {
  agentProfiles,
  invalidateAgentCaches,
} from '../services/collaboration/mentions/agent.directory';
import { withRespondingAgents } from '../services/collaboration/mentions/responding-agent';
import { withCollabMessageFields } from '../services/collaboration/feed/message-author';
import {
  IUserDirectory,
  MongoUserDirectory,
} from '../../user_management/services/user-directory.service';
import {
  accessViewOf,
  collabEnabledFor,
  conversationGrantOf,
  conversationContextOf,
  listFilterOf,
  sharedListFilterOf,
} from '../services/collaboration/http/conversation-context';
import {
  listSelect,
  sharedListDecorator,
} from '../services/collaboration/http/list-fields';
import {
  archivedStateFilter,
  listAccessViews,
  ownArchivedClause,
  redactRecipients,
} from '../services/collaboration/http/list-access';
import {
  archiveStateOf,
  perUserArchiveUpdate,
} from '../services/collaboration/access/conversation-archive';
import {
  attachUpstreamAbort,
  isUpstreamAbortError,
  StreamedContentAccumulator,
} from '../utils/stream-lifecycle';
import { AGUI_PROTOCOL, isAGUI, resolveProtocol } from '../utils/agui';
import { IAMServiceCommand } from '../../../libs/commands/iam/iam.service.command';
import EnterpriseSemanticSearch, {
  IEnterpriseSemanticSearch,
} from '../schema/search.schema';
import { AppConfig } from '../../tokens_manager/config/config';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import { IConversationCollaborationService } from '../services/collaboration/conversation-collaboration.service';
import { LegacySharing } from '../services/collaboration/legacy/legacy-sharing';
import Citation, {
  AiSearchResponse,
  ICitation,
} from '../schema/citation.schema';
import { EXCLUDE_AGENT, ONLY_AGENT } from '../constants/constants';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import {
  validateNoXSS,
  validateNoFormatSpecifiers,
} from '../../../utils/xss-sanitization';
import {
  applyProjectScope,
  loadProjectForSession,
} from '../utils/project-context';
import {
  CHAT_ERROR_MESSAGES,
  userFacingChatError,
} from '../utils/chat-error-messages';
import { ProjectService } from '../../projects/services/project.service';
import { ACL_VERSION_INC } from '../../authz/cache/acl-version';
import {
  assignAgentCapabilitiesToPayload,
  assignToolsToPayload,
  parseChatMode,
} from '../utils/ai-chat-payload';
import { hydrateScopedRequestAsUser } from '../utils/scoped-request';
import { startAIStream } from '../utils/ai-stream';
import { isReplicaSet } from '../../../libs/utils/replica-set';
import { firstSendStream } from './first-send-stream.controller';
import { followUpStream } from './follow-up-stream.controller';
import { runIdToCancel } from '../utils/cancel-run';
import { RunCanceller } from '../services/collaboration/http/run-canceller';
import {
  AgentDraftRefResolver,
  DraftRef,
  VerifiedDraftRef,
} from '../services/collaboration/agent-draft/agent-draft-ref.service';
import { ConversationNotFoundError } from '../services/collaboration/domain/errors';
import { JwtServiceTokenIssuer } from '../../../libs/services/service-token.issuer';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import {
  openTurnGate,
  outcomeForError,
  TurnGate,
} from '../services/collaboration/turn/turn-gate';
import {
  outcomeForStatus,
  TurnOutcome,
} from '../services/collaboration/turn/turn-lifecycle';
import { TurnRun } from '../services/collaboration/turn/turn-run';
import { LeaseLostError } from '../services/collaboration/leases/lease.types';
import { STOPPED_FRAME } from '../utils/turn-stream';
const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

const rsAvailable = isReplicaSet();

/** `cause` is read through a cast: the compiler's lib target predates it. */
const causeCode = (error: unknown): string | undefined => {
  if (error === null || typeof error !== 'object') return undefined;
  const cause = (error as { cause?: unknown }).cause;
  if (cause === null || typeof cause !== 'object') return undefined;
  const code = (cause as { code?: unknown }).code;
  return typeof code === 'string' ? code : undefined;
};

/** Remove `id` from graph document clones (Neo4j vs Arango shape) before returning search to the client. */
export function omitId<T>(doc: T): T {
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return doc;
  const o = { ...(doc as Record<string, unknown>) };
  delete o.id;
  return o as T;
}

export function buildSearchResponseForClient(data: AiSearchResponse & Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...data };
  if (Array.isArray(data.records)) out.records = data.records.map(omitId);
  const vmap = data.virtual_to_record_map;
  if (vmap && typeof vmap === 'object' && !Array.isArray(vmap)) {
    out.virtual_to_record_map = Object.fromEntries(
      Object.entries(vmap as Record<string, unknown>).map(([k, v]) => [k, omitId(v)]),
    );
  }
  if (Array.isArray(data.searchResults)) {
    out.searchResults = data.searchResults.map((item) => {
      const row = item as unknown as Record<string, unknown>;
      const meta = row.metadata;
      if (meta && typeof meta === 'object' && !Array.isArray(meta)) {
        return { ...row, metadata: omitId({ ...meta }) };
      }
      return item;
    });
  }
  return out;
}

const AGENT_LIST_PAGE_LIMIT = 200;
/** Newest-first window scanned to find the answer a regenerate targets. */
const REGENERATE_TAIL_MESSAGES = 50;
const AGENT_ARCHIVES_INITIAL_CHAT_LIMIT = 5;
const AGENT_ARCHIVES_INITIAL_AGENT_LIMIT = 5;

/**
 * Loads soft-deleted agent instance keys the user can still see via permissions
 * from the AI backend list-agents API (`isDeleted=true`). Callers should exclude
 * these keys in Mongo (e.g. `agentKey: { $nin: keys }`). Returns null when the AI
 * backend cannot be queried so callers can omit filtering instead of hiding all results.
 */
export async function fetchDeletedAgentKeysForUser(
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
): Promise<string[] | null> {
  const keys: string[] = [];
  let page = 1;
  try {
    while (true) {
      const queryParams = new URLSearchParams();
      queryParams.set('page', String(page));
      queryParams.set('limit', String(AGENT_LIST_PAGE_LIMIT));
      queryParams.set('isDeleted', 'true');
      const uri = `${appConfig.aiBackend}/api/v1/agent/?${queryParams.toString()}`;
      const aiCommand = new AIServiceCommand({
        uri,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      });
      const aiResponse = await aiCommand.execute();
      if (!aiResponse || aiResponse.statusCode !== 200) {
        logger.warn(
          'fetchDeletedAgentKeysForUser: AI backend returned non-200; skipping agent soft-delete filter',
          { statusCode: aiResponse?.statusCode },
        );
        return null;
      }
      const data = aiResponse.data as {
        agents?: Array<Record<string, unknown>>;
        pagination?: { hasNext?: boolean };
      };
      const agents = data.agents ?? [];
      for (const a of agents) {
        const raw = a._key ?? a.id;
        if (typeof raw === 'string' && raw.length > 0) {
          keys.push(raw);
        } else if (typeof raw === 'number' && !Number.isNaN(raw)) {
          keys.push(String(raw));
        }
      }
      if (!data.pagination?.hasNext) break;
      page += 1;
      if (page > 5000) {
        logger.warn('fetchDeletedAgentKeysForUser: page cap hit');
        break;
      }
    }
    return keys;
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : undefined;
    logger.warn(
      'fetchDeletedAgentKeysForUser failed; skipping agent soft-delete filter',
      {
        message,
      },
    );
    return null;
  }
}

/**
 * Parses the chatMode from request body and determines if agent mode is enabled.
 * Supports formats: 'agent:auto', 'agent:quick', 'agent' (defaults to 'quick' — Universal
 * Agent Mode has no strategy selector, so it always runs the fast flat-ReAct loop rather
 * than paying for the LLM tier-classifier round-trip 'auto' would trigger), or regular
 * modes like 'quick'.
 *
 * @param requestChatMode - The chatMode value from request body
 * @returns Object containing the parsed chatMode and agentMode flag
 */
export {
  assignAgentCapabilitiesToPayload,
  assignCallerContextToAiPayload,
  assignToolsToPayload,
  parseChatMode,
} from '../utils/ai-chat-payload';

export {
  addMessage,
  addMessageToAgentConversation,
  createAgentConversation,
  createConversation,
} from './non-streaming-chat.controller';

export { startAIStream };

export {
  checkServiceAccountAccess,
  hydrateScopedRequestAsUser,
  stableObjectIdHexForExternalEmail,
} from '../utils/scoped-request';

  export { handleBackendError };
  
// Image compression configuration: re-encode raster images that exceed the
// per-file threshold so they fit comfortably within multimodal LLM image
// limits and don't bloat downstream storage / token cost. PDFs and small
// images pass through untouched.
const COMPRESSIBLE_IMAGE_MIMES = new Set([
  'image/jpeg',
  'image/jpg',
  'image/png',
  'image/webp',
]);
const IMAGE_COMPRESS_THRESHOLD_BYTES = 1 * 1024 * 1024; // 1 MB
const IMAGE_COMPRESS_TARGET_BYTES = 1 * 1024 * 1024;
const IMAGE_MAX_LONGEST_SIDE_PX = 2048;
const IMAGE_QUALITY_LADDER = [85, 75, 65, 55, 45];

export interface NormalizedAttachmentFile {
  fileName: string;
  mimeType: string;
  size: number;
  buffer: Buffer;
}

export const formatBytes = (bytes: number): string => {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${bytes} B`;
};

export const compressImageIfNeeded = async (
  file: Express.Multer.File,
): Promise<NormalizedAttachmentFile> => {
  const mime = (file.mimetype || '').toLowerCase();
  const passthrough: NormalizedAttachmentFile = {
    fileName: file.originalname,
    mimeType: file.mimetype,
    size: file.size,
    buffer: file.buffer,
  };

  if (!COMPRESSIBLE_IMAGE_MIMES.has(mime)) {
    logger.info(
      `[image-compress] skip ${file.originalname}: ${formatBytes(file.size)} (${file.mimetype}) — non-image MIME, passthrough`,
    );
    return passthrough;
  }

  if (file.size <= IMAGE_COMPRESS_THRESHOLD_BYTES) {
    logger.info(
      `[image-compress] skip ${file.originalname}: ${formatBytes(file.size)} (${file.mimetype}) — under threshold (${formatBytes(IMAGE_COMPRESS_THRESHOLD_BYTES)})`,
    );
    return passthrough;
  }

  logger.info(
    `[image-compress] start ${file.originalname}: ${formatBytes(file.size)} (${file.mimetype})`,
  );

  try {
    const metadata = await sharp(file.buffer).metadata();
    const longestSide = Math.max(metadata.width || 0, metadata.height || 0);
    const needsResize = longestSide > IMAGE_MAX_LONGEST_SIDE_PX;
    const hasAlpha = mime === 'image/png' && Boolean(metadata.hasAlpha);

    const buildBase = () => {
      const base = sharp(file.buffer).rotate();
      if (needsResize) {
        base.resize({
          width: IMAGE_MAX_LONGEST_SIDE_PX,
          height: IMAGE_MAX_LONGEST_SIDE_PX,
          fit: 'inside',
          withoutEnlargement: true,
        });
      }
      return base;
    };

    let outBuffer: Buffer | null = null;
    let outMime = mime;

    if (hasAlpha) {
      outBuffer = await buildBase()
        .png({ compressionLevel: 9, palette: true })
        .toBuffer();
      outMime = 'image/png';
    } else {
      outMime = 'image/jpeg';
      for (const quality of IMAGE_QUALITY_LADDER) {
        outBuffer = await buildBase().jpeg({ quality, mozjpeg: true }).toBuffer();
        if (outBuffer.length <= IMAGE_COMPRESS_TARGET_BYTES) {
          break;
        }
      }
    }

    if (!outBuffer || outBuffer.length >= file.size) {
      logger.info(
        `[image-compress] no-gain ${file.originalname}: before=${formatBytes(file.size)}, after=${formatBytes(outBuffer?.length ?? file.size)} — keeping original`,
      );
      return passthrough;
    }

    let newFileName = file.originalname;
    if (outMime === 'image/jpeg' && !/\.(jpe?g)$/i.test(newFileName)) {
      newFileName = newFileName.replace(/\.[^.]+$/, '') + '.jpg';
    }

    const reduction = (((file.size - outBuffer.length) / file.size) * 100).toFixed(1);
    logger.info(
      `[image-compress] done ${file.originalname}: before=${formatBytes(file.size)} (${file.mimetype}), after=${formatBytes(outBuffer.length)} (${outMime}), reduction=${reduction}%`,
    );

    return {
      fileName: newFileName,
      mimeType: outMime,
      size: outBuffer.length,
      buffer: outBuffer,
    };
  } catch (err) {
    logger.warn(
      `[image-compress] failed ${file.originalname}: before=${formatBytes(file.size)} (${file.mimetype}) — keeping original: ${(err as Error).message}`,
    );
    return passthrough;
  }
};

/** Shared with `projects/controller/project.controller.ts` — project file uploads reuse this same chat-attachment pipeline. */
export const SUPPORTED_CHAT_ATTACHMENT_MIMETYPES = new Set([
  'image/jpeg',
  'image/jpg',
  'image/png',
  'application/pdf',
  'text/plain',
  'text/markdown',
  'text/mdx',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document', // .docx
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', // .xlsx
  'text/csv',
  'text/tab-separated-values', // .tsv
]);

export const uploadChatAttachments =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const files = (req.files as Express.Multer.File[]) || [];
      if (!Array.isArray(files) || files.length === 0) {
        throw new BadRequestError('At least one file is required');
      }

      const invalidFile = files.find(
        (file) => !SUPPORTED_CHAT_ATTACHMENT_MIMETYPES.has((file.mimetype || '').toLowerCase()),
      );
      if (invalidFile) {
        throw new BadRequestError(
          `Unsupported attachment type: ${invalidFile.originalname}. Supported types: PDF, JPEG, PNG, TXT, MD, MDX, DOCX, XLSX, CSV, TSV.`,
        );
      }

      const conversationIdRaw = req.body?.conversationId;
      const conversationId =
        typeof conversationIdRaw === 'string' && conversationIdRaw.trim().length > 0
          ? conversationIdRaw.trim()
          : null;

      const normalizedFiles = await Promise.all(
        files.map((file) => compressImageIfNeeded(file)),
      );

      const aiPayload = {
        conversationId,
        attachments: normalizedFiles.map((file) => ({
          fileName: file.fileName,
          mimeType: file.mimeType,
          size: file.size,
          contentBase64: file.buffer.toString('base64'),
        })),
      };

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/chat/attachments/upload`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: aiPayload,
      };

      const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiServiceCommand.execute();
      const statusCode = aiResponse?.statusCode || 500;
      const responseData = aiResponse?.data || {};

      res.status(statusCode).json(responseData);
    } catch (error: any) {
      next(handleBackendError(error, 'Upload Chat Attachments'));
    }
  };

/**
 * DELETE /api/v1/conversations/attachments/:recordId
 * DELETE /api/v1/agents/:agentKey/conversations/attachments/:recordId
 *
 * Fire-and-forget endpoint called by the frontend when the user removes an
 * attachment chip after its upload has completed. The Node.js layer simply
 * proxies to the Python Query service which handles graph cleanup.
 * Errors are NOT surfaced to the client — the chip is already gone from the
 * UI and a failed delete would only create a confusing error toast.
 */
export const deleteChatAttachment =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction): Promise<void> => {
    try {
      const { recordId } = req.params as { recordId: string };

      // Use a raw fetch rather than AIServiceCommand.execute() because the
      // Python endpoint returns 204 No Content (empty body) and execute()
      // unconditionally calls response.json(), which throws on an empty body.
      const aiUrl = `${appConfig.aiBackend}/api/v1/chat/attachments/${encodeURIComponent(recordId.trim())}`;

      // Mirror the header-filtering that BaseCommand.sanitizeHeaders() applies.
      const allowedHeaders = new Set(['content-type', 'authorization']);
      const forwardHeaders: Record<string, string> = Object.fromEntries(
        Object.entries(req.headers as Record<string, string>).filter(([k]) =>
          allowedHeaders.has(k.toLowerCase()),
        ),
      );

      const aiRes = await fetch(aiUrl, { method: 'DELETE', headers: forwardHeaders });
      res.status(aiRes.status || 204).end();
    } catch (error: any) {
      next(handleBackendError(error, 'Delete Chat Attachment'));
    }
  };

export const uploadChatAttachmentsInternal =
  (appConfig: AppConfig, keyValueStoreService?: KeyValueStoreService) =>
  async (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
    try {
      await hydrateScopedRequestAsUser(req, appConfig, keyValueStoreService);

      const files = (req.files as Express.Multer.File[]) || [];
      if (!Array.isArray(files) || files.length === 0) {
        throw new BadRequestError('At least one attachment is required');
      }

      const normalizedFiles = await Promise.all(
        files.map((file) => compressImageIfNeeded(file)),
      );

      const attachments = normalizedFiles.map((file) => ({
        fileName: file.fileName,
        mimeType: file.mimeType,
        size: file.size,
        contentBase64: file.buffer.toString('base64'),
      }));

      const conversationIdRaw = req.body?.conversationId;
      const conversationId =
        typeof conversationIdRaw === 'string' && conversationIdRaw.trim().length > 0
          ? conversationIdRaw.trim()
          : null;

      const isServiceAgent =
        (req as AuthenticatedUserRequest).user?.isServiceAccount === true;

      const aiPayload = { conversationId, attachments, isServiceAgent };

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/chat/attachments/upload`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: aiPayload,
      };

      const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiServiceCommand.execute();
      const statusCode = aiResponse?.statusCode || 500;
      const responseData = aiResponse?.data || {};

      res.status(statusCode).json(responseData);
    } catch (error: any) {
      next(handleBackendError(error, 'Upload Chat Attachments (Internal)'));
    }
  };

/** Adapts a user-facing stream controller to a service-account route: hydrates the scoped user, then runs it. */
const asInternal =
  (
    factory: ReturnType<typeof firstSendStream>,
    appConfig: AppConfig,
    keyValueStoreService?: KeyValueStoreService,
  ) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      await hydrateScopedRequestAsUser(req, appConfig, keyValueStoreService);
      await factory(
        req as AuthenticatedUserRequest | AuthenticatedServiceRequest,
        res,
        next,
      );
    } catch (error) {
      next(error);
    }
  };

export const streamChat = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): ReturnType<typeof firstSendStream> =>
  firstSendStream(appConfig, deps, 'assistant');

export const streamChatInternal = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
) => asInternal(streamChat(appConfig, deps), appConfig);

export const addMessageStream = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): ReturnType<typeof followUpStream> =>
  followUpStream(appConfig, deps, 'assistant');

export const addMessageStreamInternal = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
) => asInternal(addMessageStream(appConfig, deps), appConfig);

export const getAllConversations = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }
    const { conversationId } = req.query;
    logger.debug('Fetching conversations', {
      requestId,
      message: 'Fetching conversations',
      userId,
      conversationId: req.query.conversationId,
      query: req.query,
    });

    const source = req.query.source ?? 'owned';
    if (source !== 'owned' && source !== 'shared') {
      throw new BadRequestError(
        "Query param 'source' must be 'owned' or 'shared'",
      );
    }

    const { skip, limit, page } = getPaginationParams(req);
    const sortOptions = buildSortOptions(req);

    const isOwned = source === 'owned';
    const ctx = conversationContextOf(req);
    const stateFilter = {
      isArchived: false,
      ...(conversationId && {
        _id: new mongoose.Types.ObjectId(conversationId as string),
      }),
    };
    const { filter, queryConstraints } = await buildListQuery(req, {
      orgId,
      listFilter: listFilterOf(req),
      stateFilter,
    });
    const selection = ChatSession.find(filter)
      .sort(sortOptions as any)
      .skip(skip)
      .limit(limit)
      .select(listSelect(ctx));

    const [conversations, totalCount] = await Promise.all([
      selection.lean().exec(),
      ChatSession.countDocuments(filter),
    ]);

    const views = await listAccessViews(ctx, conversations);
    const decorate = await sharedListDecorator(ctx, conversations);
    let processedConversations = conversations.map((conversation: any) =>
      decorate(
        addComputedFields(
          isOwned ? conversation : withoutSharedWith(conversation),
          userId,
          views.get(String(conversation._id)),
        ),
      ),
    );
    if (!isOwned) {
      processedConversations = await attachSharedBy(
        processedConversations,
        orgId,
      );
    }

    const response = {
      conversations: processedConversations,
      source,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(queryConstraints, req.query),
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Successfully fetched conversations', {
      requestId,
      count: conversations.length,
      totalCount,
      duration: Date.now() - startTime,
    });

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error fetching conversations', {
      requestId,
      message: 'Error fetching conversations',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

const defaultUsers = new MongoUserDirectory();

export const getConversationById =
  (_appConfig: AppConfig, users: IUserDirectory = defaultUsers) =>
  async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  const { conversationId } = req.params;
  try {
    const { sortBy = 'createdAt', sortOrder = 'desc', ...query } = req.query;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Fetching conversation by ID', {
      requestId,
      conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });
    // Get pagination parameters
    const { page, limit } = getPaginationParams(req);

    // Build message filter
    const messageFilter = buildMessageFilter(req);

    // Get sort options for messages
    const messageSortOptions = buildMessageSortOptions(
      sortBy as string,
      sortOrder as string,
    );

    const grant = conversationGrantOf(req);
    const session = await ChatSession.findOne({
      _id: grant.session._id,
      orgId,
      isDeleted: false,
      isArchived: false,
      ...EXCLUDE_AGENT,
    })
      .select({
        title: 1,
        initiator: 1,
        createdAt: 1,
        isShared: 1,
        sharedWith: 1,
        status: 1,
        failReason: 1,
        modelInfo: 1,
        projectId: 1,
        projectVisibility: 1,
      })
      .lean()
      .exec();

    if (!session) {
      throw new NotFoundError('Conversation not found');
    }

    const sessionId = session._id as unknown as Types.ObjectId;

    const totalMessages = await ChatSessionMessage.countDocuments({
      sessionId,
    });

    const { skip, limit: effectiveLimit } = olderMessagesWindow(
      totalMessages,
      page,
      limit,
    );

    const messages = await getMessages(sessionId, {
      skip,
      limit: effectiveLimit,
      populateCitations: true,
    });

    const conversationWithMessages = attachMessages(session, messages, userId);

    // Sort messages using existing helper
    const sortedMessages = sortMessages(
      (conversationWithMessages?.messages ||
        []) as unknown as IMessageDocument[],
      messageSortOptions as { field: keyof IMessage },
    );

    const baseResponse = await attachSharedByIfRecipient(
      buildConversationResponse(
        conversationWithMessages as unknown as IChatSessionDocument,
        userId,
        {
          page,
          limit,
          skip,
          totalMessages,
          hasNextPage: skip > 0,
          hasPrevPage: skip + effectiveLimit < totalMessages,
        },
        sortedMessages,
        accessViewOf(req),
      ),
      orgId,
    );

    const withFields = await withCollabMessageFields(
      baseResponse,
      messages,
      users,
      orgId,
      grant.session.userId.toString(),
      collabEnabledFor(req),
    );
    const conversationResponse = await withRespondingAgents(
      withFields,
      callerIdentityOf(req),
      agentProfiles(),
    );

    // Build filters metadata using existing helper
    const filtersMetadata = buildFiltersMetadata(
      messageFilter,
      query,
      messageSortOptions,
    );

    // Prepare response using existing format
    const response = {
      conversation: conversationResponse,
      filters: filtersMetadata,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
        conversationId,
        messageCount: totalMessages,
      },
    };

    logger.debug('Conversation fetched successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error fetching conversation', {
      requestId,
      conversationId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });

    next(error);
  }
};

export const deleteConversationById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  const { conversationId } = req.params;

  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to delete conversation', {
      requestId,
      message: 'Attempting to delete conversation',
      conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    // Common helper that performs the delete operation.
    async function performDeleteConversation(session?: ClientSession | null) {
      // Get conversation with access control
      const conversation = await ChatSession.findOne(
        {
          _id: conversationGrantOf(req).session._id,
          orgId,
          isDeleted: false,
          ...EXCLUDE_AGENT,
        },
        undefined,
        { session },
      );

      if (!conversation) {
        throw new NotFoundError('Conversation not found');
      }

      // Perform soft delete on the conversation
      const updatedConversation = await ChatSession.findOneAndUpdate(
        { _id: conversationId, ...EXCLUDE_AGENT },
        {
          $set: {
            isDeleted: true,
            deletedBy: userId,
            lastActivityAt: Date.now(),
          },
          ...ACL_VERSION_INC,
        },
        {
          new: true,
          session,
          runValidators: true,
        },
      );

      if (!updatedConversation) {
        throw new InternalServerError('Failed to delete conversation');
      }

      if (collabEnabledFor(req)) {
        await conversationEventProducers().conversationDeleted(
          conversationGrantOf(req),
          session ?? undefined,
        );
      }

      // Extract all citation IDs from messages (projected query, not the
      // full document — message bodies are no longer embedded)
      const sessionMessages = await ChatSessionMessage.find(
        { sessionId: conversationId },
        { citations: 1 },
        { session: session || undefined },
      ).lean();
      const citationIds = sessionMessages
        .filter((msg) => msg.citations && msg.citations.length)
        .flatMap((msg) =>
          msg.citations?.map(
            (citation: IMessageCitation) => citation.citationId,
          ),
        );

      // Update all associated citations if any exist
      if (citationIds.length > 0) {
        await Citation.updateMany(
          {
            _id: { $in: citationIds },
            orgId,
            isDeleted: false,
          },
          {
            $set: {
              isDeleted: true,
              deletedBy: userId,
            },
          },
          { session: session || undefined },
        );
      }

      return { updatedConversation, citationIds };
    }

    let result;
    if (rsAvailable) {
      session = await mongoose.startSession();
      result = await session.withTransaction(() =>
        performDeleteConversation(session),
      );
    } else {
      result = await performDeleteConversation();
    }

    const response = {
      id: conversationId,
      status: 'deleted',
      deletedAt: result.updatedConversation.updatedAt,
      deletedBy: userId,
      citationsDeleted: result.citationIds.length,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Conversation deleted successfully', {
      requestId,
      message: 'Conversation deleted successfully',
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error deleting conversation', {
      requestId,
      message: 'Error deleting conversation',
      conversationId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });

    if (session?.inTransaction()) {
      await session.abortTransaction();
    }
    next(error);
  } finally {
    if (session) {
      session.endSession();
      session = null;
    }
  }
};

/** What the legacy share wrappers need from the collaboration service. */
type LegacyShareApi = Pick<
  IConversationCollaborationService,
  'legacyShare' | 'legacyUnshare'
>;

/** Without a service, share and unshare keep the PH-01 behaviour on their own. */
const legacyOnly = (appConfig: AppConfig): LegacyShareApi => {
  const legacy = new LegacySharing(appConfig.iamBackend, rsAvailable);
  return {
    legacyShare: (grant, identity, userIds) =>
      legacy.share(grant, identity, userIds),
    legacyUnshare: (grant, identity, userIds) =>
      legacy.unshare(grant, identity, userIds),
  };
};

export const shareConversationById =
  (
    appConfig: AppConfig,
    collaboration: LegacyShareApi = legacyOnly(appConfig),
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const { conversationId } = req.params;
    const { userIds, accessLevel } = req.body;
    try {
      logger.debug('Attempting to share conversation', {
        requestId,
        conversationId,
        userIds,
        accessLevel,
        timestamp: new Date().toISOString(),
      });
      if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
        throw new BadRequestError('userIds is required and must be an array');
      }

      const shared = await collaboration.legacyShare(
        conversationGrantOf(req),
        callerIdentityOf(req),
        userIds,
        accessLevel === 'write' ? 'write' : 'read',
      );

      logger.debug('Conversation shared successfully', {
        requestId,
        conversationId,
        duration: Date.now() - startTime,
      });

      res.status(200).json({
        id: shared.id,
        isShared: shared.isShared,
        shareLink: shared.shareLink,
        sharedWith: shared.sharedWith,
        ...(accessLevel === 'write' &&
          shared.appliedAccessLevel !== 'write' && {
            warnings: [
              {
                code: 'ACCESS_LEVEL_DOWNGRADED',
                requested: 'write',
                applied: shared.appliedAccessLevel,
              },
            ],
          }),
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      });
    } catch (error: any) {
      logger.error('Error sharing conversation', {
        requestId,
        message: 'Error sharing conversation',
        conversationId,
        error: error.message,
        stack: error.stack,
        duration: Date.now() - startTime,
      });
      next(error);
    }
  };

export const unshareConversationById =
  (
    appConfig: AppConfig,
    collaboration: LegacyShareApi = legacyOnly(appConfig),
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const { conversationId } = req.params;
    try {
      const { userIds } = req.body;
      logger.debug('Attempting to unshare conversation', {
        requestId,
        conversationId,
        userId: req.user?.userId,
        timestamp: new Date().toISOString(),
      });
      if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
        throw new BadRequestError('userIds is required and must be an array');
      }

      const unshared = await collaboration.legacyUnshare(
        conversationGrantOf(req),
        callerIdentityOf(req),
        userIds,
      );

      logger.debug('Conversation unshared successfully', {
        requestId,
        conversationId,
        duration: Date.now() - startTime,
      });

      res.status(200).json({
        id: unshared.id,
        isShared: unshared.isShared,
        shareLink: unshared.shareLink,
        sharedWith: unshared.sharedWith,
        unsharedUsers: userIds,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      });
    } catch (error: any) {
      logger.error('Error un-sharing conversation', {
        requestId,
        conversationId,
        error: error.message,
        stack: error.stack,
        duration: Date.now() - startTime,
      });
      next(error);
    }
  };

/**
 * PUT /api/v1/conversations/:conversationId/project
 * PUT /api/v1/agents/:agentKey/conversations/:conversationId/project
 * @desc Link or unlink a chat/agent session to a project. Authorized by the
 * route's `linkProject` guard (owner only; a shared recipient cannot move
 * someone else's conversation between projects). `projectId: null` unlinks. `ProjectService.assertAccess`
 * enforces the caller has at least viewer access to the target project
 * (cross-org / no-access -> 404, never a leak of the project's existence).
 */
export const setConversationProject = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { conversationId, agentKey } = req.params;
    const { projectId } = req.body as { projectId: string | null };

    const sessionTypeFilter = agentKey
      ? { ...ONLY_AGENT, agentKey }
      : EXCLUDE_AGENT;

    const conversation = await ChatSession.findOne({
      _id: conversationGrantOf(req).session._id,
      orgId,
      isDeleted: false,
      ...sessionTypeFilter,
    });
    if (!conversation) {
      throw new NotFoundError('Conversation not found or unauthorized');
    }

    let update: Record<string, unknown>;
    if (projectId === null) {
      update = {
        $unset: { projectId: '', projectVisibility: '' },
        ...ACL_VERSION_INC,
      };
    } else {
      const { project } = await ProjectService.assertAccess(
        orgId,
        userId,
        projectId,
        'viewer',
      );
      const projectVisibility =
        conversation.projectVisibility === 'project'
          ? 'project'
          : project.chatSharing === 'members'
            ? 'project'
            : 'private';
      update = {
        $set: {
          projectId: new mongoose.Types.ObjectId(projectId),
          projectVisibility,
        },
        ...ACL_VERSION_INC,
      };
      void ProjectService.touchActivity(projectId);
    }

    const updated = await ChatSession.findOneAndUpdate(
      { _id: conversationId, ...sessionTypeFilter },
      update,
      { new: true },
    );
    if (!updated) {
      throw new InternalServerError('Failed to update conversation project link');
    }

    // Explicit nulls: an unlinked session has no projectId, and JSON would drop the key.
    res.status(200).json({
      conversationId: updated._id,
      projectId: updated.projectId ?? null,
      projectVisibility: updated.projectVisibility ?? null,
    });
  } catch (error: any) {
    logger.error('Error linking conversation to project', {
      requestId,
      error: error.message,
      stack: error.stack,
    });
    next(error);
  }
};

/**
 * PATCH /api/v1/conversations/:conversationId/project-visibility
 * PATCH /api/v1/agents/:agentKey/conversations/:conversationId/project-visibility
 * @desc Override, per conversation, whether this chat is visible to other
 * members of its project ('project') or stays visible only to its owner
 * ('private', the default — see plan's "Chats in shared projects are
 * private by default"). Requires the session already be linked to a project.
 */
export const setConversationProjectVisibility = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  try {
    const orgId = req.user?.orgId;
    const { conversationId, agentKey } = req.params;
    const { visibility } = req.body as { visibility: 'private' | 'project' };

    const sessionTypeFilter = agentKey
      ? { ...ONLY_AGENT, agentKey }
      : EXCLUDE_AGENT;

    const conversation = await ChatSession.findOne({
      _id: conversationGrantOf(req).session._id,
      orgId,
      isDeleted: false,
      ...sessionTypeFilter,
    });
    if (!conversation) {
      throw new NotFoundError('Conversation not found or unauthorized');
    }
    if (!conversation.projectId) {
      throw new BadRequestError('Conversation is not linked to a project');
    }

    const updated = await ChatSession.findOneAndUpdate(
      { _id: conversationId, ...sessionTypeFilter },
      { $set: { projectVisibility: visibility }, ...ACL_VERSION_INC },
      { new: true },
    );
    if (!updated) {
      throw new InternalServerError('Failed to update conversation visibility');
    }

    res.status(200).json({
      conversationId: updated._id,
      projectVisibility: updated.projectVisibility,
    });
  } catch (error) {
    next(error);
  }
};

/**
 * Configuration for regeneration function
 */
interface RegenerationConfig {
  isAgentSession: boolean;
  buildQueryFilter: (
    conversationId: string,
    orgId: string | undefined,
    userId: string | undefined,
    agentKey?: string,
  ) => any;
  buildAIEndpoint: (appConfig: AppConfig, agentKey?: string) => string;
}

/** A row of the turn being regenerated, as the lean reads return it. */
type TurnRow = IMessage & { _id: mongoose.Types.ObjectId; seq: number };

/**
 * Regenerates the answer of a chat (C11) or an agent conversation (A4) in place. With the flag on
 * it runs under the lease `runLease()` acquired, on behalf of the caller (who the guard proved
 * asked the question); with it off the same steps run unfenced.
 */
async function regenerateAnswersInternal(
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  req: AuthenticatedUserRequest,
  res: Response,
  config: RegenerationConfig,
): Promise<void> {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  const { conversationId, messageId, agentKey } = req.params;
  const userId = req.user?.userId;
  const orgId = req.user?.orgId;
  // The guest agent that wrote the answer being regenerated, checked again by the guard.
  const regenGuest = turnGuestAgentOf(req);

  let existingConversation: IChatSessionDocument | null = null;
  let run: TurnRun | undefined;

  const modelInfo = extractModelInfo(req.body);
  const protocol = resolveProtocol(
    req.body as Record<string, unknown>,
    req.query as Record<string, unknown>,
  );

  // Every way the turn can end claims it first, so exactly one of them writes the outcome and settles.
  let finished = false;
  const claim = (): boolean => {
    if (finished) return false;
    finished = true;
    return true;
  };
  let completeData: IAIResponse | null = null;
  const contentAccumulator = new StreamedContentAccumulator();
  // Writes the stream handler starts without awaiting; the terminal write waits for them.
  const pendingWrites: Array<Promise<void>> = [];
  const disconnectSave: { pending: Promise<void> | null } = { pending: null };
  const live: { abort?: () => void } = {};

  const stopped = async (): Promise<void> => {
    if (!claim()) return;
    logger.warn('Run lost its lease; stopping the regeneration', {
      requestId,
      conversationId,
    });
    live.abort?.();
    await gate.settle('stopped');
    res.write(STOPPED_FRAME);
    res.end();
  };
  let gate: TurnGate;
  try {
    gate = openTurnGate(req, () => void stopped());
  } catch (error) {
    if (error instanceof LeaseLostError) return;
    throw error;
  }
  // Before any await, so a client that leaves during validation still releases the lease.
  const upstreamAbort = attachUpstreamAbort(res, requestId, () => {
    if (!claim()) return;
    disconnectSave.pending = (async () => {
      try {
        if (existingConversation && !completeData) {
          await savePartialConversation(
            existingConversation,
            contentAccumulator.getText(),
            null,
            { replaceMessageId: messageId, run },
          );
        }
      } catch (err: unknown) {
        logger.error('Failed to save partial conversation on disconnect', {
          requestId,
          error: err instanceof Error ? err.message : String(err),
        });
      }
      await gate.settle(existingConversation ? 'stopped' : 'unstarted');
    })();
  });
  live.abort = () => {
    upstreamAbort.abort();
  };

  // Helper function to validate and get conversation
  async function performRegenerateAnswersValidation(
    session?: ClientSession | null,
  ): Promise<{
    conversation: IChatSessionDocument;
    userQuery: TurnRow;
    staleAskToolCallIds: mongoose.Types.ObjectId[];
  }> {
    if (!conversationId) {
      throw new BadRequestError('Conversation ID is required');
    }

    // Get conversation with access control
    const queryFilter = config.buildQueryFilter(
      conversationId,
      orgId,
      userId,
      agentKey,
    );

    const conversation = await ChatSession.findOne(
      queryFilter,
      null,
      session ? { session } : undefined,
    );

    if (!conversation) {
      throw new NotFoundError('Conversation not found or unauthorized');
    }

    // Newest-first tail (not just the last 2 rows): an ask_user_question
    // turn ends with `tool_call` rows saved after its answer, so the answer
    // being regenerated is not necessarily the newest message.
    const recentMessages = (await getMessages(
      conversation._id as mongoose.Types.ObjectId,
      { limit: REGENERATE_TAIL_MESSAGES, sort: -1 },
      session,
    )) as TurnRow[];

    if (recentMessages.length === 0) {
      throw new BadRequestError('No messages found in conversation');
    }

    const lastBot = recentMessages.find(
      (msg) => msg.messageType === 'bot_response',
    );
    if (!lastBot || lastBot._id?.toString() !== messageId) {
      throw new BadRequestError(
        'Can only regenerate the last message in the conversation',
      );
    }

    const lastBotIdx = recentMessages.findIndex(
      (msg) => msg._id?.toString() === lastBot._id?.toString(),
    );
    const userQuery = recentMessages
      .slice(lastBotIdx + 1)
      .find((msg) => msg.messageType === 'user_query');
    if (!userQuery) {
      throw new BadRequestError('No user query found to regenerate response');
    }

    logger.debug('Regenerate answers validation passed', {
      requestId,
      conversationId,
      messageId,
      timestamp: new Date().toISOString(),
    });

    // Computed here so the replacement answer costs no extra read: this tail is
    // the turn as it stands before the regeneration overwrites it.
    const staleAskToolCallIds = staleAskUserQuestionToolCallIds(
      [...recentMessages].reverse(),
      lastBot._id,
    );

    return { conversation, userQuery, staleAskToolCallIds };
  }

  /** The last frame of a failed turn, with the conversation as it now stands when the failure was recorded. */
  const sendErrorFrame = async (
    errorMessage: string,
    details: string | undefined,
    withConversation: boolean,
  ): Promise<void> => {
    let plainConversation: unknown;
    if (withConversation) {
      try {
        const updatedConversation = await ChatSession.findById(
          conversationId ?? '',
        );
        if (updatedConversation) {
          const messages = await getMessages(
            updatedConversation._id,
            {},
            session,
          );
          plainConversation = attachMessages(
            updatedConversation.toObject(),
            messages,
            turnViewerId(run, updatedConversation),
          );
        }
      } catch (reloadError: unknown) {
        logger.error('Failed to reload conversation for error event', {
          requestId,
          error:
            reloadError instanceof Error
              ? reloadError.message
              : String(reloadError),
        });
      }
    }
    await sendSSEErrorEvent(
      res,
      errorMessage,
      details,
      plainConversation,
      protocol,
    );
  };

  try {
    // Initialize SSE response
    initializeSSEResponse(res, protocol, gate.lease?.runId);

    logger.debug('Attempting to regenerate answers via stream', {
      requestId,
      conversationId,
      messageId,
      timestamp: new Date().toISOString(),
    });

    // Validate conversation and message
    let validationResult: {
      conversation: IChatSessionDocument;
      userQuery: TurnRow;
      staleAskToolCallIds: mongoose.Types.ObjectId[];
    } | null = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      validationResult = await session.withTransaction(() =>
        performRegenerateAnswersValidation(session),
      );
      // The stream outlives this request, so its listeners write without the session.
      await session.endSession();
      session = null;
    } else {
      validationResult = await performRegenerateAnswersValidation();
    }

    if (!validationResult) {
      throw new NotFoundError('Conversation or message not found');
    }
    existingConversation = validationResult.conversation;
    const userQuery = validationResult.userQuery;
    const staleAskToolCallIds = validationResult.staleAskToolCallIds;
    const turnRun: TurnRun = {
      lease: gate.lease,
      requestedBy: new mongoose.Types.ObjectId(String(userId)),
      inReplyTo: userQuery._id,
      ...(regenGuest !== undefined && { respondingAgentKey: regenGuest }),
    };
    run = turnRun;
    if (upstreamAbort.isClientDisconnected()) {
      await disconnectSave.pending;
      return;
    }

    // The history is what came before the question being answered again; the turn itself (its
    // question, its answer and any `tool_call` rows beside them) is left out by position.
    const regenHistory = await deps.feed.historyBefore(
      existingConversation._id,
      userQuery.seq,
    );
    // The question's author is ranked with the history so refs match the turn that first answered it.
    const regenRoster = gate.lease
      ? await rosterFor(deps, existingConversation, String(userId), [
          ...regenHistory,
          userQuery,
        ], userQuery.mentions)
      : undefined;
    const previousConversations = formatPreviousConversations(
      regenHistory,
      regenRoster?.authors,
      await guestAgentRefs(
        regenHistory,
        callerIdentityOf(req),
        deps.agents ?? agentProfiles(),
      ),
    );
    const regenMentions = regenRoster
      ? toWireMentions(userQuery.mentions, regenRoster.authors.refs)
      : [];

    // For the assistant (non-agent-key) path, detect universal agent mode from chatMode
    // so the regenerate request is routed to the correct backend endpoint and carries tools.
    const { chatMode: parsedRegenChatMode, agentMode: regenIsAgentMode } = parseChatMode(req.body.chatMode);

    // Prepare AI payload
    const aiPayload: Record<string, unknown> = {
      query: userQuery.content,
      previousConversations: previousConversations || [],
      filters: req.body.filters || {},
      attachments: userQuery.attachments || [],
      // New fields for multi-model support
      modelKey: req.body.modelKey || null,
      modelName: req.body.modelName || null,
      modelFriendlyName: req.body.modelFriendlyName || null,
      reasoningEffort: req.body.reasoningEffort || null,
      chatMode: parsedRegenChatMode,
      conversationId: conversationId || null,
      timezone: req.body.timezone || null,
      currentTime: req.body.currentTime || null,
      runId:
        gate.lease?.runId ?? (req.body as { runId?: string }).runId ?? null,
      ...(regenRoster ? { collaboration: regenRoster.collaboration } : {}),
      ...(regenRoster && regenMentions.length > 0 ? { mentions: regenMentions } : {}),
      ...(isAGUI(protocol) ? { protocol: AGUI_PROTOCOL } : {}),
    };
    if (regenGuest === undefined && (agentKey || regenIsAgentMode)) {
      assignToolsToPayload(aiPayload, req.body.tools);
      assignAgentCapabilitiesToPayload(aiPayload, req.body as Record<string, unknown>);
    }
    // With the lease the guard already proved the asker's own project access (D7); without it the owner is the caller.
    const regenProject = gate.lease
      ? conversationContextOf(req).project
      : await loadProjectForSession(
          existingConversation.orgId.toString(),
          (existingConversation.userId as unknown as Types.ObjectId).toString(),
          existingConversation.projectId,
        );
    applyProjectScope(aiPayload, regenProject);
    if (existingConversation.projectId) {
      void ProjectService.touchActivity(existingConversation.projectId.toString());
    }

    const regenEndpoint = regenGuest !== undefined
      ? `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(regenGuest)}/chat/stream`
      : regenIsAgentMode
      ? `${appConfig.aiBackend}/api/v1/agent/agentIdPlaceholder/chat/stream`
      : config.buildAIEndpoint(appConfig, agentKey);

    const aiCommandOptions: AICommandOptions = {
      uri: regenEndpoint,
      method: HttpMethod.POST,
      headers: {
        ...(req.headers as Record<string, string>),
        'Content-Type': 'application/json',
      },
      body: aiPayload,
    };

    // Variables to collect complete response data
    let askUserQuestionPayload: unknown = null;
    let buffer = '';
    /** True when the AI backend already sent a terminal error we forwarded and saved */
    let upstreamAiErrorEventForwarded = false;

    let stream: Awaited<ReturnType<typeof startAIStream>>;
    try {
      stream = await startAIStream(
        aiCommandOptions,
        'Regenerate Answers Stream',
        { requestId },
        upstreamAbort.signal,
      );
    } catch (streamOpenError) {
      // Client disconnected while the AI backend request was still in
      // flight — the disconnect callback above already started the STOPPED
      // save; await it and bail out before the outer `catch` can race it
      // with a FAILED save.
      if (upstreamAbort.isClientDisconnected()) {
        logger.debug('Client disconnected before AI stream opened', {
          requestId,
        });
        if (disconnectSave.pending) await disconnectSave.pending;
        return;
      }
      // The lost-lease path already ended the turn.
      if (upstreamAbort.isAborted()) return;
      throw streamOpenError;
    }

    if (!stream) {
      throw new Error('Failed to get stream from AI service');
    }
    upstreamAbort.bindStream(stream);

    // Process SSE events, capture complete event, and forward non-complete events
    stream.on('data', (chunk: Buffer) => {
      if (finished) return;
      buffer = handleRegenerationStreamData(
        chunk,
        buffer,
        existingConversation,
        messageId || null,
        session,
        requestId || '',
        res,
        (data: IAIResponse) => {
          completeData = data;
          logger.debug('Captured complete event data from AI backend', {
            requestId,
            conversationId: existingConversation?._id,
            answer: completeData?.answer,
            citationsCount: completeData?.citations?.length || 0,
          });
        },
        protocol,
        contentAccumulator,
        () => {
          upstreamAiErrorEventForwarded = true;
        },
        (payload: unknown) => {
          askUserQuestionPayload = payload;
        },
        turnRun,
        (write) => pendingWrites.push(write),
      );
    });

    stream.on('end', async () => {
      logger.debug('Stream ended successfully', { requestId });
      if (!claim()) return;
      let outcome: TurnOutcome = 'failed';
      let lastFrame = (): Promise<void> => Promise.resolve();
      try {
        await Promise.all(pendingWrites);
        // Save the AI response to the conversation, replacing the existing message
        // A failed save is handled once, by the catch below: one error frame, one saved reason.
        if (completeData && existingConversation) {
          const { conversation: responseConversation, savedCitations } =
            await handleRegenerationSuccess(
              completeData,
              existingConversation,
              messageId || '',
              orgId || '',
              session,
              modelInfo,
              askUserQuestionPayload,
              staleAskToolCallIds,
              turnRun,
            );
          outcome = outcomeForStatus(
            (responseConversation as { status?: string }).status,
          );

          // Send final response event with the complete conversation data
          lastFrame = () => {
            sendSSECompleteEvent(
              res,
              responseConversation,
              savedCitations.length,
              requestId ?? '',
              startTime,
              protocol,
            );

            logger.debug(
              'Answer regenerated and conversation updated, sent custom complete event',
              {
                requestId,
                conversationId: existingConversation?._id,
                messageId,
                duration: Date.now() - startTime,
              },
            );
            return Promise.resolve();
          };
        } else if (!upstreamAiErrorEventForwarded) {
          // Mark as failed if no complete data received
          const errorMessage = CHAT_ERROR_MESSAGES.interrupted;
          if (
            existingConversation &&
            messageId !== undefined &&
            messageId !== ''
          ) {
            await replaceMessageWithError(
              existingConversation,
              messageId,
              errorMessage,
              session,
              'no_response',
              undefined,
              undefined,
              turnRun,
            );
            lastFrame = () => sendErrorFrame(errorMessage, undefined, true);
          } else {
            lastFrame = () => sendErrorFrame(errorMessage, undefined, false);
          }
        }
      } catch (dbError: unknown) {
        if (dbError instanceof LeaseLostError) {
          outcome = 'stopped';
          lastFrame = () => {
            res.write(STOPPED_FRAME);
            return Promise.resolve();
          };
        } else {
          const dbMessage =
            dbError instanceof Error ? dbError.message : String(dbError);
          logger.error(
            'Failed to save regenerated AI response to conversation',
            {
              requestId,
              conversationId: existingConversation?._id,
              error: dbMessage,
            },
          );

          const errorMessage =
            causeCode(dbError) === 'ECONNREFUSED'
              ? CHAT_ERROR_MESSAGES.unavailable
              : CHAT_ERROR_MESSAGES.saveFailed;
          let recorded = false;

          // Try to replace message with error if we have the message id
          if (
            existingConversation &&
            messageId !== undefined &&
            messageId !== ''
          ) {
            try {
              await replaceMessageWithError(
                existingConversation,
                messageId,
                errorMessage,
                session,
                'save_error',
                dbError instanceof Error ? dbError.stack : undefined,
                undefined,
                turnRun,
              );
              recorded = true;
            } catch (replaceError: unknown) {
              if (replaceError instanceof LeaseLostError) {
                outcome = 'stopped';
              } else {
                logger.error(
                  'Failed to replace message with error in dbError catch',
                  {
                    requestId,
                    error:
                      replaceError instanceof Error
                        ? replaceError.message
                        : String(replaceError),
                  },
                );
              }
            }
          }
          lastFrame =
            outcome === 'stopped'
              ? () => {
                  res.write(STOPPED_FRAME);
                  return Promise.resolve();
                }
              : () => sendErrorFrame(errorMessage, dbMessage, recorded);
        }
      }

      // Release before the last frame: a browser that sends again on seeing it must not meet our lease.
      await gate.settle(outcome);
      await lastFrame();
      res.end();
    });

    stream.on('error', async (error: Error) => {
      if (
        isUpstreamAbortError(error) ||
        upstreamAbort.isClientDisconnected() ||
        upstreamAbort.isAborted()
      ) {
        logger.debug('Stream aborted due to client disconnect', { requestId });
        return;
      }
      if (!claim()) return;
      logger.error('Stream error in regenerateAnswers', {
        requestId,
        error: error.message,
      });
      try {
        await Promise.all(pendingWrites);
        await handleRegenerationError(
          res,
          error,
          existingConversation,
          messageId || null,
          conversationId || '',
          session,
          requestId || '',
          'stream_error',
          protocol,
          turnRun,
          () => gate.settle('failed'),
        );
      } catch (dbError: unknown) {
        if (dbError instanceof LeaseLostError) {
          await gate.settle('stopped');
          res.write(STOPPED_FRAME);
        } else {
          logger.error('Failed to replace message with error', {
            requestId,
            conversationId: existingConversation?._id,
            error: dbError instanceof Error ? dbError.message : String(dbError),
          });
          await gate.settle('failed');
          await sendSSEErrorEvent(
            res,
            userFacingChatError(error),
            error.message,
            undefined,
            protocol,
          );
        }
      }
      await gate.settle('failed');
      res.end();
    });
  } catch (error: unknown) {
    const failure = error instanceof Error ? error : new Error(String(error));
    logger.error('Error in regenerateAnswers', {
      requestId,
      conversationId,
      messageId,
      error: failure.message,
      stack: failure.stack,
    });
    if (!claim()) return;

    if (!res.headersSent) {
      res.writeHead(500, { 'Content-Type': 'text/event-stream' });
    }

    // Nothing was replaced before the conversation loaded, so the session goes back to idle rather than failed.
    const outcome: TurnOutcome = existingConversation
      ? outcomeForError(error)
      : 'unstarted';
    try {
      await handleRegenerationError(
        res,
        error,
        existingConversation,
        messageId || null,
        conversationId || '',
        session,
        requestId || '',
        'regeneration_error',
        protocol,
        run,
        () => gate.settle(outcome),
      );
    } catch (dbError: unknown) {
      if (dbError instanceof LeaseLostError) {
        await gate.settle('stopped');
        res.write(STOPPED_FRAME);
      } else {
        logger.error('Failed to mark conversation as failed in catch block', {
          requestId,
          conversationId,
          error: dbError instanceof Error ? dbError.message : String(dbError),
        });
        await gate.settle(outcome);
        await sendSSEErrorEvent(
          res,
          userFacingChatError(error),
          failure.message,
          undefined,
          protocol,
        );
      }
    }
    await gate.settle(outcome);
    res.end();
  } finally {
    if (session) {
      session.endSession();
      session = null;
    }
  }
}

export const regenerateAnswers =
  (appConfig: AppConfig, deps: ConversationTurnDeps) =>
  async (req: AuthenticatedUserRequest, res: Response) => {
    await regenerateAnswersInternal(appConfig, deps, req, res, {
      isAgentSession: false,
      buildQueryFilter: (conversationId, orgId) => ({
        _id: conversationId,
        orgId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
      }),
      buildAIEndpoint: (appConfig) =>
        `${appConfig.aiBackend}/api/v1/chat/stream`,
    });
  };

const runCancellers = new WeakMap<AppConfig, RunCanceller>();
function runCancellerFor(appConfig: AppConfig): RunCanceller {
  let canceller = runCancellers.get(appConfig);
  if (!canceller) {
    canceller = new RunCanceller(
      new JwtServiceTokenIssuer(
        new AuthTokenService(appConfig.jwtSecret, appConfig.scopedJwtSecret),
      ),
      () => appConfig.aiBackend,
    );
    runCancellers.set(appConfig, canceller);
  }
  return canceller;
}

/**
 * Cooperatively stop an in-flight `/conversations/:conversationId/stream` or
 * `/messages/stream` run. Same owner filter as `addMessageStream` (initiator
 * only, no `sharedWith` — a shared viewer never gets to stop someone else's
 * generation) so a caller can't probe/cancel a run on a conversation they
 * don't own; Python's `/chat/cancel` (`RunOwner` check against the registry
 * entry) is the second, independent check on the `runId` itself.
 *
 * `{ cancelled: false }` (not a 4xx) is the normal response for a `runId`
 * that already finished or was never registered — the UI only cares whether
 * the stream is now stopped, not why.
 */
export const cancelConversationStream =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const { conversationId } = req.params;
    const { runId } = req.body;
    const orgId = req.user?.orgId;
    try {
      const conversation = await ChatSession.findOne({
        _id: conversationGrantOf(req).session._id,
        orgId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
      });
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
      const run = await runIdToCancel(req, runId as string);
      if (run === undefined) {
        res.status(HTTP_STATUS.OK).json({ cancelled: false });
        return;
      }
      // conversationId is forwarded (not just runId) so Python can reject a
      // runId registered under a different conversation of the same user.
      const aiResponse = await runCancellerFor(appConfig).cancel(
        req,
        conversationId as string,
        run,
      );
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Cancel Conversation Stream');
      }
      res.status(HTTP_STATUS.OK).json(aiResponse.data);
    } catch (error: any) {
      logger.error('Error cancelling conversation stream', {
        requestId,
        conversationId,
        runId,
        error: error.message,
      });
      const backendError = handleBackendError(error, 'Cancel Conversation Stream');
      next(backendError);
    }
  };

export const updateTitle = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to update conversation title', {
      requestId,
      message: 'Attempting to update conversation title',
      conversationId: req.params.conversationId,
      userId,
      title: req.body.title,
      timestamp: new Date().toISOString(),
    });

    const applyTitleUpdate = async (txnSession: ClientSession | null) => {
      const conv = await ChatSession.findOne(
        {
          _id: conversationGrantOf(req).session._id,
          orgId,
          isDeleted: false,
          ...EXCLUDE_AGENT,
        },
        null,
        txnSession ? { session: txnSession } : {},
      );

      if (!conv) {
        throw new NotFoundError('Conversation not found');
      }

      conv.title = req.body.title;
      await conv.save(txnSession ? { session: txnSession } : {});
      return conv;
    };

    let conversation;
    if (rsAvailable) {
      session = await mongoose.startSession();
      conversation = await session.withTransaction(() => applyTitleUpdate(session));
    } else {
      conversation = await applyTitleUpdate(null);
    }

    const response = {
      conversation: {
        ...withoutErrorStacks(conversation.toObject()),
        title: conversation.title,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Conversation title updated successfully', {
      requestId,
      message: 'Conversation title updated successfully',
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error updating conversation title', {
      requestId,
      message: 'Error updating conversation title',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  } finally {
    if (session) {
      await session.endSession();
      session = null;
    }
  }
};

async function performFeedbackUpdate(params: {
  query: Record<string, any>;
  conversationId: string;
  messageId: string;
  userId: string;
  body: any;
  userAgent: string | undefined;
  session?: ClientSession | null;
}): Promise<{ updatedConversation: any; feedbackEntry: any }> {
  const { query, messageId, userId, body, userAgent, session } = params;

  const conversation = await ChatSession.findOne(
    query,
    null,
    session ? { session } : undefined,
  );
  if (!conversation) {
    throw new NotFoundError('Conversation not found');
  }

  const message = await ChatSessionMessage.findOne(
    { _id: messageId, sessionId: conversation._id },
    null,
    session ? { session } : undefined,
  );
  if (!message) {
    throw new NotFoundError('Message not found');
  }

  if (message.messageType !== 'bot_response') {
    throw new BadRequestError('Feedback is only allowed for bot responses');
  }

  const feedbackEntry = {
    ...body,
    feedbackProvider: userId,
    timestamp: Date.now(),
    metrics: {
      timeToFeedback: Date.now() - Number(message.createdAt),
      userInteractionTime: body.metrics?.userInteractionTime,
      feedbackSessionId: body.metrics?.feedbackSessionId,
      userAgent,
    },
  };

  const updatedMessage = await appendMessageFeedback(
    messageId,
    feedbackEntry,
    session,
  );
  if (!updatedMessage) {
    throw new InternalServerError('Failed to update feedback');
  }

  return { updatedConversation: conversation, feedbackEntry };
}

export const updateFeedback = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId, messageId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to update conversation feedback', {
      requestId,
      message: 'Attempting to update conversation feedback',
      conversationId: req.params.conversationId,
      userId,
      feedback: req.body.feedback,
      timestamp: new Date().toISOString(),
    });

    const query = {
      _id: conversationGrantOf(req).session._id,
      orgId,
      isDeleted: false,
      ...EXCLUDE_AGENT,
    };

    let updatedConversation, feedbackEntry;
    if (rsAvailable) {
      session = await mongoose.startSession();
      ({ updatedConversation, feedbackEntry } = await session.withTransaction(
        () => performFeedbackUpdate({
          query, conversationId: conversationId!, messageId: messageId!,
          userId: userId!, body: req.body, userAgent: req.headers['user-agent'], session,
        }),
      ));
    } else {
      ({ updatedConversation, feedbackEntry } = await performFeedbackUpdate({
        query, conversationId: conversationId!, messageId: messageId!,
        userId: userId!, body: req.body, userAgent: req.headers['user-agent'],
      }));
    }

    logger.debug('Feedback updated successfully', {
      requestId,
      conversationId,
      messageId,
      duration: Date.now() - startTime,
    });

    const response = {
      conversationId: updatedConversation._id,
      messageId,
      feedback: feedbackEntry,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error updating conversation feedback', {
      requestId,
      message: 'Error updating conversation feedback',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  } finally {
    if (session) {
      await session.endSession();
      session = null;
    }
  }
};

export const archiveConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const grantedId = conversationGrantOf(req).session._id;
    const collab = conversationContextOf(req).collab === true;

    logger.debug('Attempting to archive conversation', {
      requestId,
      message: 'Attempting to archive conversation',
      conversationId: req.params.conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performArchiveConversation(session?: ClientSession | null) {
      // Get conversation with access control
      const conversation = await ChatSession.findOne(
        {
          _id: grantedId,
          orgId,
          isDeleted: false,
          ...EXCLUDE_AGENT,
        },
        '+archivedFor',
      );

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no archive permission',
        );
      }

      const state = archiveStateOf(conversation, `${userId}`, collab);
      if (state.archived) {
        throw new BadRequestError('Conversation already archived');
      }

      // Perform soft delete
      const updatedConversation: IChatSessionDocument | null =
        await ChatSession.findOneAndUpdate(
          { _id: conversationId, ...EXCLUDE_AGENT },
          state.perUser
            ? perUserArchiveUpdate('archive', conversation, `${userId}`)
            : {
                $set: {
                  isArchived: true,
                  archivedBy: userId,
                  lastActivityAt: Date.now(),
                },
              },
          {
            new: true,
            session,
            runValidators: true,
          },
        ).exec();

      if (!updatedConversation) {
        throw new InternalServerError('Failed to archive conversation');
      }

      return updatedConversation;
    }

    let updatedConversation: IChatSessionDocument | null = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updatedConversation = await performArchiveConversation(session);
      await session.commitTransaction();
    } else {
      updatedConversation = await performArchiveConversation();
    }

    // Prepare response
    const response = {
      id: updatedConversation?._id,
      status: 'archived',
      archivedBy: userId,
      archivedAt: updatedConversation?.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Conversation archived successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error archiving conversation', {
      requestId,
      message: 'Error archiving conversation',
      error: error.message,
    });
    next(error);
  }
};

export const unarchiveConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const grantedId = conversationGrantOf(req).session._id;
    const collab = conversationContextOf(req).collab === true;

    logger.debug('Attempting to unarchive conversation', {
      requestId,
      message: 'Attempting to unarchive conversation',
      conversationId: req.params.conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performUnarchiveConversation(
      session?: ClientSession | null,
    ) {
      // Get conversation with access control
      const conversation = await ChatSession.findOne(
        {
          _id: grantedId,
          orgId,
          isDeleted: false,
          ...EXCLUDE_AGENT,
        },
        '+archivedFor',
      );

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no unarchive permission',
        );
      }

      const state = archiveStateOf(conversation, `${userId}`, collab);
      if (!state.archived) {
        throw new BadRequestError('Conversation is not archived');
      }

      // Perform soft delete
      const updatedConversation: IChatSessionDocument | null =
        await ChatSession.findOneAndUpdate(
          { _id: conversationId, ...EXCLUDE_AGENT },
          state.perUser
            ? perUserArchiveUpdate('unarchive', conversation, `${userId}`)
            : {
                $set: {
                  isArchived: false,
                  archivedBy: null,
                  lastActivityAt: Date.now(),
                },
              },
          {
            new: true,
            session,
            runValidators: true,
          },
        ).exec();

      if (!updatedConversation) {
        throw new InternalServerError('Failed to unarchive conversation');
      }
      return updatedConversation;
    }

    let updatedConversation: IChatSessionDocument | null = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updatedConversation = await performUnarchiveConversation(session);
      await session.commitTransaction();
    } else {
      updatedConversation = await performUnarchiveConversation();
    }

    // Prepare response
    const response = {
      id: updatedConversation?._id,
      status: 'unarchived',
      unarchivedBy: userId,
      unarchivedAt: updatedConversation?.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Conversation un-archived successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error un-archiving conversation', {
      requestId,
      message: 'Error un-archiving conversation',
      error: error.message,
    });
    next(error);
  }
};

export const listAllArchivesConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }
    const { conversationId } = req.query;

    logger.debug('Fetching all archived conversations', {
      requestId,
      userId,
    });

    const { skip, limit, page } = getPaginationParams(req);
    const ctx = conversationContextOf(req);
    const { filter: sessionFilter, queryConstraints } = await buildListQuery(
      req,
      {
        orgId,
        listFilter: listFilterOf(req),
        stateFilter: {
          ...archivedStateFilter(ctx),
          ...(conversationId && {
            _id: new mongoose.Types.ObjectId(conversationId as string),
          }),
        },
      },
    );
    const sortOptions = buildSortOptions(req);

    // Execute query
    const [conversations, totalCount] = await Promise.all([
      ChatSession.find(sessionFilter)
        .sort(sortOptions as any)
        .skip(skip)
        .limit(limit)
        .select('-__v')
        .lean()
        .exec(),
      ChatSession.countDocuments(sessionFilter),
    ]);

    // Batch-fetch every message for the page in one query, grouped in
    // memory by sessionId — never a per-row query.
    const pageIds = conversations.map((c: any) => c._id);
    const allMessages = await ChatSessionMessage.find({
      sessionId: { $in: pageIds },
    })
      .sort({ seq: 1 })
      .lean()
      .exec();
    const messagesBySession = new Map<string, any[]>();
    for (const message of allMessages) {
      const key = message.sessionId.toString();
      const bucket = messagesBySession.get(key);
      if (bucket) {
        bucket.push(message);
      } else {
        messagesBySession.set(key, [message]);
      }
    }

    const views = await listAccessViews(ctx, conversations);

    // Process results with computed fields and restructured citations
    const processedConversations = conversations.map((conversation: any) => {
      const sessionMessages =
        messagesBySession.get(conversation._id.toString()) || [];
      const conversationWithMessages = attachMessages(
        conversation,
        sessionMessages,
        userId,
      );
      const conversationWithComputedFields = addComputedFields(
        redactRecipients(ctx, conversationWithMessages) as IConversation,
        userId,
        views.get(String(conversation._id)),
      );

      return {
        ...conversationWithComputedFields,
        archivedAt: conversation.updatedAt,
        archivedBy: conversation.archivedBy,
      };
    });

    // Build response with metadata
    const response = {
      conversations: processedConversations,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(queryConstraints, req.query),
      summary: {
        totalArchived: totalCount,
        oldestArchive: processedConversations[0]?.archivedAt,
        newestArchive:
          processedConversations[processedConversations.length - 1]?.archivedAt,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Successfully fetched archived conversations', {
      requestId,
      count: conversations.length,
      totalCount,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error fetching archived conversations', {
      requestId,
      message: 'Error fetching archived conversations',
      error: error.message,
    });
    next(error);
  }
};

/**
 * Search across all archived conversations — both assistant (Conversation)
 * and agent (AgentConversation) collections — and return a unified,
 * paginated result sorted by lastActivityAt desc.
 *
 * Query params: `search` (required), `page`, `limit`
 *
 * GET /api/v1/conversations/show/archives/search?search=foo&page=1&limit=20
 */
export const searchArchivedConversations =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }

    const deletedAgentKeys = await fetchDeletedAgentKeysForUser(appConfig, req);

    // ── Search parameter (required) ────────────────────────────────
    const rawSearch = req.query.search;
    if (!rawSearch || typeof rawSearch !== 'string' || !rawSearch.trim()) {
      throw new BadRequestError('search query parameter is required');
    }
    const searchValue = rawSearch.trim();
    const escapedSearch = validateAndEscapeSearch(searchValue, {
      formatSpecifiers: true,
    });

    // ── Pagination ─────────────────────────────────────────────────
    const { skip, limit, page } = getPaginationParams(req);

    logger.debug('Searching archived conversations (assistant + agent)', {
      requestId,
      userId,
      search: searchValue,
      page,
      limit,
    });

    const ctx = conversationContextOf(req);
    const orgOid = new mongoose.Types.ObjectId(`${orgId}`);
    const userOid = new mongoose.Types.ObjectId(`${userId}`);

    const stateFilter = archivedStateFilter(ctx);
    // Agent chats stay owner-only here: this route has no agentKey, which the access filter needs.
    const agentAccessFilter = {
      orgId: orgOid,
      isDeleted: false,
      ...ONLY_AGENT,
      userId: userOid,
      ...(deletedAgentKeys !== null && {
        agentKey: { $nin: deletedAgentKeys },
      }),
    };

    // Message content now lives in chatSessionMessages; resolve matching
    // session ids within the accessible sessions only, so the content cap
    // cannot hide accessible results behind other users' matches.
    const assistantScope = composeListFilter(listFilterOf(req), stateFilter);
    const agentScope = composeListFilter(
      agentAccessFilter,
      stateFilter,
      ownArchivedClause(ctx),
    );
    const contentMatchIds = await findSessionIdsMatchingContent(
      orgId,
      escapedSearch,
      { sessionFilter: { $or: [assistantScope, agentScope] } },
    );
    const searchFilter = listSearchClause({
      escaped: escapedSearch,
      contentMatchIds,
    });
    const assistantFilter = composeListFilter(
      listFilterOf(req),
      stateFilter,
      searchFilter,
    );
    const agentFilter = composeListFilter(
      agentAccessFilter,
      stateFilter,
      ownArchivedClause(ctx),
      searchFilter,
    );

    // ── Execute: single $match ORing both per-type predicates, one
    // combined aggregation replacing the old cross-collection $unionWith,
    // and one countDocuments per predicate ──
    const [aggregateResult, assistantCount, agentCount] = await Promise.all([
      ChatSession.aggregate([
        { $match: { $or: [assistantFilter, agentFilter] } },
        {
          $addFields: {
            source: {
              $cond: [{ $eq: ['$sessionType', 'agent'] }, 'agent', 'assistant'],
            },
          },
        },
        { $sort: { lastActivityAt: -1 } },
        { $skip: skip },
        { $limit: limit },
        { $project: { __v: 0, nextSeq: 0, sessionType: 0 } },
      ]),
      ChatSession.countDocuments(assistantFilter),
      ChatSession.countDocuments(agentFilter),
    ]);

    const totalCount = assistantCount + agentCount;

    // ── Tag and apply computed fields (bounded to `limit` docs) ──────
    const views = await listAccessViews(ctx, aggregateResult);
    const paginatedResults = aggregateResult.map((c: any) => ({
      ...addComputedFields(
        redactRecipients(ctx, c) as IConversation,
        userId,
        views.get(String(c._id)),
      ),
      archivedAt: c.updatedAt,
      archivedBy: c.archivedBy,
      source: c.source as 'assistant' | 'agent',
      ...(c.source === 'agent' ? { agentKey: c.agentKey } : {}),
    }));

    const response = {
      conversations: paginatedResults,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      summary: {
        totalMatches: totalCount,
        assistantMatches: assistantCount,
        agentMatches: agentCount,
        searchQuery: searchValue,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Successfully searched archived conversations', {
      requestId,
      totalMatches: totalCount,
      assistantMatches: assistantCount,
      agentMatches: agentCount,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error searching archived conversations', {
      requestId,
      message: 'Error searching archived conversations',
      error: error.message,
    });
    next(error);
  }
  };

export const search =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const aiBackendUrl = appConfig.aiBackend;
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    try {
      const { query, limit, filters } = req.body;

      // Validate query parameter for XSS and format specifiers
      if (query && typeof query === 'string') {
        validateNoXSS(query, 'search query');
        validateNoFormatSpecifiers(query, 'search query');
      }

      logger.debug('Attempting to search', {
        requestId,
        query,
        limit,
        filters,
        timestamp: new Date().toISOString(),
      });

      const aiCommand = new AIServiceCommand({
        uri: `${aiBackendUrl}/api/v1/search`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: { query, limit, filters },
      });

      let aiResponse;
      try {
        aiResponse =
          (await aiCommand.execute()) as AIServiceResponse<AiSearchResponse>;
      } catch (error: any) {
        if (error.cause && error.cause.code === 'ECONNREFUSED') {
          throw new InternalServerError(SERVICE_UNAVAILABLE_MESSAGE, error);
        }
        logger.error(' Failed error ', error);
        throw new InternalServerError(SERVICE_UNAVAILABLE_MESSAGE, error);
      }
      
      if (!aiResponse || !aiResponse.data) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(
          {
            response: { status: aiResponse.statusCode, data: aiResponse.data },
          },
          'Search',
        );
      }

      const results = aiResponse.data.searchResults;
      let citationIds;
      if (results) {
        // save the citations to the citations collection
        citationIds = await Promise.all(
          results.map(async (result: ICitation) => {
            const citationDoc = new Citation({
              content: result.content,
              chunkIndex: result.chunkIndex ?? 0, // fallback to 0 if not present
              citationType: result.citationType,
              metadata: result.metadata,
            });

            const savedCitation = await citationDoc.save();
            return savedCitation._id;
          }),
        );
      }

      const recordsArray = aiResponse.data?.records || {};
      const recordsMap = new Map();

      if (Array.isArray(recordsArray)) {
        recordsArray.forEach((record) => {
          // Use either _id or _key as the unique key for each record
          const key = record._id || record._key;
          // Convert the record object to a string since your schema expects Map<string, string>
          recordsMap.set(key, JSON.stringify(record));
        });
      }
      // Save the entire search operation as a single document
      const searchRecord = await new EnterpriseSemanticSearch({
        query,
        limit,
        orgId,
        userId,
        citationIds,
        records: recordsMap,
      }).save();

      logger.debug('Saved search operation', {
        requestId,
        searchId: searchRecord._id,
        resultCount: Array.isArray(aiResponse.data)
          ? aiResponse.data.length
          : 1,
      });

      // Return the response (strip Neo4j duplicate `id` === `_key` for client parity with Arango)
      res.status(HTTP_STATUS.OK).json({
        searchId: searchRecord._id,
        searchResponse: buildSearchResponseForClient(aiResponse.data as AiSearchResponse & Record<string, unknown>),
      });
    } catch (error: any) {
      logger.error('Error searching query', {
        requestId,
        message: 'Error searching query',
        error: error.message,
      });
      next(error);
    }
  };

export const searchHistory = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();

  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }
    const { page, limit, skip } = getPaginationParams(req);
    const sortOptions = buildSortOptions(req);
    const filter = buildFilter(req, orgId, userId);

    logger.debug('Attempting to get search history', {
      requestId,
      timestamp: new Date().toISOString(),
    });

    const [searchHistory, totalCount] = await Promise.all([
      EnterpriseSemanticSearch.find(filter)
        .sort(sortOptions as any)
        .skip(skip)
        .limit(limit)
        .exec(),
      EnterpriseSemanticSearch.countDocuments({
        filter,
      }),
    ]);

    const response = {
      searchHistory: searchHistory,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(filter, req.query),
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };
    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error searching history', {
      requestId,
      message: 'Error searching history',
      error: error.message,
    });
    next(error);
  }
};

export const getSearchById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const searchId = req.params.searchId;

  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    const filter = buildFilter(req, orgId, userId, searchId);

    logger.debug('Attempting to get search', {
      requestId,
      searchId,
      timestamp: new Date().toISOString(),
    });
    const search = await EnterpriseSemanticSearch.find(filter)
      .populate({
        path: 'citationIds',
        model: 'citation',
        select: '-__v',
      })
      .lean()
      .exec();
    if (!search) {
      throw new NotFoundError('Search Id not found');
    }

    res.status(HTTP_STATUS.OK).json(search);
  } catch (error: any) {
    logger.error('Error getting search by ID', {
      requestId,
      message: 'Error getting search by ID',
      error: error.message,
    });
    next(error);
  }
};

export const deleteSearchById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const searchId = req.params.searchId;

  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    const filter = buildFilter(req, orgId, userId, searchId);

    logger.debug('Attempting to delete search', {
      requestId,
      searchId,
      timestamp: new Date().toISOString(),
    });

    const search = await EnterpriseSemanticSearch.findOneAndDelete(filter);
    if (!search) {
      throw new NotFoundError('Search Id not found');
    }

    // delete related citations
    await Citation.deleteMany({ _id: { $in: search.citationIds } });

    res.status(HTTP_STATUS.OK).json({ message: 'Search deleted successfully' });
  } catch (error: any) {
    logger.error('Error deleting search by ID', {
      requestId,
      message: 'Error deleting search by ID',
      error: error.message,
    });
    next(error);
  }
};

export const shareSearch =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const searchId = req.params.searchId;
    const { userIds, accessLevel } = req.body;

    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const filter = buildFilter(req, orgId, userId, searchId);

      logger.debug('Attempting to share search', {
        requestId,
        searchId,
        userIds,
        accessLevel,
        timestamp: new Date().toISOString(),
      });

      if (accessLevel && !['read', 'write'].includes(accessLevel)) {
        throw new BadRequestError(
          "Invalid access level. Must be 'read' or 'write'",
        );
      }

      const search: IEnterpriseSemanticSearch | null =
        await EnterpriseSemanticSearch.findOne(filter);
      if (!search) {
        throw new NotFoundError('Search Id not found');
      }

      // Update object for conversation
      const updateObject: Partial<IEnterpriseSemanticSearch> = {
        isShared: true,
      };

      updateObject.shareLink = `${appConfig.frontendUrl}/api/v1/search/${search._id}`;

      // Validate all user IDs
      const validUsers = await Promise.all(
        userIds.map(async (id: string) => {
          if (!mongoose.Types.ObjectId.isValid(id)) {
            throw new BadRequestError(`Invalid user ID format: ${id}`);
          }
          try {
            const iamCommand = new IAMServiceCommand({
              uri: `${appConfig.iamBackend}/api/v1/users/${encodeURIComponent(id)}`,
              method: HttpMethod.GET,
              headers: req.headers as Record<string, string>,
            });
            const userResponse = await iamCommand.execute();
            if (userResponse && userResponse.statusCode !== 200) {
              throw new BadRequestError(`User not found: ${id}`);
            }
          } catch (exception) {
            logger.debug(`User does not exist: ${id}`, {
              requestId,
            });
            throw new BadRequestError(`User not found: ${id}`);
          }
          return {
            userId: id,
            accessLevel: accessLevel || 'read',
          };
        }),
      );

      // Get existing shared users
      const existingSharedWith = persistableSharedWith(search.sharedWith);

      // Create a map of existing users for quick lookup
      const existingUserMap = new Map(
        userSharedWithRows(existingSharedWith).map((share) => [
            sharedWithUserId(share) ?? '',
            share,
          ] as const),
      );

      // Merge existing and new users, updating access levels for existing users if they're in the new list
      const mergedSharedWith = [...existingSharedWith];

      for (const newUser of validUsers) {
        const existingUser = existingUserMap.get(newUser.userId.toString());
        if (existingUser) {
          // Update access level if user already exists
          existingUser.accessLevel = newUser.accessLevel;
        } else {
          // Add new user if they don't exist
          mergedSharedWith.push(newUser);
        }
      }

      // Update sharedWith array with merged users
      updateObject.sharedWith = mergedSharedWith;
      // Update the search
      const updatedSearch = await EnterpriseSemanticSearch.findByIdAndUpdate(
        searchId,
        updateObject,
      );

      if (!updatedSearch) {
        throw new InternalServerError(
          'Failed to update search sharing settings',
        );
      }

      res.status(HTTP_STATUS.OK).json(updatedSearch);
    } catch (error: any) {
      logger.error('Error sharing search', {
        requestId,
        message: 'Error sharing search',
        error: error.message,
      });
      next(error);
    }
  };

export const unshareSearch =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const searchId = req.params.searchId;
    const startTime = Date.now();
    const { userIds } = req.body;
    try {
      if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
        throw new BadRequestError('userIds is required and must be a non-empty array');
      }

      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const filter = buildFilter(req, orgId, userId, searchId);

      logger.debug('Attempting to unshare search', {
        requestId,
        searchId,
        userId,
        timestamp: new Date().toISOString(),
      });

      const search = await EnterpriseSemanticSearch.findOne(filter);
      if (!search) {
        throw new NotFoundError('Search Id not found or unauthorized');
      }

      // Validate all user IDs
      await Promise.all(
        userIds.map(async (id: string) => {
          if (!mongoose.Types.ObjectId.isValid(id)) {
            throw new BadRequestError(`Invalid user ID format: ${id}`);
          }
          try {
            const iamCommand = new IAMServiceCommand({
              uri: `${appConfig.iamBackend}/api/v1/users/${encodeURIComponent(id)}`,
              method: HttpMethod.GET,
              headers: req.headers as Record<string, string>,
            });
            const userResponse = await iamCommand.execute();
            if (userResponse && userResponse.statusCode !== 200) {
              throw new BadRequestError(`User not found: ${id}`);
            }
          } catch (exception) {
            logger.debug(`User does not exist: ${id}`, {
              requestId,
            });
            throw new BadRequestError(`User not found: ${id}`);
          }
        }),
      );

      // Get existing shared users
      const existingSharedWith = persistableSharedWith(search.sharedWith);

      // Remove specified users from sharedWith array
      const updatedSharedWith = existingSharedWith.filter(
        (share) => {
          const id = sharedWithUserId(share);
          return id === undefined || !userIds.includes(id);
        },
      );

      // Prepare update object
      const updateObject: Partial<IEnterpriseSemanticSearch> = {
        sharedWith: updatedSharedWith,
      };

      // If no more shares exist, update isShared and remove shareLink
      if (updatedSharedWith.length === 0) {
        updateObject.isShared = false;
        updateObject.shareLink = undefined;
      }

      const updatedSearch = await EnterpriseSemanticSearch.findByIdAndUpdate(
        searchId,
        updateObject,
      );

      if (!updatedSearch) {
        throw new InternalServerError(
          'Failed to update search sharing settings',
        );
      }

      // Prepare response
      const response = {
        id: updatedSearch._id,
        isShared: updatedSearch.isShared,
        shareLink: updatedSearch.shareLink,
        sharedWith: updatedSearch.sharedWith,
        unsharedUsers: userIds,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      };

      res.status(200).json(response);
    } catch (error: any) {
      logger.error('Error un-sharing search', {
        requestId,
        message: 'Error un-sharing search',
        error: error.message,
      });
      next(error);
    }
  };

export const archiveSearch = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const searchId = req.params.searchId;
  const startTime = Date.now();
  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }
    const filter = buildFilter(req, orgId, userId, searchId);

    logger.debug('Attempting to archive search', {
      requestId,
      message: 'Attempting to archive search',
      searchId,
      userId,
      timestamp: new Date().toISOString(),
    });

    const search = await EnterpriseSemanticSearch.findOne(filter);
    if (!search) {
      throw new NotFoundError('Search Id not found or no archive permission');
    }

    if (search.isArchived) {
      throw new BadRequestError('Search already archived');
    }

    const updatedSearch: IEnterpriseSemanticSearch | null =
      await EnterpriseSemanticSearch.findByIdAndUpdate(searchId, {
        $set: {
          isArchived: true,
          archivedBy: userId,
          lastActivityAt: Date.now(),
        },
      }).exec();

    if (!updatedSearch) {
      throw new InternalServerError('Failed to archive search');
    }

    // Prepare response
    const response = {
      id: updatedSearch?._id,
      status: 'archived',
      archivedBy: userId,
      archivedAt: updatedSearch?.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Search archived successfully', {
      requestId,
      searchId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error archiving search', {
      requestId,
      message: 'Error archiving search',
      error: error.message,
    });
    next(error);
  }
};

export const unarchiveSearch = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const searchId = req.params.searchId;
  const startTime = Date.now();
  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }
    const filter = {
      ...buildFilter(req, orgId, userId, searchId),
      isArchived: true,
    };

    logger.debug('Attempting to unarchive conversation', {
      requestId,
      message: 'Attempting to unarchive search',
      searchId,
      userId,
      timestamp: new Date().toISOString(),
    });
    const search = await EnterpriseSemanticSearch.findOne(filter);
    if (!search) {
      throw new NotFoundError('Search Id not found or no unarchive permission');
    }

    if (!search.isArchived) {
      throw new BadRequestError('Search is not archived');
    }

    const updatedSearch: IEnterpriseSemanticSearch | null =
      await EnterpriseSemanticSearch.findOneAndUpdate(filter, {
        $set: {
          isArchived: false,
          archivedBy: null,
          lastActivityAt: Date.now(),
        },
      }).exec();

    if (!updatedSearch) {
      throw new InternalServerError('Failed to unarchive search');
    }

    // Prepare response
    const response = {
      id: updatedSearch?._id,
      status: 'unarchived',
      unarchivedBy: userId,
      unarchivedAt: updatedSearch?.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Search un-archived successfully', {
      requestId,
      searchId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error unarchiving search', {
      requestId,
      message: 'Error unarchiving search',
      error: error.message,
    });
    next(error);
  }
};

export const deleteSearchHistory = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }
    const filter = buildFilter(req, orgId, userId);

    logger.debug('Attempting to delete search history', {
      requestId,
      timestamp: new Date().toISOString(),
    });

    const searches = await EnterpriseSemanticSearch.find(filter).lean().exec();

    if (!searches.length) {
      throw new NotFoundError('Search history not found');
    }

    const citationIds = searches.flatMap((search) => search.citationIds);
    await EnterpriseSemanticSearch.deleteMany(filter);
    await Citation.deleteMany({ _id: { $in: citationIds } });

    res.status(HTTP_STATUS.OK).json({
      message: 'Search history deleted successfully',
    });
  } catch (error: any) {
    logger.error('Error deleting search history', {
      requestId,
      message: 'Error deleting search history',
      error: error.message,
    });
    next(error);
  }
};

/////////////////////// AGENT ///////////////////////

const agentTokenIssuers = new WeakMap<AppConfig, JwtServiceTokenIssuer>();
function agentTokenIssuerFor(appConfig: AppConfig): JwtServiceTokenIssuer {
  let issuer = agentTokenIssuers.get(appConfig);
  if (!issuer) {
    issuer = new JwtServiceTokenIssuer(
      new AuthTokenService(appConfig.jwtSecret, appConfig.scopedJwtSecret),
    );
    agentTokenIssuers.set(appConfig, issuer);
  }
  return issuer;
}

/** Creates an agent. With a `draftRef` the draft is verified here and Python is called with a token bound to it. */
export const createAgent =
  (appConfig: AppConfig, draftRefs?: AgentDraftRefResolver) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;

      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }

      const { draftRef, ...spec } = (req.body ?? {}) as Record<string, unknown> & {
        draftRef?: DraftRef;
      };
      let aiCommandOptions: AICommandOptions;
      let verified: VerifiedDraftRef | undefined;
      if (draftRef !== undefined) {
        if (!draftRefs) {
          throw new ConversationNotFoundError();
        }
        verified = await draftRefs.resolve(req, draftRef);
        const token = agentTokenIssuerFor(appConfig).issue(
          {
            userId,
            orgId,
            scopes: [TokenScopes.AGENT_CREATE_FROM_CHAT],
            conversationId: verified.conversationId,
            messageId: verified.messageId,
          },
          '1m',
        );
        aiCommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/internal/create-from-chat`,
          method: HttpMethod.POST,
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: {
            ...spec,
            createdVia: 'chat',
            sourceConversationId: verified.conversationId,
            sourceMessageId: verified.messageId,
          },
        };
      } else {
        aiCommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/create`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: spec,
        };
      }
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw mapAgentHandleError(aiResponse) ??
          handleBackendError(aiResponse, 'Create Agent');
      }
      const agent = aiResponse.data;
      invalidateAgentCaches(
        callerIdentityOf(req),
        (agent as { agent?: { _key?: string } } | undefined)?.agent?._key,
      );
      if (verified !== undefined) {
        const created = (agent as { agent?: { _key?: string; handle?: string } } | undefined)
          ?.agent;
        if (created?._key && draftRefs) {
          await draftRefs
            .markCreated(verified, { agentKey: created._key, handle: created.handle ?? '' })
            .catch((error: unknown) =>
              logger.warn('Could not mark the agent draft as created', {
                requestId,
                error: error instanceof Error ? error.message : String(error),
              }),
            );
        }
        logger.info('agent.audit', {
          action: 'create',
          agentKey: created?._key,
          actor: userId,
          createdVia: 'chat',
          sourceConversationId: verified.conversationId,
        });
      }
      res.status(HTTP_STATUS.CREATED).json(agent);
    } catch (error: any) {
      logger.error('Error creating agent', {
        requestId,
        message: 'Error creating agent',
        error: error.message,
      });
      const backendError = handleBackendError(error, 'Create Agent');
      next(backendError);
    }
  };

/** Whether `handle` is free in the caller's org, with the next free one when it is not. */
export const checkAgentHandle =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const aiResponse = await new AIServiceCommand({
        uri: `${appConfig.aiBackend}/api/v1/agent/handle-availability?handle=${encodeURIComponent(String(req.query.handle ?? ''))}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      }).execute();
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Check Agent Handle');
      }
      res.status(HTTP_STATUS.OK).json(aiResponse.data);
    } catch (error: any) {
      next(handleBackendError(error, 'Check Agent Handle'));
    }
  };

export const getAgent =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const agentKey = req.params.agentKey as string;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey)}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Get Agent');
      }
      const responsePayload = aiResponse.data as Record<string, unknown>;
      if (
        responsePayload &&
        typeof responsePayload === 'object' &&
        responsePayload.agent &&
        typeof responsePayload.agent === 'object' &&
        !Array.isArray(responsePayload.agent)
      ) {
        const normalizedAgent = { ...(responsePayload.agent as Record<string, unknown>) };
        delete normalizedAgent.id;
        res.status(HTTP_STATUS.OK).json({
          ...responsePayload,
          agent: normalizedAgent,
        });
        return;
      }
      res.status(HTTP_STATUS.OK).json(responsePayload);
    } catch (error: any) {
      logger.error('Error getting agent', {
        requestId,
        message: 'Error getting agent',
        error: error.message,
      });
      const backendError = handleBackendError(error, 'Get Agent');
      next(backendError);
    }
  };

export const getWebSearchProviderUsage =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      const { provider } = req.params;
      if (!provider) {
        throw new BadRequestError('Provider is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/agent/web-search-usage/${encodeURIComponent(provider)}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse || aiResponse.statusCode !== 200) {
        res.status(HTTP_STATUS.OK).json({ success: true, agents: [] });
        return;
      }
      res.status(HTTP_STATUS.OK).json(aiResponse.data);
    } catch (error: any) {
      logger.error('Error checking web search provider usage', {
        requestId,
        message: 'Error checking web search provider usage',
        error: error.message,
      });
      const backendError = handleBackendError(error, 'Web Search Provider Usage');
      next(backendError);
    }
  };

export const getModelUsage =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      const { model_key: modelKey } = req.params;
      if (!modelKey) {
        throw new BadRequestError('Model key is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/agent/model-usage/${encodeURIComponent(modelKey)}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse || aiResponse.statusCode !== 200) {
        res.status(HTTP_STATUS.OK).json({ success: true, agents: [] });
        return;
      }
      res.status(HTTP_STATUS.OK).json(aiResponse.data);
    } catch (error: any) {
      logger.error('Error checking AI model usage', {
        requestId,
        message: 'Error checking AI model usage',
        error: error.message,
      });
      if (error instanceof BadRequestError) {
        next(error);
        return;
      }
      const backendError = handleBackendError(error, 'AI Model Usage');
      next(backendError);
    }
  };

export const listAgents =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      // Forward pagination/search params
      const { page, limit, search, sort_by, sort_order } = req.query as Record<string, string | undefined>;
      const queryParams = new URLSearchParams();
      if (page) queryParams.set('page', page);
      if (limit) queryParams.set('limit', limit);
      if (search) queryParams.set('search', search);
      if (sort_by) queryParams.set('sort_by', sort_by);
      if (sort_order) queryParams.set('sort_order', sort_order);
      const qs = queryParams.toString();
      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/agent/${qs ? `?${qs}` : ''}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse || aiResponse.statusCode !== 200) {
        res.status(HTTP_STATUS.OK).json({ success: true, agents: [], pagination: { currentPage: Number(page ?? 1), limit: Number(limit ?? 20), totalItems: 0, totalPages: 0, hasNext: false, hasPrev: false } });
        return;
      }
      const responsePayload = aiResponse.data as Record<string, unknown>;
      if (
        responsePayload &&
        typeof responsePayload === 'object' &&
        Array.isArray(responsePayload.agents)
      ) {
        const agents = responsePayload.agents.map((agent) =>
          omitId(agent),
        ) as Array<Record<string, unknown>>;
        res.status(HTTP_STATUS.OK).json({
          ...responsePayload,
          agents,
        });
        return;
      }
      res.status(HTTP_STATUS.OK).json(aiResponse.data);
    } catch (error: any) {
      logger.error('Error getting agents', {
        requestId,
        message: 'Error getting agents',
        error: error.message,
      });
      const backendError = handleBackendError(error, 'List Agents');
      next(backendError);
    }
  };

export const updateAgent =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const agentKey = req.params.agentKey as string;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey)}`,
        method: HttpMethod.PUT,
        body: req.body,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw mapAgentHandleError(aiResponse) ??
          handleBackendError(aiResponse, 'Update Agent');
      }
      const agent = aiResponse.data;
      invalidateAgentCaches(callerIdentityOf(req), agentKey);
      res.status(HTTP_STATUS.OK).json(agent);
    } catch (error: any) {
      logger.error('Error updating agent', {
        requestId,
        message: 'Error updating agent',
        error: error.message,
      });
      const backendError = handleBackendError(error, 'Update Agent');
      next(backendError);
    }
  };

export const deleteAgent =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const agentKey = req.params.agentKey as string;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey)}`,
        method: HttpMethod.DELETE,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Delete Agent');
      }
      const agent = aiResponse.data;
      invalidateAgentCaches(callerIdentityOf(req), agentKey);
      res.status(HTTP_STATUS.OK).json(agent);
    } catch (error: any) {
      logger.error('Error deleting agent', {
        requestId,
        message: 'Error deleting agent',
        error: error.message,
      });
      const backendError = handleBackendError(error, 'Delete Agent');
      next(backendError);
    }
  };

export const streamAgentConversation = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): ReturnType<typeof firstSendStream> =>
  firstSendStream(appConfig, deps, 'agent');

export const streamAgentConversationInternal = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  keyValueStoreService?: KeyValueStoreService,
) => asInternal(streamAgentConversation(appConfig, deps), appConfig, keyValueStoreService);

export const addMessageStreamToAgentConversation = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): ReturnType<typeof followUpStream> =>
  followUpStream(appConfig, deps, 'agent');

export const addMessageStreamToAgentConversationInternal = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  keyValueStoreService?: KeyValueStoreService,
) => asInternal(addMessageStreamToAgentConversation(appConfig, deps), appConfig, keyValueStoreService);

export const regenerateAgentAnswers =
  (appConfig: AppConfig, deps: ConversationTurnDeps) =>
  async (req: AuthenticatedUserRequest, res: Response) => {
    await regenerateAnswersInternal(appConfig, deps, req, res, {
      isAgentSession: true,
      buildQueryFilter: (_conversationId, orgId, _userId, agentKey) => ({
        _id: conversationGrantOf(req).session._id,
        agentKey,
        orgId,
        isDeleted: false,
        ...ONLY_AGENT,
      }),
      buildAIEndpoint: (appConfig, agentKey) =>
        `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey as string)}/chat/stream`,
    });
  };

/**
 * Agent-conversation counterpart of `cancelConversationStream`: forwards to
 * the SAME Python `/chat/cancel` endpoint (registry is keyed by `runId`
 * alone, not by which route created it).
 */
export const cancelAgentConversationStream =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const { conversationId, agentKey } = req.params;
    const { runId } = req.body;
    try {
      const grantedId = conversationGrantOf(req).session._id;
      const run = await runIdToCancel(req, runId as string);
      if (run === undefined) {
        res.status(HTTP_STATUS.OK).json({ cancelled: false });
        return;
      }
      const aiResponse = await runCancellerFor(appConfig).cancel(
        req,
        String(grantedId),
        run,
      );
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Cancel Agent Conversation Stream');
      }
      res.status(HTTP_STATUS.OK).json(aiResponse.data);
    } catch (error: any) {
      logger.error('Error cancelling agent conversation stream', {
        requestId,
        conversationId,
        agentKey,
        runId,
        error: error.message,
      });
      const backendError = handleBackendError(error, 'Cancel Agent Conversation Stream');
      next(backendError);
    }
  };

export const getAllAgentConversations = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }
    const { conversationId, agentKey } = req.params;
    logger.debug('Fetching conversations', {
      requestId,
      message: 'Fetching conversations',
      userId,
      agentKey,
      conversationId: req.params.conversationId,
      query: req.query,
    });

    const { skip, limit, page } = getPaginationParams(req);
    const ctx = conversationContextOf(req);
    const { filter, queryConstraints } = await buildListQuery(req, {
      orgId,
      listFilter: listFilterOf(req),
      stateFilter: {
        // Sidebar / chat list: omit archived threads (archived view uses a dedicated route).
        isArchived: { $ne: true },
        ...(conversationId && {
          _id: new mongoose.Types.ObjectId(conversationId as string),
        }),
      },
      formatSpecifiers: true,
    });
    const sortOptions = buildSortOptions(req);

    // sharedWith Me Conversation
    const sharedWithMeFilter = composeListFilter(sharedListFilterOf(req), {
      isArchived: { $ne: true },
      ...(req.query.status && { status: req.query.status }),
    });

    // Execute query
    const [conversations, totalCount, sharedWithMeConversations] =
      await Promise.all([
        ChatSession.find(filter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select(listSelect(ctx))
          .lean()
          .exec(),
        ChatSession.countDocuments(filter),
        ChatSession.find(sharedWithMeFilter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select(listSelect(ctx))
          .lean()
          .exec(),
      ]);

    const views = await listAccessViews(ctx, [
      ...conversations,
      ...sharedWithMeConversations,
    ]);
    // One read-state query for both lists of the page.
    const decorate = await sharedListDecorator(ctx, [
      ...conversations,
      ...sharedWithMeConversations,
    ]);
    const processedConversations = conversations.map((conversation: any) =>
      decorate(
        addComputedFields(
          redactRecipients(ctx, conversation) as IAgentConversation,
          userId,
          views.get(String(conversation._id)),
        ),
      ),
    );
    const processedSharedWithMeConversations = sharedWithMeConversations.map(
      (sharedWithMeConversation: any) =>
        decorate(
          addComputedFields(
            withoutSharedWith(sharedWithMeConversation) as IAgentConversation,
            userId,
            views.get(String(sharedWithMeConversation._id)),
          ),
        ),
    );

    // Build response metadata
    const response = {
      conversations: processedConversations,
      sharedWithMeConversations: processedSharedWithMeConversations,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(queryConstraints, req.query),
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Successfully fetched conversations', {
      requestId,
      count: conversations.length,
      totalCount,
      duration: Date.now() - startTime,
    });

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error fetching conversations', {
      requestId,
      message: 'Error fetching conversations',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const getAgentConversationById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  const { conversationId, agentKey } = req.params;
  try {
    const { sortBy = 'createdAt', sortOrder = 'desc', ...query } = req.query;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Fetching conversation by ID', {
      requestId,
      conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });
    // Get pagination parameters
    const { page, limit } = getPaginationParams(req);

    const grantedId = conversationGrantOf(req).session._id;

    // Build message filter
    const messageFilter = buildMessageFilter(req);

    // Get sort options for messages
    const messageSortOptions = buildMessageSortOptions(
      sortBy as string,
      sortOrder as string,
    );

    const session = await ChatSession.findOne({
      _id: grantedId,
      agentKey,
      orgId,
      isDeleted: false,
      ...ONLY_AGENT,
    })
      .select({
        title: 1,
        initiator: 1,
        createdAt: 1,
        isShared: 1,
        sharedWith: 1,
        status: 1,
        failReason: 1,
        modelInfo: 1,
        projectId: 1,
        projectVisibility: 1,
      })
      .lean()
      .exec();

    if (!session) {
      throw new NotFoundError('Conversation not found');
    }

    const sessionId = session._id as unknown as Types.ObjectId;

    const totalMessages = await ChatSessionMessage.countDocuments({
      sessionId,
    });

    const { skip, limit: effectiveLimit } = olderMessagesWindow(
      totalMessages,
      page,
      limit,
    );

    const messages = await getMessages(sessionId, {
      skip,
      limit: effectiveLimit,
      populateCitations: true,
    });

    const conversationWithMessages = attachMessages(session, messages, userId);

    // Sort messages using existing helper
    const sortedMessages = sortMessages(
      (conversationWithMessages?.messages ||
        []) as unknown as IMessageDocument[],
      messageSortOptions as { field: keyof IMessage },
    );

    // Build conversation response using existing helper
    const baseResponse = buildConversationResponse(
      conversationWithMessages as unknown as IChatSessionDocument,
      userId,
      {
        page,
        limit,
        skip,
        totalMessages,
        hasNextPage: skip > 0,
        hasPrevPage: skip + effectiveLimit < totalMessages,
      },
      sortedMessages,
      accessViewOf(req),
    );

    const withFields = await withCollabMessageFields(
      baseResponse,
      messages,
      defaultUsers,
      orgId,
      conversationGrantOf(req).session.userId.toString(),
      collabEnabledFor(req),
    );
    const conversationResponse = await withRespondingAgents(
      withFields,
      callerIdentityOf(req),
      agentProfiles(),
    );

    // Build filters metadata using existing helper
    const filtersMetadata = buildFiltersMetadata(
      messageFilter,
      query,
      messageSortOptions,
    );

    // Prepare response using existing format
    const response = {
      conversation: conversationResponse,
      filters: filtersMetadata,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
        conversationId,
        messageCount: totalMessages,
      },
    };

    logger.debug('Conversation fetched successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error fetching conversation', {
      requestId,
      conversationId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });

    next(error);
  }
};

export const deleteAgentConversationById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  const { conversationId, agentKey } = req.params;
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    const conversation = await deleteAgentConversation(
      conversationGrantOf(req).session._id,
      agentKey as string,
      userId as string,
      orgId as string,
    );

    if (conversation && collabEnabledFor(req)) {
      await conversationEventProducers().conversationDeleted(
        conversationGrantOf(req),
      );
    }

    res.status(200).json({
      message: 'Conversation deleted successfully',
      conversation: conversation && withoutErrorStacks(conversation.toJSON()),
    });
  } catch (error: any) {
      logger.error('Error deleting conversation', {
      requestId,
      conversationId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const archiveAgentConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId, agentKey } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const grantedId = conversationGrantOf(req).session._id;
    const collab = conversationContextOf(req).collab === true;

    logger.debug('Attempting to archive agent conversation', {
      requestId,
      message: 'Attempting to archive agent conversation',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performArchive(s?: ClientSession | null) {
      const conversation = await ChatSession.findOne(
        {
          _id: grantedId,
          orgId,
          agentKey,
          isDeleted: false,
          ...ONLY_AGENT,
        },
        '+archivedFor',
        { session: s },
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      const state = archiveStateOf(conversation, `${userId}`, collab);
      if (state.archived) {
        throw new BadRequestError('Agent conversation already archived');
      }

      const updated = await ChatSession.findOneAndUpdate(
        { _id: conversationId, ...ONLY_AGENT },
        state.perUser
          ? perUserArchiveUpdate('archive', conversation, `${userId}`)
          : {
              $set: {
                isArchived: true,
                archivedBy: userId,
                lastActivityAt: Date.now(),
              },
            },
        { new: true, session: s, runValidators: true },
      ).exec();

      if (!updated) {
        throw new InternalServerError('Failed to archive agent conversation');
      }
      return updated;
    }

    let updated: any = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updated = await performArchive(session);
      await session.commitTransaction();
    } else {
      updated = await performArchive();
    }

    logger.debug('Agent conversation archived successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      id: updated._id,
      status: 'archived',
      archivedBy: userId,
      archivedAt: updated.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    });
  } catch (error: any) {
    logger.error('Error archiving agent conversation', {
      requestId,
      message: 'Error archiving agent conversation',
      error: error.message,
      stack: error.stack,
    });
    next(error);
  } finally {
    if (session) {
      await session.endSession();
      session = null;
    }
  }
};

export const unarchiveAgentConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId, agentKey } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const grantedId = conversationGrantOf(req).session._id;
    const collab = conversationContextOf(req).collab === true;

    logger.debug('Attempting to unarchive agent conversation', {
      requestId,
      message: 'Attempting to unarchive agent conversation',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performUnarchive(s?: ClientSession | null) {
      const conversation = await ChatSession.findOne(
        {
          _id: grantedId,
          orgId,
          agentKey,
          isDeleted: false,
          ...ONLY_AGENT,
        },
        '+archivedFor',
        { session: s },
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      const state = archiveStateOf(conversation, `${userId}`, collab);
      if (!state.archived) {
        throw new BadRequestError('Agent conversation is not archived');
      }

      const updated = await ChatSession.findOneAndUpdate(
        { _id: conversationId, ...ONLY_AGENT },
        state.perUser
          ? perUserArchiveUpdate('unarchive', conversation, `${userId}`)
          : {
              $set: {
                isArchived: false,
                archivedBy: null,
                lastActivityAt: Date.now(),
              },
            },
        { new: true, session: s, runValidators: true },
      ).exec();

      if (!updated) {
        throw new InternalServerError('Failed to unarchive agent conversation');
      }
      return updated;
    }

    let updated: any = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updated = await performUnarchive(session);
      await session.commitTransaction();
    } else {
      updated = await performUnarchive();
    }

    logger.debug('Agent conversation unarchived successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      id: updated._id,
      status: 'unarchived',
      unarchivedBy: userId,
      unarchivedAt: updated.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    });
  } catch (error: any) {
    logger.error('Error unarchiving agent conversation', {
      requestId,
      message: 'Error unarchiving agent conversation',
      error: error.message,
      stack: error.stack,
    });
    next(error);
  } finally {
    if (session) {
      await session.endSession();
      session = null;
    }
  }
};

export const listAllArchivesAgentConversation = () =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { agentKey } = req.params;

    logger.debug('Fetching all archived agent conversations', {
      requestId,
      userId,
      agentKey,
    });

    const { skip, limit, page } = getPaginationParams(req);
    const ctx = conversationContextOf(req);
    const { filter, queryConstraints } = await buildListQuery(req, {
      orgId,
      listFilter: listFilterOf(req),
      stateFilter: archivedStateFilter(ctx),
      formatSpecifiers: true,
    });
    const sortOptions = buildSortOptions(req);

    const [conversations, totalCount] = await Promise.all([
      ChatSession.find(filter)
        .sort(sortOptions as any)
        .skip(skip)
        .limit(limit)
        .select('-__v')
        .lean()
        .exec(),
      ChatSession.countDocuments(filter),
    ]);

    const views = await listAccessViews(ctx, conversations);
    const processedConversations = conversations.map((conversation: any) => ({
      ...addComputedFields(
        conversation as IAgentConversation,
        userId,
        views.get(String(conversation._id)),
      ),
      archivedAt: conversation.updatedAt,
      archivedBy: conversation.archivedBy,
    }));

    logger.debug('Successfully fetched archived agent conversations', {
      requestId,
      count: conversations.length,
      totalCount,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      conversations: processedConversations,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(queryConstraints, req.query),
      summary: {
        totalArchived: totalCount,
        oldestArchive: processedConversations[0]?.archivedAt,
        newestArchive: processedConversations[processedConversations.length - 1]?.archivedAt,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    });
  } catch (error: any) {
    logger.error('Error fetching archived agent conversations', {
      requestId,
      message: 'Error fetching archived agent conversations',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
  };

export const updateAgentConversationTitle = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const { conversationId, agentKey } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to update agent conversation title', {
      requestId,
      message: 'Attempting to update agent conversation title',
      conversationId,
      agentKey,
      userId,
      title: req.body.title,
      timestamp: new Date().toISOString(),
    });

    const conversation = await ChatSession.findOne({
      _id: conversationGrantOf(req).session._id,
      orgId,
      agentKey,
      isDeleted: false,
      ...ONLY_AGENT,
    });

    if (!conversation) {
      throw new NotFoundError('Agent conversation not found');
    }

    const title = req.body.title?.trim();
    if (!title) {
      throw new BadRequestError('Title is required and cannot be empty');
    }

    conversation.title = title;
    await conversation.save();

    logger.debug('Agent conversation title updated successfully', {
      requestId,
      message: 'Agent conversation title updated successfully',
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      conversation: {
        ...withoutErrorStacks(conversation.toObject()),
        title: conversation.title,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    });
  } catch (error: any) {
    logger.error('Error updating agent conversation title', {
      requestId,
      message: 'Error updating agent conversation title',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const updateAgentFeedback = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { agentKey, conversationId, messageId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to update agent conversation feedback', {
      requestId,
      message: 'Attempting to update agent conversation feedback',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    const query = {
      _id: conversationGrantOf(req).session._id,
      orgId,
      agentKey,
      isDeleted: false,
      ...ONLY_AGENT,
    };

    let updatedConversation, feedbackEntry;
    if (rsAvailable) {
      session = await mongoose.startSession();
      ({ updatedConversation, feedbackEntry } = await session.withTransaction(
        () => performFeedbackUpdate({
          query, conversationId: conversationId!, messageId: messageId!,
          userId: userId!, body: req.body, userAgent: req.headers['user-agent'], session,
        }),
      ));
    } else {
      ({ updatedConversation, feedbackEntry } = await performFeedbackUpdate({
        query, conversationId: conversationId!, messageId: messageId!,
        userId: userId!, body: req.body, userAgent: req.headers['user-agent'],
      }));
    }

    logger.debug('Agent feedback updated successfully', {
      requestId,
      conversationId,
      messageId,
      duration: Date.now() - startTime,
    });

    const response = {
      conversationId: updatedConversation._id,
      messageId,
      feedback: feedbackEntry,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error updating agent conversation feedback', {
      requestId,
      message: 'Error updating agent conversation feedback',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  } finally {
    if (session) {
      await session.endSession();
      session = null;
    }
  }
};

/**
 * GET /api/v1/agents/conversations/show/archives
 * Returns archived agent conversations for the current user, grouped by agentKey.
 *
 * Supports agent-level pagination via `agentPage` and `agentLimit` query params
 * (defaults: page 1, limit {@link AGENT_ARCHIVES_INITIAL_AGENT_LIMIT}).
 *
 * Each group contains the first {@link AGENT_ARCHIVES_INITIAL_CHAT_LIMIT} conversations plus
 * a totalCount so the frontend can render a "More Chats" affordance without extra round-trips.
 *
 * Conversations whose agent instance is soft-deleted in the AI backend are omitted
 * (Mongo `agentKey` not in the user's deleted-agent list from the AI backend).
 */
export const listAllAgentsArchivedConversationsGrouped =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }

    const deletedAgentKeys = await fetchDeletedAgentKeysForUser(appConfig, req);

    // Agent-level pagination params
    const agentPage = Math.max(1, parseInt(req.query.agentPage as string, 10) || 1);
    const agentLimit = Math.min(
      100,
      Math.max(1, parseInt(req.query.agentLimit as string, 10) || AGENT_ARCHIVES_INITIAL_AGENT_LIMIT),
    );
    const agentSkip = (agentPage - 1) * agentLimit;

    logger.debug('Fetching all agents archived conversations (grouped)', {
      requestId,
      userId,
      agentPage,
      agentLimit,
    });

    // No agentKey on this route, which the shared access filter requires, so it stays owner-only.
    const ctx = conversationContextOf(req);
    const ownedArchives = {
      orgId: new mongoose.Types.ObjectId(`${orgId}`),
      userId: new mongoose.Types.ObjectId(`${userId}`),
      ...archivedStateFilter(ctx),
      isDeleted: false,
      ...ONLY_AGENT,
      ...(deletedAgentKeys !== null && {
        agentKey: { $nin: deletedAgentKeys },
      }),
    };
    const perUserArchives = ownArchivedClause(ctx);
    const filter = perUserArchives
      ? { $and: [ownedArchives, perUserArchives] }
      : ownedArchives;

    // Amazon DocumentDB does not support the $facet stage (see AWS aggregation compatibility).
    // Use a cheap count pipeline + the full data pipeline instead of $facet.
    const [countRows, rawGroups] = await Promise.all([
      ChatSession.aggregate<{ totalAgentCount: number }>([
        { $match: filter },
        { $group: { _id: '$agentKey' } },
        { $count: 'totalAgentCount' },
      ]),
      ChatSession.aggregate([
        { $match: filter },
        // Sort by lastActivityAt desc — must match the per-agent archive endpoint's default sort
        // so that the initial 5 chats here align with page 1 of the per-agent pagination
        { $sort: { lastActivityAt: -1 as const } },
        // Exclude heavy fields before grouping
        { $project: { __v: 0, messages: 0, nextSeq: 0, sessionType: 0 } },
        {
          $group: {
            _id: '$agentKey',
            conversations: { $push: '$$ROOT' },
            totalCount: { $sum: 1 },
            latestActivity: { $first: '$lastActivityAt' },
          },
        },
        // Sort agent groups by most recent activity
        { $sort: { latestActivity: -1 as const } },
        { $skip: agentSkip },
        { $limit: agentLimit },
        {
          $project: {
            agentKey: '$_id',
            conversations: { $slice: ['$conversations', AGENT_ARCHIVES_INITIAL_CHAT_LIMIT] },
            totalCount: 1,
          },
        },
      ]),
    ]);

    const totalAgentCount = countRows[0]?.totalAgentCount ?? 0;

    // Apply computed fields (isOwner, accessLevel) to sliced conversations
    const groups = rawGroups.map((g: any) => ({
      agentKey: g.agentKey,
      conversations: g.conversations.map((c: any) => ({
        ...addComputedFields(c as IAgentConversation, userId),
        archivedAt: c.updatedAt,
        archivedBy: c.archivedBy,
      })),
      pagination: buildPaginationMetadata(g.totalCount, 1, AGENT_ARCHIVES_INITIAL_CHAT_LIMIT),
    }));

    logger.debug('Successfully fetched grouped archived agent conversations', {
      requestId,
      agentGroupCount: groups.length,
      totalAgentCount,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      groups,
      agentPagination: {
        page: agentPage,
        limit: agentLimit,
        totalCount: totalAgentCount,
        totalPages: Math.ceil(totalAgentCount / agentLimit),
        hasNextPage: agentPage * agentLimit < totalAgentCount,
        hasPrevPage: agentPage > 1,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    });
  } catch (error: any) {
    logger.error('Error fetching grouped archived agent conversations', {
      requestId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

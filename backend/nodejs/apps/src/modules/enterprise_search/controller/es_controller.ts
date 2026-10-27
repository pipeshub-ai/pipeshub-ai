import {
  buildMessageSortOptions,
  markConversationFailed,
  saveCompleteAgentConversation,
  saveCompleteConversation,
  markAgentConversationFailed,
  buildAgentConversationFilter,
  buildAgentSharedWithMeFilter,
  deleteAgentConversation,
  replaceMessageWithError,
  initializeSSEResponse,
  sendSSEErrorEvent,
  sendSSECompleteEvent,
  handleRegenerationStreamData,
  handleRegenerationSuccess,
  updateUserQueryContent,
  deleteMessagesById,
  hasAskUserQuestionTool,
  isClassifiedFailureAnswer,
  staleAskUserQuestionToolCallIds,
  handleRegenerationError,
} from './../utils/utils';
import {
  assertRasterImage,
  RasterImageFormat,
  sharp,
} from '../../../libs/utils/image.utils';
import { Response, NextFunction } from 'express';
import type { Readable } from 'stream';
import mongoose, { ClientSession, QueryFilter, Types } from 'mongoose';
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
import { logLevelFor } from '../../../libs/utils/error.middleware.utils';
import {
  handleBackendError,
  SERVICE_UNAVAILABLE_MESSAGE,
} from '../../../libs/errors/backend-error';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  AIServiceResponse,
  IAgentConversation,
  IAIModel,
  IAIResponse,
  IChatAttachmentRef,
  IChatSession,
  IChatSessionDocument,
  IMessage,
  IMessageCitation,
  IMessageDocument,
  IMessagePart,
} from '../types/conversation.interfaces';
import { IConversation } from '../types/conversation.interfaces';
import { ChatSession } from '../schema/chat.session.schema';
import { ChatSessionMessage } from '../schema/chat.session.message.schema';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  addComputedFields,
  attachSharedBy,
  attachSharedByIfRecipient,
  withoutOwnerOnlyFields,
  findOrgUserIds,
  assignAiModelField,
  buildAIResponseMessage,
  buildFiltersMetadata,
  buildConversationResponse,
  buildFilter,
  buildMessageFilter,
  buildPaginationMetadata,
  buildSortOptions,
  buildUserQueryMessage,
  extractModelInfo,
  formatPreviousConversations,
  StageTimer,
  getPaginationParams,
  olderMessagesWindow,
  sortMessages,
  attachPopulatedCitations,
  appendMessages,
  getMessages,
  attachMessages,
  appendMessageFeedback,
  findSessionIdsMatchingContent,
  validateAndEscapeSearch,
  withoutErrorStacks,
  recordClassifiedFailureOnSession,
  savePartialConversation,
  keepRunAlive,
  releaseRun,
  reserveRun,
  type RunReservation,
} from '../utils/utils';
import {
  redactMessagesFor,
  redactSearchesFor,
  viewerOf,
} from '../utils/viewer-redaction';
import {
  attachUpstreamAbort,
  runGuarded,
  StreamedContentAccumulator,
} from '../utils/stream-lifecycle';
import {
  AGUIEventType,
  AGUI_PROTOCOL,
  aguiErrorCodeFromPayload,
  frameAGUI,
  isAGUI,
  resolveProtocol,
} from '../utils/agui';
import { notifyChatShared } from '../utils/share-notification';
import { IAMServiceCommand } from '../../../libs/commands/iam/iam.service.command';
import EnterpriseSemanticSearch, {
  IEnterpriseSemanticSearch,
} from '../schema/search.schema';
import { AppConfig } from '../../tokens_manager/config/config';
import { deleteConversationArtifacts } from '../../artifacts/services/conversation-artifacts.service';
import Citation, {
  AiSearchResponse,
  ICitation,
} from '../schema/citation.schema';
import {
  CONVERSATION_STATUS,
  EXCLUDE_AGENT,
  ONLY_AGENT,
} from '../constants/constants';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { validateNoFormatSpecifiers } from '../../../utils/xss-sanitization';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import {
  applyProjectScope,
  loadProjectForSession,
  resolveProjectLink,
} from '../utils/project-context';
import {
  buildAiRequestLogMetadata,
  buildFilterLogMetadata,
} from '../utils/ai-request-log';
import {
  CHAT_ERROR_MESSAGES,
  userFacingChatError,
} from '../utils/chat-error-messages';
import {
  copyArtifactsForFork,
  copySessionUpToMessage,
  ForkChatSessionResult,
  ForkSessionFields,
  resolveForkProjectId,
} from '../utils/fork-conversation';
import { ArtifactCopier, ArtifactCopyOutcome } from '../utils/fork-artifacts';
import { executeConnectorCommand } from '../../tokens_manager/utils/connector.utils';
import { ProjectService } from '../../projects/services/project.service';
import {
  assignAgentCapabilitiesToPayload,
  assignToolsToPayload,
  buildAiChatRequest,
  parseChatMode,
} from '../utils/ai-chat-payload';
import { hydrateScopedRequestAsUser } from '../utils/scoped-request';
const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

/** Node hand-builds the AI request body, so the protocol has to ride in it: a header alone never reaches Python. */
const withStreamProtocol = (
  payload: Record<string, unknown>,
  protocol: ReturnType<typeof resolveProtocol>,
): Record<string, unknown> =>
  isAGUI(protocol) ? { ...payload, protocol: AGUI_PROTOCOL } : payload;
const rsAvailable = process.env.REPLICA_SET_AVAILABLE === 'true';

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
  assignRetrievalContextToPayload,
  assignToolsToPayload,
  parseChatMode,
} from '../utils/ai-chat-payload';

export {
  addMessage,
  addMessageToAgentConversation,
  createAgentConversation,
  createConversation,
} from './non-streaming-chat.controller';

export {
  checkServiceAccountAccess,
  hydrateScopedRequestAsUser,
  stableObjectIdHexForExternalEmail,
} from '../utils/scoped-request';

export { handleBackendError };

// Common helper to start AI streams with consistent error mapping and logging
export const startAIStream = async (
  options: AICommandOptions,
  operation: string,
  logContext: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<Readable | undefined> => {
  const aiServiceCommand = new AIServiceCommand(options);
  try {
    return await aiServiceCommand.executeStream(signal);
  } catch (error: unknown) {
    // The caller's own cap abandoned the open (see `attachUpstreamAbort`): it already finalized the run.
    if (signal?.aborted) {
      logger.info('AI service stream open abandoned', logContext);
      return undefined;
    }
    // handleBackendError logs the cause; the request id is on every log line.
    throw handleBackendError(error, operation);
  }
};

const RASTER_IMAGE_MIMES = new Set([
  'image/jpeg',
  'image/jpg',
  'image/png',
  'image/webp',
]);
// A decode allocates by pixel count, not file size: a 5 MiB PNG can hold
// hundreds of megapixels. 50 MP admits a 48 MP camera photo (8000 × 6000).
const MAX_ATTACHMENT_IMAGE_PIXELS = 50_000_000;
// The image types an attachment can be stored as, by what its bytes are.
const ATTACHMENT_MIME_BY_FORMAT: Partial<Record<RasterImageFormat, string>> = {
  jpeg: 'image/jpeg',
  png: 'image/png',
};

export interface NormalizedAttachmentFile {
  fileName: string;
  mimeType: string;
  size: number;
  buffer: Buffer;
}

/**
 * Rejects an image that does not decode as a raster or is over the pixel cap,
 * and forwards every file's bytes exactly as uploaded: the stored file is what
 * downloads, code and other apps receive, so it stays the user's original. The
 * query service shrinks only the copy the model reads.
 */
export const validateAttachmentFile = async (
  file: Express.Multer.File,
): Promise<NormalizedAttachmentFile> => {
  const passthrough: NormalizedAttachmentFile = {
    fileName: file.originalname,
    mimeType: file.mimetype,
    size: file.size,
    buffer: file.buffer,
  };
  if (!RASTER_IMAGE_MIMES.has((file.mimetype || '').toLowerCase())) {
    return passthrough;
  }

  // Magic bytes reject SVG, but they also accept a bare PNG signature.
  const invalidImageMessage =
    `${file.originalname} is not a valid JPEG, PNG or WebP image`;
  const format = assertRasterImage(
    file.buffer,
    ['jpeg', 'png', 'webp'],
    invalidImageMessage,
  );
  let pixels: number;
  try {
    const { width, height } = await sharp(file.buffer).metadata();
    pixels = width * height;
  } catch {
    throw new BadRequestError(invalidImageMessage);
  }
  if (pixels > MAX_ATTACHMENT_IMAGE_PIXELS) {
    throw new BadRequestError(
      `${file.originalname} is too large to attach: images can be at most 50 megapixels`,
    );
  }
  try {
    await sharp(file.buffer, { limitInputPixels: MAX_ATTACHMENT_IMAGE_PIXELS })
      .raw()
      .toBuffer();
  } catch {
    throw new BadRequestError(invalidImageMessage);
  }
  // A browser labels a file by its name, so a renamed screenshot arrives as
  // the wrong type: store the type its bytes are. A WebP keeps its label:
  // WebP is not an attachment type, so relabelling it would refuse an upload
  // that works today.
  const actualMime = ATTACHMENT_MIME_BY_FORMAT[format];
  return actualMime ? { ...passthrough, mimeType: actualMime } : passthrough;
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
        files.map((file) => validateAttachmentFile(file)),
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
        // Every attempt stores the files as new records, so a retry after a
        // slow first attempt leaves duplicates the user never sees.
        maxAttempts: 1,
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
        files.map((file) => validateAttachmentFile(file)),
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
        // Not retried, for the same reason as uploadChatAttachments.
        maxAttempts: 1,
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

export const streamChat =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const timer = new StageTimer();
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    let session: ClientSession | null = null;
    let savedConversation: IChatSessionDocument | null = null;

    const modelInfo = extractModelInfo(req.body);
    const protocol = resolveProtocol(
      req.body as Record<string, unknown>,
      req.query as Record<string, unknown>,
    );

    if (!req.body.query) {
      throw new BadRequestError('Query is required');
    }

    try {
      // Set SSE headers
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'X-Accel-Buffering': 'no',
      });

      // Create initial conversation record (before `connected` so the client
      // can link the stream to a real conversationId for URL/sidebar/parallel tabs)
      const userQueryMessage = buildUserQueryMessage(
        req.body.query,
        req.body.appliedFilters,
        req.body.chatMode,
        req.body.attachments,
      );

      const projectLink = await resolveProjectLink(
        orgId as unknown as string,
        userId as unknown as string,
        req.body as Record<string, unknown>,
      );

      const userConversationData: Partial<IChatSession> = {
        orgId,
        userId,
        initiator: userId,
        title: req.body.query.slice(0, 100),
        lastActivityAt: Date.now(),
        status: CONVERSATION_STATUS.INPROGRESS,
        // Store model and mode information
        modelInfo: modelInfo,
        sessionType: 'chat',
        ...(projectLink.projectId
          ? {
              projectId: new mongoose.Types.ObjectId(projectLink.projectId),
              projectVisibility: projectLink.projectVisibility,
            }
          : {}),
      };

      // Start transaction if replica set is available
      if (rsAvailable) {
        session = await mongoose.startSession();
        await session.withTransaction(async () => {
          const conversation = new ChatSession(userConversationData);
          savedConversation = await conversation.save({ session });
          await appendMessages(
            savedConversation._id as mongoose.Types.ObjectId,
            orgId as mongoose.Types.ObjectId,
            [userQueryMessage],
            session,
          );
        });
        // The stream outlives this request, so its listeners write without the session.
        await session.endSession();
        session = null;
      } else {
        const conversation = new ChatSession(userConversationData);
        savedConversation = await conversation.save();
        await appendMessages(
          savedConversation._id as mongoose.Types.ObjectId,
          orgId as mongoose.Types.ObjectId,
          [userQueryMessage],
        );
      }

      if (!savedConversation) {
        throw new InternalServerError('Failed to create conversation');
      }

      const newConversationId = savedConversation._id?.toString() || '';

      logger.debug('Initial conversation created', {
        requestId,
        conversationId: savedConversation._id,
        userId,
      });

      // Send initial connection event with conversationId + title and flush.
      // Title mirrors the persisted row (initial slice of query); avoids an extra
      // GET /conversations/:id just for sidebar display while streaming. AG-UI
      // mode: this becomes `CUSTOM{name:"conversation_created"}` — see
      // `streamAgentConversation`'s identical treatment.
      res.write(
        isAGUI(protocol)
          ? frameAGUI(AGUIEventType.CUSTOM, {
              name: 'conversation_created',
              value: {
                conversationId: newConversationId,
                title: savedConversation.title || undefined,
                ...(projectLink.projectId ? { projectId: projectLink.projectId } : {}),
              },
            })
          : `event: connected\ndata: ${JSON.stringify({
              message: 'SSE connection established',
              conversationId: newConversationId,
              title: savedConversation.title || undefined,
              ...(projectLink.projectId ? { projectId: projectLink.projectId } : {}),
            })}\n\n`,
      );
      (res as any).flush?.();
      timer.mark('conversation_created');

      const { agentMode } = parseChatMode((req.body as { chatMode?: string }).chatMode);
      // A new conversation has no stored history; history is never taken from the client (CE-19).
      const aiRequest = buildAiChatRequest({ kind: 'assistant' }, req.body, {
        conversationId: newConversationId,
        previousConversations: [],
        isNewConversation: true,
        project: projectLink.project,
      });
      const aiPayload = withStreamProtocol(aiRequest.payload, protocol);
      if (projectLink.projectId) {
        void ProjectService.touchActivity(projectLink.projectId);
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}${aiRequest.path}/stream`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: aiPayload,
      };

      // Variables to collect complete response data
      let completeData: IAIResponse | null = null;
      let buffer = '';
      let firstAiChunkSeen = false;
      /** True when AI backend already emitted a terminal `error` SSE we forwarded */
      let upstreamAiErrorEventForwarded = false;
      /** Guards `onAbandon` against also running after the normal
       * `stream.on('end')`/`'error'` path already finalized this run. */
      let streamSettled = false;
      const contentAccumulator = new StreamedContentAccumulator();

      const upstreamAbort = attachUpstreamAbort(res, requestId, () => {
        if (streamSettled || !savedConversation) return;
        if (completeData || upstreamAiErrorEventForwarded) {
          runGuarded(finalizeRun, { requestId });
          return;
        }
        streamSettled = true;
        void savePartialConversation(
          savedConversation,
          contentAccumulator.getText(),
          null,
          { parts: contentAccumulator.getParts() },
        ).catch((err: any) => {
          logger.error('Failed to save partial conversation on disconnect', {
            requestId,
            error: err?.message,
          });
        });
      });
      const stream = await startAIStream(
        aiCommandOptions,
        'Chat Stream',
        { requestId },
        upstreamAbort.signal,
      );
      timer.mark('ai_stream_open');

      if (!stream) {
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, savedConversation._id);

      // Process SSE events, capture complete event, and forward non-complete events
      stream.on('data', (chunk: Buffer) => {
        if (!firstAiChunkSeen) {
          firstAiChunkSeen = true;
          timer.mark('ai_first_byte');
          timer.emit('chat stream (node)', { requestId, agentMode });
        }
        const chunkStr = chunk.toString();
        buffer += chunkStr;

        // Look for complete events in the buffer
        const events = buffer.split('\n\n');
        buffer = events.pop() || ''; // Keep incomplete event in buffer

        let filteredChunk = '';
        const agui = isAGUI(protocol);

        for (const event of events) {
          if (event.trim()) {
            // Check if this is a complete event
            const lines = event.split('\n');
            const eventType = lines
              .find((line) => line.startsWith('event:'))
              ?.replace('event:', '')
              .trim();
            const dataLines = lines
              .filter((line) => line.startsWith('data:'))
              .map((line) => line.replace(/^data: ?/, ''));
            const dataLine = dataLines.join('\n');
            if (
              agui &&
              eventType === AGUIEventType.TOOL_CALL_START &&
              dataLine
            ) {
              contentAccumulator.feedToolCallStart(dataLine);
            }

            if (agui && eventType === AGUIEventType.RUN_FINISHED && dataLine) {
              // Root RUN_FINISHED's `result` IS `completion_data` — see
              // `AGUIFormatter.answer_final`. Nested (sub-agent) RUN_FINISHED
              // frames carry no `result` and are just forwarded through below.
              try {
                const parsed = JSON.parse(dataLine);
                if (parsed.result) {
                  completeData = parsed.result;
                  logger.debug('Captured RUN_FINISHED result from AI backend', {
                    requestId,
                    conversationId: savedConversation?._id,
                    answer: completeData?.answer,
                    citationsCount: completeData?.citations?.length || 0,
                  });
                  // DO NOT forward the root RUN_FINISHED — Node re-emits its
                  // own enriched RUN_FINISHED after saving, same as `complete`.
                } else {
                  filteredChunk += event + '\n\n';
                }
              } catch (parseError: any) {
                logger.error('Failed to parse RUN_FINISHED event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                filteredChunk += event + '\n\n';
              }
            } else if (agui && eventType === AGUIEventType.TEXT_MESSAGE_CONTENT && dataLine) {
              // Feed the passive-disconnect accumulator so a partial answer
              // survives a dropped connection — see savePartialConversation.
              try {
                contentAccumulator.feedTextMessageContent(JSON.parse(dataLine));
              } catch {
                // Non-fatal: still forward the frame below.
              }
              filteredChunk += event + '\n\n';
            } else if (agui && eventType === AGUIEventType.RUN_ERROR && dataLine) {
              try {
                const errorData = JSON.parse(dataLine) as Record<string, unknown>;
                const errorMessage =
                  (typeof errorData.message === 'string' && errorData.message) ||
                  CHAT_ERROR_MESSAGES.failed;
                upstreamAiErrorEventForwarded = true;
                if (savedConversation) {
                  void markConversationFailed(
                    savedConversation,
                    errorMessage,
                    session,
                    aguiErrorCodeFromPayload(errorData),
                    typeof errorData.stack === 'string' ? errorData.stack : undefined,
                  ).catch((markErr: any) => {
                    logger.error('Failed to mark conversation from AI RUN_ERROR SSE', {
                      requestId,
                      error: markErr?.message,
                    });
                  });
                }
                filteredChunk += event + '\n\n';
              } catch (parseError: any) {
                logger.error('Failed to parse RUN_ERROR event data from AI stream', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                filteredChunk += event + '\n\n';
              }
            } else if (!agui && eventType === 'complete' && dataLine) {
              try {
                completeData = JSON.parse(dataLine);
                logger.debug('Captured complete event data from AI backend', {
                  requestId,
                  conversationId: savedConversation?._id,
                  answer: completeData?.answer,
                  citationsCount: completeData?.citations?.length || 0,
                });
                // DO NOT forward the complete event from AI backend
                // We'll send our own complete event after processing
              } catch (parseError: any) {
                logger.error('Failed to parse complete event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                // Forward the event if we can't parse it
                filteredChunk += event + '\n\n';
              }
            } else if (!agui && eventType === 'answer_chunk' && dataLine) {
              // `accumulated` is the running full text, not a delta — see
              // LegacyFormatter.answer_delta.
              try {
                const parsed = JSON.parse(dataLine) as Record<string, unknown>;
                if (typeof parsed.accumulated === 'string') {
                  contentAccumulator.setAccumulatedText(parsed.accumulated);
                }
              } catch {
                // Non-fatal: still forward the frame below.
              }
              filteredChunk += event + '\n\n';
            } else if (!agui && eventType === 'error' && dataLine) {
              try {
                const errorData = JSON.parse(dataLine) as Record<string, unknown>;
                const errorMessage =
                  (typeof errorData.message === 'string' && errorData.message) ||
                  (typeof errorData.error === 'string' && errorData.error) ||
                  CHAT_ERROR_MESSAGES.failed;
                upstreamAiErrorEventForwarded = true;
                if (savedConversation) {
                  const meta =
                    errorData.metadata && typeof errorData.metadata === 'object'
                      ? new Map(Object.entries(errorData.metadata as Record<string, unknown>))
                      : undefined;
                  void markConversationFailed(
                    savedConversation,
                    errorMessage,
                    session,
                    'streaming_error',
                    typeof errorData.stack === 'string' ? errorData.stack : undefined,
                    meta,
                  ).catch((markErr: any) => {
                    logger.error('Failed to mark conversation from AI error SSE', {
                      requestId,
                      error: markErr?.message,
                    });
                  });
                }
                filteredChunk += event + '\n\n';
              } catch (parseError: any) {
                logger.error('Failed to parse error event data from AI stream', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                upstreamAiErrorEventForwarded = true;
                if (savedConversation) {
                  const parseFailMsg = CHAT_ERROR_MESSAGES.failed;
                  void markConversationFailed(
                    savedConversation,
                    parseFailMsg,
                    session,
                    'parse_error',
                    parseError.stack,
                  ).catch((markErr: any) => {
                    logger.error('Failed to mark conversation after SSE parse error', {
                      requestId,
                      error: markErr?.message,
                    });
                  });
                }
                filteredChunk += event + '\n\n';
              }
            } else if (agui && eventType === AGUIEventType.CUSTOM && dataLine && savedConversation) {
              try {
                const eventData = JSON.parse(dataLine);
                if (eventData?.name === 'ask_user_question' && eventData.value) {
                  const toolCallMessage = {
                    messageType: 'tool_call' as const,
                    content: '',
                    tools: [{
                      toolName: 'ask_user_question',
                      toolResult: eventData.value.toolData ?? eventData.value,
                    }],
                    createdAt: new Date(),
                    updatedAt: new Date(),
                  };
                  void appendMessages(
                    savedConversation._id as mongoose.Types.ObjectId,
                    savedConversation.orgId,
                    [toolCallMessage],
                  ).catch((saveErr: any) => {
                    logger.error('Failed to persist ask_user_question tool_call message', {
                      requestId,
                      conversationId: savedConversation?._id,
                      error: saveErr?.message,
                    });
                  });
                }
              } catch (parseErr: any) {
                logger.warn('Failed to parse CUSTOM event data', {
                  requestId,
                  error: parseErr?.message,
                });
              }
              filteredChunk += event + '\n\n';
            } else if (!agui && eventType === 'ask_user_question' && dataLine && savedConversation) {
              try {
                const eventData = JSON.parse(dataLine);
                if (eventData.status === 'success') {
                  const toolCallMessage = {
                    messageType: 'tool_call' as const,
                    content: '',
                    tools: [{
                      toolName: 'ask_user_question',
                      toolResult: eventData.toolData ?? eventData,
                    }],
                    createdAt: new Date(),
                    updatedAt: new Date(),
                  };
                  void appendMessages(
                    savedConversation._id as mongoose.Types.ObjectId,
                    savedConversation.orgId,
                    [toolCallMessage],
                  ).catch((saveErr: any) => {
                    logger.error('Failed to persist ask_user_question tool_call message', {
                      requestId,
                      conversationId: savedConversation?._id,
                      error: saveErr?.message,
                    });
                  });
                }
              } catch (parseErr: any) {
                logger.warn('Failed to parse ask_user_question event data', {
                  requestId,
                  error: parseErr?.message,
                });
              }
              filteredChunk += event + '\n\n';
            } else {
              // Forward all other non-complete events
              filteredChunk += event + '\n\n';
            }
          }
        }

        // Forward only non-complete events to client
        if (filteredChunk) {
          res.write(filteredChunk);
          (res as any).flush?.();
        }
      });

      const finalizeRun = async (): Promise<void> => {
        streamSettled = true;
        logger.debug('Stream ended successfully', { requestId });
        try {
          // Save the complete conversation data to database
          if (completeData && savedConversation) {
            const conversation = await saveCompleteConversation(
              savedConversation,
              completeData,
              orgId,
              session,
              modelInfo,
            );

            // Send the final conversation data in the same format as createConversation
            const responsePayload = {
              conversation: conversation,
              meta: {
                requestId,
                timestamp: new Date().toISOString(),
                duration: Date.now() - startTime,
              },
            };

            // Send final response event with the complete conversation data
            res.write(
              isAGUI(protocol)
                ? frameAGUI(AGUIEventType.RUN_FINISHED, { result: responsePayload })
                : `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
            );

            logger.debug(
              'Conversation completed and saved, sent custom complete event',
              {
                requestId,
                conversationId: savedConversation._id,
                duration: Date.now() - startTime,
              },
            );
          } else if (!upstreamAiErrorEventForwarded) {
            // Mark as failed if no complete data received (and AI did not already send error SSE)
            if (savedConversation) {
              await markConversationFailed(
                savedConversation,
                CHAT_ERROR_MESSAGES.interrupted,
                session,
                'no_response',
              );
            }

            // Send error event
            res.write(
              isAGUI(protocol)
                ? frameAGUI(AGUIEventType.RUN_ERROR, {
                    message: CHAT_ERROR_MESSAGES.interrupted,
                    code: 'no_response',
                  })
                : `event: error\ndata: ${JSON.stringify({
                    error: CHAT_ERROR_MESSAGES.interrupted,
                  })}\n\n`,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to save complete conversation', {
            requestId,
            conversationId: savedConversation?._id,
            error: dbError.message,
          });

          if (savedConversation) {
            // Awaited but contained: a second failed write must not keep the stream open.
            await markConversationFailed(
              savedConversation,
              CHAT_ERROR_MESSAGES.saveFailed,
              session,
              'save_error',
              dbError.stack,
            ).catch((markErr: unknown) => {
              logger.error(
                'Failed to mark conversation as failed after save error',
                {
                  requestId,
                  error: markErr instanceof Error ? markErr.message : markErr,
                },
              );
            });
          }

          // Send error event
          res.write(
            isAGUI(protocol)
              ? frameAGUI(AGUIEventType.RUN_ERROR, {
                  message: CHAT_ERROR_MESSAGES.saveFailed,
                  code: 'save_error',
                })
              : `event: error\ndata: ${JSON.stringify({
                  error: CHAT_ERROR_MESSAGES.saveFailed,
                })}\n\n`,
          );
        }

        res.end();
      };
      stream.on('end', () => {
        runGuarded(finalizeRun, { requestId });
      });

      stream.on('error', async (error: Error) => {
        if (upstreamAbort.isAborted()) {
          logger.debug('Stream abandoned after the detached-run cap', {
            requestId,
          });
          return;
        }
        streamSettled = true;
        logger.error('Stream error', { requestId, error: error.message });
        try {
          // Mark conversation as failed
          if (savedConversation) {
            await markConversationFailed(
              savedConversation,
              userFacingChatError(error),
              session,
              'stream_error',
              error.stack,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to mark conversation as failed', {
            requestId,
            conversationId: savedConversation?._id,
            error: dbError.message,
          });
        }

        const errorEvent = isAGUI(protocol)
          ? frameAGUI(AGUIEventType.RUN_ERROR, {
              message: userFacingChatError(error),
              code: 'stream_error',
            })
          : `event: error\ndata: ${JSON.stringify({
              error: userFacingChatError(error),
            })}\n\n`;
        res.write(errorEvent);
        res.end();
      });
    } catch (error: any) {
      try {
        // Mark conversation as failed if it was created
        if (savedConversation) {
          await markConversationFailed(
            savedConversation,
            userFacingChatError(error),
            session,
            'internal_error',
            error.stack,
          );
        }
      } catch (dbError: any) {
        logger.error('Failed to mark conversation as failed in catch block', {
          requestId,
          error: dbError.message,
        });
      }

      if (!res.headersSent) {
        res.writeHead(500, {
          'Content-Type': 'text/event-stream; charset=utf-8',
        });
      }
      const level = logLevelFor(error);
      logger[level]('Error in streamChat', {
        requestId,
        error: error.message,
        ...(level === 'error' ? { stack: error.stack } : {}),
      });
      const errorEvent = isAGUI(protocol)
        ? frameAGUI(AGUIEventType.RUN_ERROR, {
            message: userFacingChatError(error),
            code: 'internal_error',
          })
        : `event: error\ndata: ${JSON.stringify({
            error: userFacingChatError(error),
          })}\n\n`;
      res.write(errorEvent);
      res.end();
    } finally {
      if (session) {
        // End before the SSE callbacks run. They close over `session` and
        // Mongo rejects a save on an already-ended session.
        session.endSession();
        session = null;
      }
    }
  };

export const streamChatInternal =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      await hydrateScopedRequestAsUser(req, appConfig);
      await streamChat(appConfig)(req as AuthenticatedUserRequest, res);
    } catch (error) {
      next(error);
    }
  };

/**
 * Express 4 drops a rejected async handler, so a refusal before the stream starts
 * goes to `next`. Internal wrappers pass no `next` and catch the throw themselves.
 */
const refuseBeforeStream = (
  next: NextFunction | undefined,
  message: string,
): void => {
  const error = new BadRequestError(message);
  if (!next) throw error;
  next(error);
};

export const addMessageStream =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next?: NextFunction,
  ): Promise<void> => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { conversationId } = req.params;

    let session: ClientSession | null = null;
    let existingConversation: IChatSessionDocument | null = null;
    let reservation: RunReservation | null = null;

    const modelInfo = extractModelInfo(req.body);
    const protocol = resolveProtocol(
      req.body as Record<string, unknown>,
      req.query as Record<string, unknown>,
    );

    if (!req.body.query) {
      return refuseBeforeStream(next, 'Query is required');
    }

    // Helper function that contains the common conversation operations
    async function performAddMessageStream(
      session?: ClientSession | null,
    ): Promise<void> {
      // Get existing conversation
      const conversation = await ChatSession.findOne({
        _id: conversationId,
        orgId,
        userId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
      });

      if (!conversation) {
        throw new NotFoundError('Conversation not found');
      }

      // Update status to processing when adding a new message
      conversation.status = CONVERSATION_STATUS.INPROGRESS;
      conversation.failReason = undefined; // Clear previous error if any

      // Update model and mode information if provided
      const fieldsToUpdate: Array<keyof IAIModel> = [
        'modelKey',
        'modelName',
        'modelProvider',
        'chatMode',
        'modelFriendlyName',
        'reasoningEffort',
      ];
      for (const field of fieldsToUpdate) {
        const value = req.body[field];
        if (value !== undefined && value !== null) {
          assignAiModelField(conversation.modelInfo as IAIModel, field, value);
        }
      }

      // First, add the user message to the existing conversation
      await appendMessages(
        conversation._id as mongoose.Types.ObjectId,
        conversation.orgId,
        [
          buildUserQueryMessage(
            req.body.query,
            req.body.appliedFilters,
            req.body.chatMode,
            req.body.attachments,
          ),
        ],
        session,
      );
      conversation.lastActivityAt = Date.now();

      // Save the user message to the existing conversation first
      const savedConversation = session
        ? await conversation.save({ session })
        : await conversation.save();

      if (!savedConversation) {
        throw new InternalServerError(
          'Failed to update conversation with user message',
        );
      }

      existingConversation = savedConversation;

      logger.debug('User message added to conversation', {
        requestId,
        conversationId: existingConversation._id,
        userId,
      });
    }

    try {
      // Set SSE headers
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'X-Accel-Buffering': 'no',
      });

      // Send initial connection event and flush. AG-UI mode: this becomes
      // `CUSTOM{name:"conversation_created"}` — see `streamAgentConversation`.
      res.write(
        isAGUI(protocol)
          ? frameAGUI(AGUIEventType.CUSTOM, {
              name: 'conversation_created',
              value: { conversationId },
            })
          : `event: connected\ndata: ${JSON.stringify({ message: 'SSE connection established' })}\n\n`,
      );
      (res as any).flush?.();

      logger.debug('Adding message to conversation via stream', {
        requestId,
        conversationId,
        userId,
        ...buildAiRequestLogMetadata(req.body as Record<string, unknown>),
        timestamp: new Date().toISOString(),
      });

      reservation = await reserveRun({
        _id: conversationId,
        orgId,
        userId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
      });

      // Get existing conversation and add user message
      if (rsAvailable) {
        session = await mongoose.startSession();
        await session.withTransaction(() => performAddMessageStream(session));
        // The stream outlives this request, so its listeners write without the session.
        await session.endSession();
        session = null;
      } else {
        await performAddMessageStream();
      }

      if (!existingConversation) {
        throw new NotFoundError('Conversation not found');
      }
      const confirmedConversation =
        existingConversation as IChatSessionDocument;

      // Format previous conversations for context (excluding the user message we just added)
      const allMessagesSoFar = (await getMessages(
        confirmedConversation._id,
        { orgId: confirmedConversation.orgId },
        session,
      )) as IMessage[];
      const previousConversations = formatPreviousConversations(
        allMessagesSoFar.slice(0, -1),
      );

      const followUpProject = await loadProjectForSession(
        confirmedConversation.orgId.toString(),
        confirmedConversation.userId.toString(),
        confirmedConversation.projectId,
      );
      const aiRequest = buildAiChatRequest({ kind: 'assistant' }, req.body, {
        conversationId,
        previousConversations,
        isNewConversation: false,
        project: followUpProject,
      });
      const aiPayload = withStreamProtocol(aiRequest.payload, protocol);
      if (confirmedConversation.projectId) {
        void ProjectService.touchActivity(
          confirmedConversation.projectId.toString(),
        );
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}${aiRequest.path}/stream`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: aiPayload,
      };

      // Variables to collect complete response data
      let completeData: IAIResponse | null = null;
      let buffer = '';
      /** True when AI backend already emitted a terminal `error` SSE we forwarded */
      let upstreamAiErrorEventForwarded = false;
      /** Guards `onAbandon` against also running after the normal
       * `stream.on('end')`/`'error'` path already finalized this run. */
      let streamSettled = false;
      const contentAccumulator = new StreamedContentAccumulator();

      const upstreamAbort = attachUpstreamAbort(res, requestId, () => {
        if (streamSettled || !existingConversation) return;
        if (completeData || upstreamAiErrorEventForwarded) {
          runGuarded(finalizeRun, { requestId });
          return;
        }
        streamSettled = true;
        void savePartialConversation(
          existingConversation,
          contentAccumulator.getText(),
          null,
          { parts: contentAccumulator.getParts() },
        ).catch((err: any) => {
          logger.error('Failed to save partial conversation on disconnect', {
            requestId,
            error: err?.message,
          });
        });
      });
      const stream = await startAIStream(
        aiCommandOptions,
        'Add Message Stream',
        { requestId },
        upstreamAbort.signal,
      );

      if (!stream) {
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, confirmedConversation._id, reservation?.runId);

      // Process SSE events, capture complete event, and forward non-complete events
      stream.on('data', (chunk: Buffer) => {
        const chunkStr = chunk.toString();
        buffer += chunkStr;

        // Look for complete events in the buffer
        const events = buffer.split('\n\n');
        buffer = events.pop() || ''; // Keep incomplete event in buffer

        let filteredChunk = '';
        const agui = isAGUI(protocol);

        for (const event of events) {
          if (event.trim()) {
            // Check if this is a complete event
            const lines = event.split('\n');
            const eventType = lines
              .find((line) => line.startsWith('event:'))
              ?.replace('event:', '')
              .trim();
            const dataLines = lines
              .filter((line) => line.startsWith('data:'))
              .map((line) => line.replace(/^data: ?/, ''));
            const dataLine = dataLines.join('\n');
            if (
              agui &&
              eventType === AGUIEventType.TOOL_CALL_START &&
              dataLine
            ) {
              contentAccumulator.feedToolCallStart(dataLine);
            }
            if (agui && eventType === AGUIEventType.RUN_FINISHED && dataLine) {
              // Root RUN_FINISHED's `result` IS `completion_data` — see
              // `AGUIFormatter.answer_final`. Nested (sub-agent) RUN_FINISHED
              // frames carry no `result` and are just forwarded through below.
              try {
                const parsed = JSON.parse(dataLine);
                if (parsed.result) {
                  completeData = parsed.result;
                  logger.debug('Captured RUN_FINISHED result from AI backend', {
                    requestId,
                    conversationId: existingConversation?._id,
                    answer: completeData?.answer,
                    citationsCount: completeData?.citations?.length || 0,
                  });
                  // DO NOT forward the root RUN_FINISHED — Node re-emits its
                  // own enriched RUN_FINISHED after saving, same as `complete`.
                } else {
                  filteredChunk += event + '\n\n';
                }
              } catch (parseError: any) {
                logger.error('Failed to parse RUN_FINISHED event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                filteredChunk += event + '\n\n';
              }
            } else if (
              agui &&
              eventType === AGUIEventType.TEXT_MESSAGE_CONTENT &&
              dataLine
            ) {
              // Feed the passive-disconnect accumulator so a partial answer
              // survives a dropped connection — see savePartialConversation.
              try {
                contentAccumulator.feedTextMessageContent(JSON.parse(dataLine));
              } catch {
                // Non-fatal: still forward the frame below.
              }
              filteredChunk += event + '\n\n';
            } else if (
              agui &&
              eventType === AGUIEventType.RUN_ERROR &&
              dataLine
            ) {
              try {
                const errorData = JSON.parse(dataLine);
                const errorMessage =
                  errorData.message || CHAT_ERROR_MESSAGES.failed;
                upstreamAiErrorEventForwarded = true;
                if (existingConversation) {
                  void markConversationFailed(
                    existingConversation,
                    errorMessage,
                    session,
                    aguiErrorCodeFromPayload(
                      errorData as Record<string, unknown>,
                    ),
                    errorData.stack,
                  ).catch((markErr: any) => {
                    logger.error(
                      'Failed to mark conversation from AI RUN_ERROR SSE',
                      {
                        requestId,
                        error: markErr?.message,
                      },
                    );
                  });
                }
                filteredChunk += event + '\n\n';
              } catch (parseError: any) {
                logger.error('Failed to parse RUN_ERROR event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                filteredChunk += event + '\n\n';
              }
            } else if (!agui && eventType === 'complete' && dataLine) {
              try {
                completeData = JSON.parse(dataLine);
                logger.debug('Captured complete event data from AI backend', {
                  requestId,
                  conversationId: existingConversation?._id,
                  answer: completeData?.answer,
                  citationsCount: completeData?.citations?.length || 0,
                });
                // DO NOT forward the complete event from AI backend
                // We'll send our own complete event after processing
              } catch (parseError: any) {
                logger.error('Failed to parse complete event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                // Forward the event if we can't parse it
                filteredChunk += event + '\n\n';
              }
            } else if (!agui && eventType === 'answer_chunk' && dataLine) {
              // `accumulated` is the running full text, not a delta — see
              // LegacyFormatter.answer_delta.
              try {
                const parsed = JSON.parse(dataLine) as Record<string, unknown>;
                if (typeof parsed.accumulated === 'string') {
                  contentAccumulator.setAccumulatedText(parsed.accumulated);
                }
              } catch {
                // Non-fatal: still forward the frame below.
              }
              filteredChunk += event + '\n\n';
            } else if (!agui && eventType === 'error' && dataLine) {
              try {
                const errorData = JSON.parse(dataLine);
                const errorMessage =
                  errorData.message ||
                  errorData.error ||
                  CHAT_ERROR_MESSAGES.failed;
                upstreamAiErrorEventForwarded = true;
                if (existingConversation) {
                  void markConversationFailed(
                    existingConversation,
                    errorMessage,
                    session,
                    'streaming_error',
                    errorData.stack,
                    errorData.metadata
                      ? new Map(Object.entries(errorData.metadata))
                      : undefined,
                  ).catch((markErr: any) => {
                    logger.error(
                      'Failed to mark conversation from AI error SSE',
                      {
                        requestId,
                        error: markErr?.message,
                      },
                    );
                  });
                }
                filteredChunk += event + '\n\n';
              } catch (parseError: any) {
                logger.error('Failed to parse error event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                upstreamAiErrorEventForwarded = true;
                const errorMessage = CHAT_ERROR_MESSAGES.failed;
                if (existingConversation) {
                  void markConversationFailed(
                    existingConversation,
                    errorMessage,
                    session,
                    'parse_error',
                    parseError.stack,
                  ).catch((markErr: any) => {
                    logger.error(
                      'Failed to mark conversation after SSE parse error',
                      {
                        requestId,
                        error: markErr?.message,
                      },
                    );
                  });
                }
                filteredChunk += event + '\n\n';
              }
            } else if (
              agui &&
              eventType === AGUIEventType.CUSTOM &&
              dataLine &&
              existingConversation
            ) {
              try {
                const eventData = JSON.parse(dataLine);
                if (
                  eventData?.name === 'ask_user_question' &&
                  eventData.value
                ) {
                  const toolCallMessage = {
                    messageType: 'tool_call' as const,
                    content: '',
                    tools: [
                      {
                        toolName: 'ask_user_question',
                        toolResult: eventData.value.toolData ?? eventData.value,
                      },
                    ],
                    createdAt: new Date(),
                    updatedAt: new Date(),
                  };
                  void appendMessages(
                    existingConversation._id,
                    existingConversation.orgId,
                    [toolCallMessage as unknown as IMessage],
                    session,
                  ).catch((saveErr: any) => {
                    logger.error(
                      'Failed to persist ask_user_question tool_call message',
                      {
                        requestId,
                        conversationId: existingConversation?._id,
                        error: saveErr?.message,
                      },
                    );
                  });
                }
              } catch (parseErr: any) {
                logger.warn('Failed to parse CUSTOM event data', {
                  requestId,
                  error: parseErr?.message,
                });
              }
              filteredChunk += event + '\n\n';
            } else if (
              !agui &&
              eventType === 'ask_user_question' &&
              dataLine &&
              existingConversation
            ) {
              try {
                const eventData = JSON.parse(dataLine);
                if (eventData.status === 'success') {
                  const toolCallMessage = {
                    messageType: 'tool_call' as const,
                    content: '',
                    tools: [
                      {
                        toolName: 'ask_user_question',
                        toolResult: eventData.toolData ?? eventData,
                      },
                    ],
                    createdAt: new Date(),
                    updatedAt: new Date(),
                  };
                  void appendMessages(
                    existingConversation._id,
                    existingConversation.orgId,
                    [toolCallMessage as unknown as IMessage],
                    session,
                  ).catch((saveErr: any) => {
                    logger.error(
                      'Failed to persist ask_user_question tool_call message',
                      {
                        requestId,
                        conversationId: existingConversation?._id,
                        error: saveErr?.message,
                      },
                    );
                  });
                }
              } catch (parseErr: any) {
                logger.warn('Failed to parse ask_user_question event data', {
                  requestId,
                  error: parseErr?.message,
                });
              }
              filteredChunk += event + '\n\n';
            } else {
              // Forward all non-complete events
              filteredChunk += event + '\n\n';
            }
          }
        }

        // Forward only non-complete events to client
        if (filteredChunk) {
          res.write(filteredChunk);
          (res as any).flush?.();
        }
      });

      const finalizeRun = async (): Promise<void> => {
        logger.debug('Stream ended successfully', { requestId });
        streamSettled = true;
        try {
          // Save the AI response to the existing conversation
          if (completeData && existingConversation) {
            try {
              // Create and save citations
              const savedCitations: ICitation[] = await Promise.all(
                completeData.citations?.map(async (citation: ICitation) => {
                  const newCitation = new Citation({
                    content: citation.content,
                    chunkIndex: citation.chunkIndex,
                    citationType: citation.citationType,
                    metadata: {
                      ...citation.metadata,
                      orgId,
                    },
                  });
                  return newCitation.save();
                }) || [],
              );

              // Build AI response message using existing utility
              const aiResponseMessage = buildAIResponseMessage(
                { statusCode: 200, data: completeData },
                savedCitations,
                modelInfo,
              ) as IMessageDocument;

              // Add the AI message to the existing conversation
              const insertedAiMessage = (
                await appendMessages(
                  existingConversation._id,
                  existingConversation.orgId,
                  [aiResponseMessage],
                  session,
                )
              )[0];
              existingConversation.lastActivityAt = Date.now();
              recordClassifiedFailureOnSession(
                existingConversation,
                completeData,
              );

              // Save the updated conversation with AI response
              const updatedConversation = session
                ? await existingConversation.save({ session })
                : await existingConversation.save();

              if (!updatedConversation) {
                throw new InternalServerError(
                  'Failed to update conversation with AI response',
                );
              }

              // Return the updated conversation in the same format as addMessage
              const responseConversation = await attachPopulatedCitations(
                updatedConversation.toObject(),
                [insertedAiMessage!.toObject()],
                savedCitations,
                session,
              );

              // Send the final conversation data in the same format as addMessage
              const responsePayload = {
                conversation: responseConversation,
                recordsUsed: savedCitations.length,
                meta: {
                  requestId,
                  timestamp: new Date().toISOString(),
                  duration: Date.now() - startTime,
                  recordsUsed: savedCitations.length,
                },
              };

              // Send final response event with the complete conversation data
              res.write(
                isAGUI(protocol)
                  ? frameAGUI(AGUIEventType.RUN_FINISHED, {
                      result: responsePayload,
                    })
                  : `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
              );

              logger.debug(
                'Message added and conversation updated, sent custom complete event',
                {
                  requestId,
                  conversationId: existingConversation._id,
                  duration: Date.now() - startTime,
                },
              );
            } catch (error: any) {
              // Update conversation status for general errors
              if (existingConversation) {
                await markConversationFailed(
                  existingConversation,
                  userFacingChatError(error),
                  session,
                  'internal_error',
                  error.stack,
                );
              }

              if (error.cause && error.cause.code === 'ECONNREFUSED') {
                throw new InternalServerError(
                  SERVICE_UNAVAILABLE_MESSAGE,
                  error,
                );
              }
              throw error;
            }
          } else if (!upstreamAiErrorEventForwarded) {
            // Mark as failed if no complete data received (and AI did not already send error SSE)
            if (existingConversation) {
              await markConversationFailed(
                existingConversation,
                CHAT_ERROR_MESSAGES.interrupted,
                session,
                'no_response',
              );
            }

            // Send error event
            res.write(
              isAGUI(protocol)
                ? frameAGUI(AGUIEventType.RUN_ERROR, {
                    message: CHAT_ERROR_MESSAGES.interrupted,
                    code: 'no_response',
                  })
                : `event: error\ndata: ${JSON.stringify({
                    error: CHAT_ERROR_MESSAGES.interrupted,
                  })}\n\n`,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to save AI response to conversation', {
            requestId,
            conversationId: existingConversation?._id,
            error: dbError.message,
          });

          // Send error event
          res.write(
            isAGUI(protocol)
              ? frameAGUI(AGUIEventType.RUN_ERROR, {
                  message: CHAT_ERROR_MESSAGES.saveFailed,
                  code: 'save_error',
                })
              : `event: error\ndata: ${JSON.stringify({
                  error: CHAT_ERROR_MESSAGES.saveFailed,
                })}\n\n`,
          );
        }

        res.end();
      };
      stream.on('end', () => {
        runGuarded(finalizeRun, { requestId });
      });

      stream.on('error', async (error: Error) => {
        if (upstreamAbort.isAborted()) {
          logger.debug('Stream abandoned after the detached-run cap', {
            requestId,
          });
          return;
        }
        streamSettled = true;
        logger.error('Stream error', { requestId, error: error.message });
        try {
          if (existingConversation) {
            await markConversationFailed(
              existingConversation,
              userFacingChatError(error),
              session,
              'stream_error',
              error.stack,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to mark conversation as failed', {
            requestId,
            conversationId: existingConversation?._id,
            error: dbError.message,
          });
        }

        const errorEvent = isAGUI(protocol)
          ? frameAGUI(AGUIEventType.RUN_ERROR, {
              message: userFacingChatError(error),
              code: 'stream_error',
            })
          : `event: error\ndata: ${JSON.stringify({
              error: userFacingChatError(error),
            })}\n\n`;
        res.write(errorEvent);
        res.end();
      });
    } catch (error: any) {
      logger[logLevelFor(error)]('Error in addMessageStream', {
        requestId,
        conversationId,
        error: error.message,
      });

      try {
        // Mark conversation as failed if it exists
        if (existingConversation) {
          await markConversationFailed(
            existingConversation as IChatSessionDocument,
            userFacingChatError(error),
            session,
            'internal_error',
            error.stack,
          );
        } else {
          await releaseRun(reservation);
        }
      } catch (dbError: any) {
        logger.error('Failed to mark conversation as failed in catch block', {
          requestId,
          conversationId,
          error: dbError.message,
        });
      }

      if (!res.headersSent) {
        res.writeHead(500, {
          'Content-Type': 'text/event-stream; charset=utf-8',
        });
      }

      const errorEvent = isAGUI(protocol)
        ? frameAGUI(AGUIEventType.RUN_ERROR, {
            message: userFacingChatError(error),
            code: 'internal_error',
          })
        : `event: error\ndata: ${JSON.stringify({
            error: userFacingChatError(error),
          })}\n\n`;
      res.write(errorEvent);
      res.end();
    } finally {
      if (session) {
        session.endSession();
        session = null;
      }
    }
  };

export const addMessageStreamInternal =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      await hydrateScopedRequestAsUser(req, appConfig);
      await addMessageStream(appConfig)(req as AuthenticatedUserRequest, res);
    } catch (error) {
      next(error);
    }
  };

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
      queryParams: Object.keys(req.query),
    });

    const source = req.query.source ?? 'owned';
    if (source !== 'owned' && source !== 'shared') {
      throw new BadRequestError(
        "Query param 'source' must be 'owned' or 'shared'",
      );
    }

    const { skip, limit, page } = getPaginationParams(req);
    const isOwned = source === 'owned';
    // A pin belongs to its owner, so only the owner's own list is pinned-first.
    const sortOptions = buildSortOptions(req, { pinnedFirst: isOwned });

    let contentMatchIds: mongoose.Types.ObjectId[] | undefined;
    if (req.query.search) {
      const escapedSearch = validateAndEscapeSearch(req.query.search);
      contentMatchIds = await findSessionIdsMatchingContent(
        orgId,
        escapedSearch,
      );
    }
    const accessibleProjectIds = !isOwned
      ? await ProjectService.getAccessibleProjectIds(orgId, userId)
      : undefined;
    const filter = {
      ...buildFilter(
        req,
        orgId,
        userId,
        conversationId as string,
        isOwned,
        !isOwned,
        contentMatchIds,
        accessibleProjectIds,
      ),
      // Chats shared with the user include agent chats, so the recipient can open them
      // from the sidebar without first finding the agent. Owned chats stay plain-chat only:
      // agent chats are listed per agent.
      sessionType: isOwned
        ? EXCLUDE_AGENT.sessionType
        : { $in: [EXCLUDE_AGENT.sessionType, ONLY_AGENT.sessionType] },
    };
    let selection = ChatSession.find(filter)
      .sort(sortOptions as any)
      .skip(skip)
      .limit(limit)
      .select('-__v');
    if (!isOwned) {
      selection = selection.select('-sharedWith -isPinned') as typeof selection;
    }

    const [conversations, totalCount] = await Promise.all([
      selection.lean().exec(),
      ChatSession.countDocuments(filter),
    ]);

    let processedConversations = conversations.map((conversation: any) =>
      addComputedFields(conversation as IConversation, userId),
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
      filters: buildFiltersMetadata(filter, req.query),
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

const CONVERSATION_PERMISSION_SYNC_TIMEOUT_MS = 15_000;
// Awaited on a shared viewer's conversation load, so a degraded AI service must
// not stall the read.
const CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS = 3_000;

const CHAT_ATTACHMENT_CONNECTOR = 'ATTACHMENTS';

const collectChatAttachmentRecordIds = (messages: any[]): string[] => {
  const ids = new Set<string>();
  for (const msg of messages ?? []) {
    for (const att of msg.attachments ?? []) {
      if (typeof att?.recordId === 'string' && att.recordId) {
        ids.add(att.recordId);
      }
    }
    for (const cit of msg.citations ?? []) {
      const populated =
        cit?.citationData ??
        (cit?.citationId &&
        typeof cit.citationId === 'object' &&
        cit.citationId.metadata
          ? cit.citationId
          : null);
      const recordId = populated?.metadata?.recordId;
      if (typeof recordId !== 'string' || !recordId) {
        continue;
      }
      if (
        String(populated?.metadata?.connector ?? '').toUpperCase() ===
        CHAT_ATTACHMENT_CONNECTOR
      ) {
        ids.add(recordId);
      }
    }
  }
  return [...ids];
};

const loadConversationChatAttachmentRecordIds = async (
  conversationId: string,
): Promise<string[]> => {
  const sessionMessages = await ChatSessionMessage.find(
    { sessionId: conversationId },
    { attachments: 1, citations: 1 },
  )
    .populate({
      path: 'citations.citationId',
      model: 'citation',
      select: 'metadata.recordId metadata.connector',
    })
    .lean();
  return collectChatAttachmentRecordIds(sessionMessages as any[]);
};

interface ConversationPermissionSyncOptions {
  appConfig: AppConfig;
  records: 'attachments' | 'artifacts';
  method: typeof HttpMethod.POST | typeof HttpMethod.DELETE;
  // Conversation owner. Python only grants/revokes on records this user OWNS,
  // so callers must have verified ownership or sharing in Mongo first.
  grantorUserId: string;
  orgId: string;
  body: Record<string, unknown>;
  requestId?: string;
  conversationId?: string;
  timeoutMs?: number;
  maxAttempts?: number;
}

// Best-effort: a failure is logged and never fails the share/unshare/view.
const syncConversationRecordPermissions = async ({
  appConfig,
  records,
  method,
  grantorUserId,
  orgId,
  body,
  requestId,
  conversationId,
  timeoutMs = CONVERSATION_PERMISSION_SYNC_TIMEOUT_MS,
  maxAttempts,
}: ConversationPermissionSyncOptions): Promise<void> => {
  const logContext = { requestId, conversationId, records, method };
  try {
    const serviceToken = new AuthTokenService(
      appConfig.jwtSecret,
      appConfig.scopedJwtSecret,
    ).generateScopedToken(
      {
        userId: grantorUserId,
        orgId,
        scopes: [TokenScopes.CONVERSATION_PERMISSIONS],
      },
      '5m',
    );
    const response = await new AIServiceCommand({
      uri: `${appConfig.aiBackend}/api/v1/chat/${records}/permissions`,
      method,
      headers: {
        Authorization: `Bearer ${serviceToken}`,
        'Content-Type': 'application/json',
      },
      body,
      timeoutMs,
      maxAttempts,
    }).execute();
    // execute() resolves on 4xx/5xx; only transport errors reject.
    if (response.statusCode < 200 || response.statusCode >= 300) {
      logger.warn('Conversation record permission sync rejected', {
        ...logContext,
        statusCode: response.statusCode,
        response: response.data,
      });
    }
  } catch (error: any) {
    logger.warn('Conversation record permission sync failed', {
      ...logContext,
      error: error.message,
    });
  }
};

export const getConversationById =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
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

      // Build the base filter with access control
      let contentMatchIds: mongoose.Types.ObjectId[] | undefined;
      if (req.query.search) {
        const escapedSearch = validateAndEscapeSearch(req.query.search);
        contentMatchIds = await findSessionIdsMatchingContent(
          orgId,
          escapedSearch,
        );
      }
      const accessibleProjectIds = await ProjectService.getAccessibleProjectIds(
        orgId,
        userId,
      );
      const baseFilter = buildFilter(
        req,
        orgId,
        userId,
        conversationId,
        true,
        true,
        contentMatchIds,
        accessibleProjectIds,
      );

      // Build message filter
      const messageFilter = buildMessageFilter(req);

      // Get sort options for messages
      const messageSortOptions = buildMessageSortOptions(
        sortBy as string,
        sortOrder as string,
      );

      const session = await ChatSession.findOne({
        ...baseFilter,
        ...EXCLUDE_AGENT,
      })
        .select({
          orgId: 1,
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

      // Sharing only grants READER on artifacts that existed at share time, so a
      // shared viewer is caught up on artifacts created since. Awaited so the edges
      // exist before the client requests artifact content; older pages carry no
      // new artifacts and skip it.
      const initiatorId = session.initiator?.toString();
      if (page === 1 && initiatorId && initiatorId !== userId) {
        await syncConversationRecordPermissions({
          appConfig,
          records: 'artifacts',
          method: HttpMethod.POST,
          grantorUserId: initiatorId,
          orgId,
          body: { conversationId, userIds: [userId] },
          requestId,
          conversationId,
          timeoutMs: CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS,
          maxAttempts: 1,
        });
      }

      const sessionId = session._id as unknown as Types.ObjectId;

      const totalMessages = await ChatSessionMessage.countDocuments({
        sessionId,
        orgId: session.orgId,
      });

      const { skip, limit: effectiveLimit } = olderMessagesWindow(
        totalMessages,
        page,
        limit,
      );

      const messages = await getMessages(sessionId, {
        orgId: session.orgId,
        skip,
        limit: effectiveLimit,
        populateCitations: true,
      });

      // Sharing only grants READER on attachments that existed at share time. Catch
      // up the chips and ATTACHMENTS citations on every page the viewer loads — an
      // attachment added after the share can already sit on an older page.
      if (initiatorId && initiatorId !== userId) {
        const attachmentRecordIds = collectChatAttachmentRecordIds(messages);
        if (attachmentRecordIds.length > 0) {
          await syncConversationRecordPermissions({
            appConfig,
            records: 'attachments',
            method: HttpMethod.POST,
            grantorUserId: initiatorId,
            orgId,
            body: { userIds: [userId], recordIds: attachmentRecordIds },
            requestId,
            conversationId,
            timeoutMs: CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS,
            maxAttempts: 1,
          });
        }
      }

      const conversationWithMessages = attachMessages(session, messages);

      // Sort messages using existing helper
      const sortedMessages = sortMessages(
        (conversationWithMessages?.messages ||
          []) as unknown as IMessageDocument[],
        messageSortOptions as { field: keyof IMessage },
      );

      const conversationResponse = await attachSharedByIfRecipient(
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
        ),
        orgId,
      );
      // Another viewer sees only what they may read of each message.
      conversationResponse.messages = await redactMessagesFor(
        appConfig,
        viewerOf(userId, orgId),
        conversationResponse.messages,
        () => initiatorId,
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

export const deleteConversationById =
  (appConfig: AppConfig) =>
  (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> =>
    deleteConversation(appConfig, req, res, next);

const deleteConversation = async (
  appConfig: AppConfig,
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

    const ownerOrWriter: Record<string, unknown>[] = [
      { initiator: userId },
      { 'sharedWith.userId': userId as string, 'sharedWith.accessLevel': 'write' },
    ];
    const deletable: Record<string, unknown> = {
      _id: conversationId,
      userId,
      orgId,
      isDeleted: false,
      $or: ownerOrWriter,
      ...EXCLUDE_AGENT,
    };

    // The chat's artifacts go first, outside the transaction (a network call
    // would hold it open): if they cannot be deleted the chat stays, so a
    // retry finishes the cleanup instead of orphaning them.
    if (!(await ChatSession.findOne(deletable))) {
      throw new NotFoundError('Conversation not found');
    }
    await deleteConversationArtifacts(appConfig, String(conversationId), req);

    // Common helper that performs the delete operation.
    async function performDeleteConversation(session?: ClientSession | null) {
      // Get conversation with access control
      const conversation = await ChatSession.findOne(deletable, undefined, {
        session,
      });

      if (!conversation) {
        throw new NotFoundError('Conversation not found');
      }

      // Perform soft delete on the conversation
      const updatedConversation = await ChatSession.findOneAndUpdate(
        { _id: conversationId, orgId, ...EXCLUDE_AGENT },
        {
          $set: {
            isDeleted: true,
            deletedBy: userId,
            lastActivityAt: Date.now(),
          },
        },
        {
          returnDocument: 'after',
          session,
          runValidators: true,
        },
      );

      if (!updatedConversation) {
        throw new InternalServerError('Failed to delete conversation');
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
            _id: { $in: citationIds as Types.ObjectId[] },
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
      // withTransaction starts and commits (or aborts and retries) the transaction itself.
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

/**
 * Which sessions a share/unshare handler may touch. Plain chats and agent
 * chats live in one collection, so the scope is what keeps
 * `/conversations/:id/share` from sharing an agent chat and vice versa.
 */
type ShareScope = (req: AuthenticatedUserRequest) => Record<string, unknown>;

const plainChatShareScope: ShareScope = () => EXCLUDE_AGENT;
const agentChatShareScope: ShareScope = (req) => ({
  ...ONLY_AGENT,
  agentKey: req.params.agentKey,
});

/**
 * Shared by the plain-chat and agent-chat share routes. `readOnlyShares`
 * (agent chats) refuses any access level but 'read' and refuses sharing with
 * oneself: a recipient can open the transcript but never continue, regenerate,
 * rename, archive or delete it.
 */
const createShareConversationHandler =
  (
    appConfig: AppConfig,
    scopeFor: ShareScope,
    { readOnlyShares = false }: { readOnlyShares?: boolean } = {},
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    let session: ClientSession | null = null;
    const { conversationId } = req.params;
    let { userIds, accessLevel } = req.body;
    const scope = scopeFor(req);
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      logger.debug('Attempting to share conversation', {
        requestId,
        conversationId,
        userIds,
        accessLevel,
        timestamp: new Date().toISOString(),
      });

      // Validate request body
      if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
        throw new BadRequestError('userIds is required and must be an array');
      }

      if (accessLevel && !['read', 'write'].includes(accessLevel)) {
        throw new BadRequestError(
          "Invalid access level. Must be 'read' or 'write'",
        );
      }

      // An ObjectId is hex, so 'ABC…' and 'abc…' are the same user. Compare and de-duplicate on one form.
      userIds = (userIds as unknown[]).map((id) => String(id).toLowerCase());

      if (readOnlyShares) {
        if (accessLevel && accessLevel !== 'read') {
          throw new BadRequestError(
            "Agent conversations can only be shared with 'read' access",
          );
        }
        if ((userIds as string[]).includes(String(userId).toLowerCase())) {
          throw new BadRequestError(
            'You cannot share a conversation with yourself',
          );
        }
        userIds = [...new Set(userIds as string[])];
      }

      const recipientIds = userIds as string[];

      // Get conversation with access control
      const conversation = await ChatSession.findOne({
        _id: conversationId,
        orgId,
        userId,
        isDeleted: false,
        initiator: userId, // Only initiator can share
        ...scope,
      });

      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
      const ownedConversation: IChatSessionDocument = conversation;

      // The recipients are checked before the transaction opens: one query, and no network call
      // while the transaction is held.
      recipientIds.forEach((id) => {
        if (!mongoose.Types.ObjectId.isValid(id)) {
          throw new BadRequestError(`Invalid user ID format: ${id}`);
        }
      });
      const orgUserIds = await findOrgUserIds(orgId as string, recipientIds);
      const unknownId = recipientIds.find((id) => !orgUserIds.has(id));
      if (unknownId) {
        throw new BadRequestError(`User not found: ${unknownId}`);
      }
      const validUsers = recipientIds.map((id) => ({
        userId: new mongoose.Types.ObjectId(id),
        accessLevel: accessLevel || 'read',
      }));

      // Only users who did not already have access are told; re-sharing or a level change is not news.
      const alreadySharedIds = new Set(
        (ownedConversation.sharedWith || []).map((share) =>
          String(share.userId).toLowerCase(),
        ),
      );
      const newlySharedIds = recipientIds.filter(
        (id) => !alreadySharedIds.has(id),
      );

      async function performShareConversation(session?: ClientSession | null) {
        // Update object for conversation
        const updateObject: Partial<IConversation> = {
          isShared: true,
        };

        // Get existing shared users
        const existingSharedWith = ownedConversation.sharedWith || [];

        // Create a map of existing users for quick lookup
        const existingUserMap = new Map(
          existingSharedWith.map((share: any) => [
            share.userId.toString(),
            share,
          ]),
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

        // Update the conversation
        const updatedConversation = await ChatSession.findOneAndUpdate(
          { _id: conversationId, ...scope },
          updateObject,
          {
            returnDocument: 'after',
            session,
            runValidators: true,
          },
        );

        if (!updatedConversation) {
          throw new InternalServerError(
            'Failed to update conversation sharing settings',
          );
        }
        return updatedConversation;
      }

      let updatedConversation: IChatSessionDocument | null = null;
      if (rsAvailable) {
        session = await mongoose.startSession();
        session.startTransaction();
        updatedConversation = await performShareConversation(session);
        await session.commitTransaction();
      } else {
        updatedConversation = await performShareConversation();
      }

      // Grant READER on chat attachments (message chips and ATTACHMENTS citations).
      const attachmentRecordIds = await loadConversationChatAttachmentRecordIds(
        String(conversationId),
      );

      const grantor = {
        appConfig,
        method: HttpMethod.POST,
        grantorUserId: String(userId),
        orgId: String(orgId),
        requestId,
        conversationId,
      } as const;
      if (attachmentRecordIds.length > 0) {
        await syncConversationRecordPermissions({
          ...grantor,
          records: 'attachments',
          body: { userIds, recordIds: attachmentRecordIds },
        });
      }
      await syncConversationRecordPermissions({
        ...grantor,
        records: 'artifacts',
        body: { conversationId, userIds },
      });

      await notifyChatShared({
        orgId: String(orgId),
        ownerId: String(userId),
        recipientIds: newlySharedIds,
        conversationId: String(conversationId),
        chatTitle: updatedConversation.title,
        agentKey: scope.agentKey as string | undefined,
      });

      logger.debug('Conversation shared successfully', {
        requestId,
        conversationId,
        duration: Date.now() - startTime,
      });

      // Prepare response
      const response = {
        id: updatedConversation._id,
        isShared: updatedConversation.isShared,
        shareLink: updatedConversation.shareLink,
        sharedWith: updatedConversation.sharedWith,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      };

      res.status(200).json(response);
    } catch (error: any) {
      logger.error('Error sharing conversation', {
        requestId,
        message: 'Error sharing conversation',
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

type ConversationHandler = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

export const shareConversationById = (
  appConfig: AppConfig,
): ConversationHandler =>
  createShareConversationHandler(appConfig, plainChatShareScope);

/** Owner-only, read-only share of an agent conversation. */
export const shareAgentConversation = (
  appConfig: AppConfig,
): ConversationHandler =>
  createShareConversationHandler(appConfig, agentChatShareScope, {
    readOnlyShares: true,
  });

const createUnshareConversationHandler =
  (appConfig: AppConfig, scopeFor: ShareScope) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    let session: ClientSession | null = null;
    const { conversationId } = req.params;
    const scope = scopeFor(req);
    try {
      const { userIds } = req.body;
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      logger.debug('Attempting to unshare conversation', {
        requestId,
        conversationId,
        userId,
        timestamp: new Date().toISOString(),
      });

      // Validate request body
      if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
        throw new BadRequestError('userIds is required and must be an array');
      }

      // Validate all user IDs format
      userIds.forEach((id) => {
        if (!mongoose.Types.ObjectId.isValid(id)) {
          throw new BadRequestError(`Invalid user ID format: ${id}`);
        }
      });
      // An ObjectId is hex, so 'ABC…' and 'abc…' are the same user.
      const removedIds = (userIds as unknown[]).map((id) =>
        String(id).toLowerCase(),
      );

      async function performUnshareConversation(
        session?: ClientSession | null,
      ) {
        // Get conversation with access control
        const conversation = await ChatSession.findOne({
          _id: conversationId,
          orgId,
          isDeleted: false,
          initiator: userId, // Only initiator can unshare
          ...scope,
        });

        if (!conversation) {
          throw new NotFoundError('Conversation not found or unauthorized');
        }

        // Get existing shared users
        const existingSharedWith = conversation.sharedWith || [];

        // Remove specified users from sharedWith array
        const updatedSharedWith = existingSharedWith.filter(
          (share) => !removedIds.includes(share.userId.toString()),
        );

        // Prepare update object
        const updateObject: Partial<IConversation> = {
          sharedWith: updatedSharedWith,
        };

        // If no more shares exist, update isShared and remove shareLink
        if (updatedSharedWith.length === 0) {
          updateObject.isShared = false;
          updateObject.shareLink = undefined;
        }

        // Update the conversation
        const updatedConversation = await ChatSession.findOneAndUpdate(
          { _id: conversationId, ...scope },
          updateObject,
          {
            returnDocument: 'after',
            session,
            runValidators: true,
          },
        );

        if (!updatedConversation) {
          throw new InternalServerError(
            'Failed to update conversation sharing settings',
          );
        }
        return updatedConversation;
      }

      let updatedConversation: IChatSessionDocument | null = null;
      if (rsAvailable) {
        session = await mongoose.startSession();
        session.startTransaction();
        updatedConversation = await performUnshareConversation(session);
        await session.commitTransaction();
      } else {
        updatedConversation = await performUnshareConversation();
      }

      // Revoke READER on chat attachments (message chips and ATTACHMENTS citations).
      const attachmentRecordIds = await loadConversationChatAttachmentRecordIds(
        String(conversationId),
      );

      const grantor = {
        appConfig,
        method: HttpMethod.DELETE,
        grantorUserId: String(userId),
        orgId: String(orgId),
        requestId,
        conversationId,
      } as const;
      if (attachmentRecordIds.length > 0) {
        await syncConversationRecordPermissions({
          ...grantor,
          records: 'attachments',
          body: { userIds: removedIds, recordIds: attachmentRecordIds },
        });
      }
      await syncConversationRecordPermissions({
        ...grantor,
        records: 'artifacts',
        body: { conversationId, userIds: removedIds },
      });

      logger.debug('Conversation unshared successfully', {
        requestId,
        conversationId,
        duration: Date.now() - startTime,
      });

      // Prepare response
      const response = {
        id: updatedConversation._id,
        isShared: updatedConversation.isShared,
        shareLink: updatedConversation.shareLink,
        sharedWith: updatedConversation.sharedWith,
        unsharedUsers: removedIds,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      };

      res.status(200).json(response);
    } catch (error: any) {
      logger.error('Error un-sharing conversation', {
        requestId,
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

export const unshareConversationById = (
  appConfig: AppConfig,
): ConversationHandler =>
  createUnshareConversationHandler(appConfig, plainChatShareScope);

export const unshareAgentConversation = (
  appConfig: AppConfig,
): ConversationHandler =>
  createUnshareConversationHandler(appConfig, agentChatShareScope);

/**
 * PUT /api/v1/conversations/:conversationId/project
 * PUT /api/v1/agents/:agentKey/conversations/:conversationId/project
 * @desc Link or unlink a chat/agent session to a project. Initiator-only
 * (mirrors `shareConversationById`'s ownership check) — a chat shared to
 * this user does not let them move someone else's conversation between
 * projects. `projectId: null` unlinks. `ProjectService.assertAccess`
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
      _id: conversationId,
      orgId,
      userId,
      initiator: userId,
      isDeleted: false,
      ...sessionTypeFilter,
    });
    if (!conversation) {
      throw new NotFoundError('Conversation not found or unauthorized');
    }

    let update: Record<string, unknown>;
    if (projectId === null) {
      update = { $unset: { projectId: '', projectVisibility: '' } };
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
      };
      void ProjectService.touchActivity(projectId);
    }

    const updated = await ChatSession.findOneAndUpdate(
      { _id: conversationId, ...sessionTypeFilter },
      update,
      { returnDocument: 'after' },
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
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { conversationId, agentKey } = req.params;
    const { visibility } = req.body as { visibility: 'private' | 'project' };

    const sessionTypeFilter = agentKey
      ? { ...ONLY_AGENT, agentKey }
      : EXCLUDE_AGENT;

    const conversation = await ChatSession.findOne({
      _id: conversationId,
      orgId,
      userId,
      initiator: userId,
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
      { $set: { projectVisibility: visibility } },
      { returnDocument: 'after' },
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

/** The app's answer to a tool approval card (Python `TOOL_APPROVAL_RESUME_PREFIX`): it carried on the turn that stopped on the card. */
export function isToolApprovalAnswer(
  query: string | undefined | null,
): boolean {
  return (
    typeof query === 'string' && query.trimStart().startsWith('Tool approval:')
  );
}

/** The answers to a question card (Python `ASK_USER_QUESTION_RESUME_PREFIX`): saved as a question of their own, shown on the card's row. */
export function isAskUserQuestionAnswer(
  query: string | undefined | null,
): boolean {
  return (
    typeof query === 'string' &&
    query.trimStart().startsWith('User selections:')
  );
}

/** The question a turn was first asked, and what the turn saved after it, when it went through a question card. */
interface CardTurnQuestion {
  question: IMessage & { _id: mongoose.Types.ObjectId };
  /** Rows between that question and the turn's last answer: the card, its answers, earlier replies. */
  intermediateIds: mongoose.Types.ObjectId[];
  /** Whether the turn's answers each follow a stored question card. Text alone does not make a card turn. */
  hasCard: boolean;
}

/** Whether a row holds the questions of a card: stamped on a reply, or saved as a `tool_call` row. */
const rowHoldsQuestionCard = (row: IMessage): boolean =>
  hasAskUserQuestionTool(row) ||
  (row.parts ?? []).some(
    (part) =>
      part.type === 'tool_call' &&
      Boolean(part.toolName?.includes('ask_user_question')),
  );

/**
 * A card turn is saved as several rows: the question, a reply holding the card, the card's answers
 * (a `user_query` of their own) and the reply to them. The chat shows them as one question, so an
 * edit must reach the first row, not the answers.
 *
 * @param older the rows before the turn's last answer, newest first
 * @returns null when no question of the turn is found, or the turn answered an approval card
 */
export function findQuestionBehindCard(
  older: Array<IMessage & { _id: mongoose.Types.ObjectId }>,
): CardTurnQuestion | null {
  const intermediateIds: mongoose.Types.ObjectId[] = [];
  let hasCard = false;
  for (const [i, row] of older.entries()) {
    if (row.messageType === 'user_query') {
      if (
        isAskUserQuestionAnswer(row.content) &&
        (answersCardBefore(older, i) || repeatsCardAnswers(older, i))
      ) {
        intermediateIds.push(row._id);
        hasCard = true;
        continue;
      }
      if (isToolApprovalAnswer(row.content)) return null;
      return { question: row, intermediateIds, hasCard };
    }
    intermediateIds.push(row._id);
  }
  return null;
}

/**
 * Whether the row of answers at `older[i]` answers a card: the reply just before it holds one, or the
 * `tool_call` rows beside that reply do. A card an earlier turn asked does not make a question the
 * reader typed with the same first words into answers.
 */
function answersCardBefore(
  older: Array<IMessage & { _id: mongoose.Types.ObjectId }>,
  i: number,
): boolean {
  for (const row of older.slice(i + 1)) {
    if (rowHoldsQuestionCard(row)) return true;
    if (row.messageType !== 'tool_call') return false;
  }
  return false;
}

/**
 * The questions a row of card answers quotes, when the whole row is written the way the app
 * writes them (`User selections:` then `1. "question" → answer`, one line per question); empty
 * when any line is something else, so text typed around answers is never read as answers.
 */
function quotedCardQuestions(content: string | undefined | null): Set<string> {
  const questions = new Set<string>();
  const body = (content ?? '').trimStart().replace(/^User selections:\s*/, '');
  const lines = body.split('\n').filter((line) => line.trim() !== '');
  for (const [index, line] of lines.entries()) {
    const match = /^(\d+)\.\s+"(.+?)"\s+→/.exec(line.trim());
    const question = match?.[2]?.trim();
    if (!question || Number(match?.[1]) !== index + 1) return new Set<string>();
    questions.add(question);
  }
  return questions;
}

/**
 * Whether the row of answers at `older[i]` answers again a card the same turn already answered:
 * a card can be sent twice, and the second send follows a reply with no card next to it. No row
 * records which send made it, so the text decides: written as the app writes answers, and every
 * question it quotes answered earlier in the turn by answers that do follow a card. A question
 * the reader typed with the same first words, or that asks anything else, stays its own.
 */
function repeatsCardAnswers(
  older: Array<IMessage & { _id: mongoose.Types.ObjectId }>,
  i: number,
): boolean {
  const quoted = quotedCardQuestions(older[i]?.content);
  if (quoted.size === 0) return false;
  const answeredInTurn = new Set<string>();
  for (let j = i + 1; j < older.length; j += 1) {
    const row = older[j];
    if (row?.messageType !== 'user_query') continue;
    if (!isAskUserQuestionAnswer(row.content) || !answersCardBefore(older, j))
      break;
    for (const question of quotedCardQuestions(row.content)) {
      answeredInTurn.add(question);
    }
  }
  return [...quoted].every((question) => answeredInTurn.has(question));
}

/** Whether two lists name the same files, whatever their order. */
export function sameAttachmentFiles(
  a: Array<{ recordId: string }> | undefined | null,
  b: Array<{ recordId: string }> | undefined | null,
): boolean {
  const key = (list: Array<{ recordId: string }> | undefined | null): string =>
    (list ?? [])
      .map((file) => file.recordId)
      .sort()
      .join('\n');
  return key(a) === key(b);
}

/** The files of a request once each file appears only once, in the order first named. */
export function uniqueAttachmentFiles<T extends { recordId: string }>(
  files: T[],
): T[] {
  const seen = new Set<string>();
  return files.filter((file) => {
    if (seen.has(file.recordId)) return false;
    seen.add(file.recordId);
    return true;
  });
}

/** Whether a reply's transcript holds a tool call a person approved (`IMessagePart.approved`). */
export function ranApprovedCall(
  parts: IMessagePart[] | undefined | null,
): boolean {
  return (parts ?? []).some(
    (part) =>
      (part.type === 'tool_call' && part.approved === true) ||
      ranApprovedCall(part.parts),
  );
}

/**
 * Generic internal function to handle regeneration for both regular and agent conversations
 */
async function regenerateAnswersInternal(
  appConfig: AppConfig,
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
  // Changed answers to the card this reply followed; they take the place of the saved ones.
  const regenAnswersBody = req.body as { answers?: unknown } | undefined;
  const replacementAnswers: string | undefined =
    typeof regenAnswersBody?.answers === 'string'
      ? regenAnswersBody.answers
      : undefined;

  let existingConversation: IChatSessionDocument | null = null;
  let reservation: RunReservation | null = null;

  const modelInfo = extractModelInfo(req.body);
  const protocol = resolveProtocol(
    req.body as Record<string, unknown>,
    req.query as Record<string, unknown>,
  );

  // Helper function to validate and get conversation
  async function performRegenerateAnswersValidation(
    session?: ClientSession | null,
  ): Promise<{
    conversation: IChatSessionDocument;
    userQuery: IMessage;
    staleAskToolCallIds: mongoose.Types.ObjectId[];
    cardEdit: CardTurnQuestion | null;
    editBlocked: boolean;
    newerQuestion: boolean;
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

    // Newest-first tail (not just the last 2 rows): an ask_user_question turn
    // ends with `tool_call` rows saved after its answer, so the answer being
    // regenerated is not necessarily the newest message.
    const recentMessages = (await getMessages(
      conversation._id as mongoose.Types.ObjectId,
      { orgId: conversation.orgId, limit: REGENERATE_TAIL_MESSAGES, sort: -1 },
      session,
    )) as Array<IMessage & { _id: mongoose.Types.ObjectId }>;

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
    if (ranApprovedCall(lastBot.parts)) {
      throw new BadRequestError(
        'This reply ran an action you approved. Regenerating it could run that action again, so ask again instead.',
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
    if (isToolApprovalAnswer(userQuery.content)) {
      throw new BadRequestError(
        'This reply came from your answer to an approval request, so it cannot be regenerated. Ask again instead.',
      );
    }
    // The stored question here is the card's answers, which the chat shows as part of the question
    // before it, so an edit goes to that question and the whole card turn is asked again.
    // Whether the request is an edit is decided by the caller; a missing question only matters then.
    const looksLikeCardAnswer = isAskUserQuestionAnswer(userQuery.content);
    const behindCard = looksLikeCardAnswer
      ? findQuestionBehindCard(recentMessages.slice(lastBotIdx + 1))
      : null;
    // Only a stored card makes a card turn: a question the reader typed with the same first words has none.
    const cardEdit = behindCard?.hasCard ? behindCard : null;
    const editBlocked = looksLikeCardAnswer && behindCard === null;
    // A question after the newest answer has none of its own (a run that never got to answer), so
    // the answer found here belongs to an older question than the last one.
    const newerQuestion = recentMessages
      .slice(0, lastBotIdx)
      .some((msg) => msg.messageType === 'user_query');
    // Only a stored card makes these answers: text that starts the same way has no card to re-answer.
    if (replacementAnswers && !cardEdit) {
      throw new BadRequestError(
        'Answers can only replace the answers to a question card.',
      );
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

    return {
      conversation,
      userQuery,
      staleAskToolCallIds,
      cardEdit,
      editBlocked,
      newerQuestion,
    };
  }

  try {
    // Initialize SSE response
    initializeSSEResponse(res, protocol);

    logger.debug('Attempting to regenerate answers via stream', {
      requestId,
      conversationId,
      messageId,
      timestamp: new Date().toISOString(),
    });

    if (!conversationId) {
      throw new BadRequestError('Conversation ID is required');
    }
    reservation = await reserveRun(
      config.buildQueryFilter(
        conversationId,
        orgId as string | undefined,
        userId as string | undefined,
        agentKey,
      ) as QueryFilter<IChatSessionDocument>,
    );

    // Validate conversation and message
    let validationResult: {
      conversation: IChatSessionDocument;
      userQuery: IMessage;
      staleAskToolCallIds: mongoose.Types.ObjectId[];
      cardEdit: CardTurnQuestion | null;
      editBlocked: boolean;
      newerQuestion: boolean;
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
    const userQuery = validationResult.userQuery;
    // Only an edit carries these two; their types are not trusted until read.
    const editBody = req.body as { query?: unknown; attachments?: unknown };
    const requestedQuery =
      typeof editBody.query === 'string' ? editBody.query.trim() : '';
    if (
      requestedQuery &&
      (isToolApprovalAnswer(requestedQuery) ||
        isAskUserQuestionAnswer(requestedQuery))
    ) {
      throw new BadRequestError('This question cannot be used as an edit.');
    }
    // The question the chat shows: for a card turn, the first one, not the card's answers.
    const cardEdit = validationResult.cardEdit;
    const shownQuestion = cardEdit?.question ?? userQuery;
    const requestedAttachments: IChatAttachmentRef[] | undefined =
      Array.isArray(editBody.attachments)
        ? uniqueAttachmentFiles(editBody.attachments as IChatAttachmentRef[])
        : undefined;
    const textChanged =
      Boolean(requestedQuery) &&
      requestedQuery !== shownQuestion.content.trim();
    const attachmentsChanged =
      requestedAttachments !== undefined &&
      !sameAttachmentFiles(requestedAttachments, shownQuestion.attachments);
    // An unchanged edit is a plain regenerate; only a real change rewrites the stored question.
    const isEdit = textChanged || attachmentsChanged;
    if (isEdit && replacementAnswers) {
      throw new BadRequestError(
        'An edit cannot also replace the answers to a question card.',
      );
    }
    if (isEdit && validationResult.editBlocked) {
      throw new BadRequestError(
        'This question cannot be edited. Ask again instead.',
      );
    }
    if (isEdit && validationResult.newerQuestion) {
      throw new BadRequestError(
        'Can only edit the last question in the conversation',
      );
    }
    const editedQuery = isEdit ? requestedQuery || shownQuestion.content : null;
    // A card turn is asked again from its first question, edited or not: redoing only the reply to
    // the stored answers would keep the old card and its answers, with no way to choose again.
    // Changed answers (`replacementAnswers`) redo the reply to the card instead, so they keep the turn.
    const turnQuery =
      editedQuery ??
      (cardEdit && !replacementAnswers ? shownQuestion.content : null);
    const editedAttachments = attachmentsChanged
      ? requestedAttachments
      : undefined;
    const staleAskToolCallIds = validationResult.staleAskToolCallIds;
    // Set only once the request is accepted: an error that reaches the catch below with a
    // conversation in hand replaces the stored answer, which a refused edit must not do.
    existingConversation = validationResult.conversation;

    // Format previous conversations up to this message (exclude last bot
    // response and the user query that triggered it)
    const allMessagesForRegen = (await getMessages(
      existingConversation._id,
      { orgId: existingConversation.orgId },
      session,
    )) as Array<IMessage & { _id?: mongoose.Types.ObjectId }>;
    // The turn being regenerated (its user query, its answer, and any
    // `tool_call` rows saved after it) is excluded by position, not by a
    // fixed `slice(0, -2)`: an ask_user_question turn ends with tool rows.
    const regenBotIdx = allMessagesForRegen.findIndex(
      (msg) => msg._id?.toString() === String(messageId),
    );
    let regenUserIdx = regenBotIdx - 1;
    while (
      regenUserIdx >= 0 &&
      allMessagesForRegen[regenUserIdx]?.messageType !== 'user_query'
    ) {
      regenUserIdx -= 1;
    }
    if (turnQuery !== null && cardEdit) {
      const questionIdx = allMessagesForRegen.findIndex(
        (msg) => msg._id?.toString() === cardEdit.question._id.toString(),
      );
      if (questionIdx >= 0) regenUserIdx = questionIdx;
    }
    const previousConversations = formatPreviousConversations(
      regenUserIdx >= 0
        ? allMessagesForRegen.slice(0, regenUserIdx)
        : allMessagesForRegen.slice(0, -2),
    );

    // The title of a chat is its first question, so editing that question moves the title too,
    // unless the user already renamed the chat.
    const titleFollowsEdit =
      regenUserIdx === 0 &&
      existingConversation.title === shownQuestion.content.slice(0, 100);
    // The rows a card turn asked again leaves behind: its card, the card's answers, earlier replies.
    // They go only after the new answer is saved, so a failed save loses nothing but the old answer.
    const replacedTurnIds =
      turnQuery !== null && cardEdit ? cardEdit.intermediateIds : [];
    const originalTitle = existingConversation.title;
    let editedQuestionSaved = false;
    let answersReplaced = false;
    /** Set once the new answer stands for the turn, so the rows it replaced may go. */
    let turnAnswered = false;
    const saveEditedQuestion = async (): Promise<void> => {
      if (!existingConversation) return;
      if (!editedQuery) {
        turnAnswered = true;
        return;
      }
      const editedRow = await updateUserQueryContent(
        (shownQuestion as IMessage & { _id: mongoose.Types.ObjectId })._id,
        editedQuery,
        existingConversation._id,
        session,
        editedAttachments,
      );
      if (!editedRow) {
        throw new InternalServerError('Failed to save the edited question');
      }
      // Only now: a failed save keeps the old card rows, as the question kept its old text.
      editedQuestionSaved = true;
      turnAnswered = true;
      if (titleFollowsEdit) {
        existingConversation.title = editedQuery.slice(0, 100);
      }
    };
    // A save that failed after the question was rewritten leaves the question as the reader had it,
    // not edited and without an answer.
    const restoreOriginalQuestion = async (): Promise<void> => {
      turnAnswered = false;
      if (answersReplaced && existingConversation) {
        answersReplaced = false;
        try {
          await updateUserQueryContent(
            (userQuery as IMessage & { _id: mongoose.Types.ObjectId })._id,
            userQuery.content,
            existingConversation._id,
            session,
          );
        } catch (err: unknown) {
          logger.error('Failed to put the original answers back', {
            requestId,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }
      if (!editedQuestionSaved || !existingConversation) return;
      editedQuestionSaved = false;
      existingConversation.title = originalTitle;
      try {
        await updateUserQueryContent(
          (shownQuestion as IMessage & { _id: mongoose.Types.ObjectId })._id,
          shownQuestion.content,
          existingConversation._id,
          session,
          editedAttachments ? (shownQuestion.attachments ?? []) : undefined,
        );
      } catch (err: unknown) {
        logger.error('Failed to put the original question back', {
          requestId,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    };
    // The rows the new answer replaced go once it is saved, and never fail it.
    const dropReplacedTurn = async (): Promise<boolean> => {
      if (!turnAnswered || !existingConversation) return false;
      if (replacedTurnIds.length === 0) return false;
      try {
        await deleteMessagesById(
          replacedTurnIds,
          existingConversation.orgId,
          session,
          existingConversation._id,
        );
        return true;
      } catch (err: unknown) {
        logger.warn('Failed to drop the rows an edited turn replaced', {
          requestId,
          error: err instanceof Error ? err.message : String(err),
        });
        return false;
      }
    };

    // For the assistant (non-agent-key) path, detect universal agent mode from chatMode
    // so the regenerate request is routed to the correct backend endpoint and carries tools.
    const { chatMode: parsedRegenChatMode, agentMode: regenIsAgentMode } =
      parseChatMode((req.body as { chatMode?: string }).chatMode);

    // Prepare AI payload
    const aiPayload: Record<string, unknown> = {
      query: turnQuery ?? replacementAnswers ?? userQuery.content,
      previousConversations: previousConversations || [],
      filters: req.body.filters || {},
      // The files of the question being asked: the edit's, else those stored with it.
      attachments:
        editedAttachments ??
        (turnQuery !== null ? shownQuestion : userQuery).attachments ??
        [],
      // New fields for multi-model support
      modelKey: req.body.modelKey || null,
      modelName: req.body.modelName || null,
      modelFriendlyName: req.body.modelFriendlyName || null,
      reasoningEffort: req.body.reasoningEffort || null,
      chatMode: parsedRegenChatMode,
      conversationId: conversationId || null,
      // A new id per press, so the answer on screen is the newest id of the question.
      regenerationId: new Types.ObjectId().toString(),
      timezone: req.body.timezone || null,
      currentTime: req.body.currentTime || null,
      runId: req.body.runId || null,
      ...(isAGUI(protocol) ? { protocol: AGUI_PROTOCOL } : {}),
    };
    if (agentKey || regenIsAgentMode) {
      assignToolsToPayload(aiPayload, req.body.tools);
      assignAgentCapabilitiesToPayload(
        aiPayload,
        req.body as Record<string, unknown>,
      );
    }
    const regenProject = await loadProjectForSession(
      existingConversation.orgId.toString(),
      existingConversation.userId.toString(),
      existingConversation.projectId,
    );
    applyProjectScope(aiPayload, regenProject);
    if (existingConversation.projectId) {
      void ProjectService.touchActivity(
        existingConversation.projectId.toString(),
      );
    }

    const regenEndpoint = regenIsAgentMode
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
    let completeData: IAIResponse | null = null;
    let askUserQuestionPayload: unknown = null;
    let buffer = '';
    /** True when the AI backend already sent a terminal error we forwarded and saved */
    let upstreamAiErrorEventForwarded = false;
    /** Guards `onAbandon` against also running after the normal
     * `stream.on('end')`/`'error'` path already finalized this run. */
    let streamSettled = false;
    const contentAccumulator = new StreamedContentAccumulator();

    const upstreamAbort = attachUpstreamAbort(res, requestId, () => {
      if (streamSettled || !existingConversation) return;
      if (completeData || upstreamAiErrorEventForwarded) {
        runGuarded(finalizeRun, { requestId });
        return;
      }
      streamSettled = true;
      const abandoned = existingConversation;
      const partialText = contentAccumulator.getText();
      // A partial answer was shown for the edited question, so the question is saved with it.
      void (partialText ? saveEditedQuestion() : Promise.resolve())
        .catch((err: unknown) => {
          logger.error('Failed to save the edited question on disconnect', {
            requestId,
            error: err instanceof Error ? err.message : String(err),
          });
        })
        .then(() =>
          savePartialConversation(abandoned, partialText, null, {
            replaceMessageId: messageId || undefined,
            parts: contentAccumulator.getParts(),
          }),
        )
        .then(async () => {
          await dropReplacedTurn();
        })
        .catch((err: unknown) => {
          logger.error('Failed to save partial conversation on disconnect', {
            requestId,
            error: err instanceof Error ? err.message : String(err),
          });
        })
        .finally(() => releaseRun(reservation));
    });
    const stream = await startAIStream(
      aiCommandOptions,
      'Regenerate Answers Stream',
      { requestId },
      upstreamAbort.signal,
    );

    if (!stream) {
      if (upstreamAbort.isAborted()) return;
      throw new Error('Failed to get stream from AI service');
    }
    upstreamAbort.bindStream(stream);
    keepRunAlive(stream, existingConversation._id, reservation?.runId);

    // Process SSE events, capture complete event, and forward non-complete events
    stream.on('data', (chunk: Buffer) => {
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
      );
    });

    const finalizeRun = async (): Promise<void> => {
      streamSettled = true;
      logger.debug('Stream ended successfully', { requestId });
      try {
        // Save the AI response to the conversation, replacing the existing message
        // A failed save is handled once, by the catch below: one error frame, one saved reason.
        if (completeData && existingConversation) {
          // A run that ended in an error answer, or in none, did not answer the edited question: it
          // stays as it was, with the rows of its card turn, and only the answer is replaced.
          const answeredEdit =
            !isClassifiedFailureAnswer(completeData) &&
            (Boolean(completeData.answer.trim()) ||
              Boolean(askUserQuestionPayload) ||
              completeData.status === 'waiting_input');
          if (answeredEdit) await saveEditedQuestion();
          // The answers the new reply is for replace the saved ones, so a reload shows the card as answered now.
          if (answeredEdit && replacementAnswers) {
            const answersRow = await updateUserQueryContent(
              (userQuery as IMessage & { _id: mongoose.Types.ObjectId })._id,
              replacementAnswers,
              existingConversation._id,
              session,
            );
            if (!answersRow) {
              throw new InternalServerError(
                'Failed to save the replacement answers',
              );
            }
            answersReplaced = true;
          }
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
            );
          if (await dropReplacedTurn()) {
            const shown = responseConversation as {
              messages: Array<{ _id?: unknown }>;
            };
            const gone = new Set(replacedTurnIds.map(String));
            shown.messages = shown.messages.filter(
              (row) => !gone.has(String(row._id)),
            );
          }

          // Send final response event with the complete conversation data
          sendSSECompleteEvent(
            res,
            responseConversation,
            savedCitations.length,
            requestId || '',
            startTime,
            protocol,
          );

          logger.debug(
            'Answer regenerated and conversation updated, sent custom complete event',
            {
              requestId,
              conversationId: existingConversation._id,
              messageId,
              duration: Date.now() - startTime,
            },
          );
        } else if (!upstreamAiErrorEventForwarded) {
          // Mark as failed if no complete data received
          if (existingConversation && messageId) {
            const errorMessage = CHAT_ERROR_MESSAGES.interrupted;
            await replaceMessageWithError(
              existingConversation,
              messageId,
              errorMessage,
              session,
              'no_response',
            );

            // Reload conversation to get updated state
            const updatedConversation = await ChatSession.findById(
              conversationId || '',
            );
            if (updatedConversation) {
              const messages = await getMessages(
                updatedConversation._id as mongoose.Types.ObjectId,
                { orgId: updatedConversation.orgId },
                session,
              );
              const plainConversation = attachMessages(
                updatedConversation.toObject(),
                messages,
              );
              await sendSSEErrorEvent(
                res,
                errorMessage,
                undefined,
                plainConversation,
                protocol,
              );
            } else {
              await sendSSEErrorEvent(
                res,
                errorMessage,
                undefined,
                undefined,
                protocol,
              );
            }
          } else {
            await sendSSEErrorEvent(
              res,
              CHAT_ERROR_MESSAGES.interrupted,
              undefined,
              undefined,
              protocol,
            );
          }
        }
      } catch (dbError: any) {
        logger.error(
          'Failed to save regenerated AI response to conversation',
          {
            requestId,
            conversationId: existingConversation?._id,
            error: dbError.message,
          },
        );
        await restoreOriginalQuestion();

        const errorMessage =
          causeCode(dbError) === 'ECONNREFUSED'
            ? CHAT_ERROR_MESSAGES.unavailable
            : CHAT_ERROR_MESSAGES.saveFailed;

        // Try to replace message with error if we have the message id
        if (existingConversation && messageId) {
          try {
            await replaceMessageWithError(
              existingConversation,
              messageId,
              errorMessage,
              session,
              'save_error',
              dbError.stack,
            );

            // Reload conversation to get updated state
            const updatedConversation = await ChatSession.findById(
              conversationId || '',
            );
            if (updatedConversation) {
              const messages = await getMessages(
                updatedConversation._id as mongoose.Types.ObjectId,
                { orgId: updatedConversation.orgId },
                session,
              );
              const plainConversation = attachMessages(
                updatedConversation.toObject(),
                messages,
              );
              await sendSSEErrorEvent(
                res,
                errorMessage,
                dbError.message,
                plainConversation,
                protocol,
              );
            } else {
              await sendSSEErrorEvent(
                res,
                errorMessage,
                dbError.message,
                undefined,
                protocol,
              );
            }
          } catch (replaceError: any) {
            logger.error(
              'Failed to replace message with error in dbError catch',
              {
                requestId,
                error: replaceError.message,
              },
            );
            await sendSSEErrorEvent(
              res,
              errorMessage,
              dbError.message,
              undefined,
              protocol,
            );
          }
        } else {
          await sendSSEErrorEvent(
            res,
            errorMessage,
            dbError.message,
            undefined,
            protocol,
          );
        }
      }

      await releaseRun(reservation);
      res.end();
    };
    stream.on('end', () => {
      runGuarded(finalizeRun, { requestId });
    });

    stream.on('error', async (error: Error) => {
      if (upstreamAbort.isAborted()) {
        logger.debug('Stream abandoned after the detached-run cap', {
          requestId,
        });
        return;
      }
      streamSettled = true;
      logger.error('Stream error in regenerateAnswers', {
        requestId,
        error: error.message,
      });
      try {
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
        );
      } catch (dbError: any) {
        logger.error('Failed to replace message with error', {
          requestId,
          conversationId: existingConversation?._id,
          error: dbError.message,
        });
        await sendSSEErrorEvent(
          res,
          userFacingChatError(error),
          error.message,
          undefined,
          protocol,
        );
      }
      await releaseRun(reservation);
      res.end();
    });
  } catch (error: any) {
    const level = logLevelFor(error);
    logger[level]('Error in regenerateAnswers', {
      requestId,
      conversationId,
      messageId,
      error: error.message,
      ...(level === 'error' ? { stack: error.stack } : {}),
    });

    if (!res.headersSent) {
      res.writeHead(500, {
        'Content-Type': 'text/event-stream; charset=utf-8',
      });
    }

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
      );
    } catch (dbError: any) {
      logger.error('Failed to mark conversation as failed in catch block', {
        requestId,
        conversationId,
        error: dbError.message,
      });
      await sendSSEErrorEvent(
        res,
        userFacingChatError(error),
        error.message,
        undefined,
        protocol,
      );
    }
    await releaseRun(reservation);
    res.end();
  } finally {
    if (session) {
      session.endSession();
      session = null;
    }
  }
}

export const regenerateAnswers =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response) => {
    await regenerateAnswersInternal(appConfig, req, res, {
      isAgentSession: false,
      buildQueryFilter: (conversationId, orgId, userId) => ({
        _id: conversationId,
        orgId,
        userId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
      }),
      buildAIEndpoint: (appConfig) =>
        `${appConfig.aiBackend}/api/v1/chat/stream`,
    });
  };

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
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    try {
      const conversation = await ChatSession.findOne({
        _id: conversationId,
        orgId,
        userId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
      });
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/chat/cancel`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        // conversationId is forwarded (not just runId) so Python's own
        // RunOwner check can reject a runId that belongs to a DIFFERENT
        // conversation this same user owns — the Mongo lookup above only
        // proves the caller owns `conversationId`, not that `runId` was
        // ever issued for it.
        body: { runId, conversationId },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
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
          _id: conversationId,
          orgId,
          userId,
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

interface ForkConversationBody {
  id: string;
  title: string | undefined;
  projectId: string | undefined;
  forkedFrom: { conversationId: string; messageId: string };
  messageCount: number;
  meta: { requestId: string | undefined; timestamp: string; duration: number };
}

const forkConversationResponse = (
  { conversation, messageCount }: ForkChatSessionResult,
  conversationId: string,
  messageId: string,
  requestId: string | undefined,
  startTime: number,
): ForkConversationBody => ({
  id: String(conversation._id),
  title: conversation.title,
  projectId: conversation.projectId
    ? String(conversation.projectId)
    : undefined,
  forkedFrom: { conversationId, messageId },
  messageCount,
  meta: {
    requestId,
    timestamp: new Date().toISOString(),
    duration: Date.now() - startTime,
  },
});

interface ForkTarget {
  /** Names the kind of chat in the error log. */
  label: string;
  fields: Omit<ForkSessionFields, 'projectId'>;
  findSource: (
    orgId: string,
    userId: string,
    conversationId: string,
  ) => Promise<IChatSessionDocument | null>;
  copyArtifacts: ArtifactCopier;
  /** Removes the artifacts copied under a fork that could not be created. */
  discardArtifacts: (forkId: string) => Promise<void>;
}

const FORK_ARTIFACT_COPY_BATCH = 50;
// Copying re-uploads every version's bytes, so allow far longer than a lookup.
const FORK_ARTIFACT_COPY_TIMEOUT_MS = 180_000;

/**
 * Asks the connectors service to copy artifacts as the caller. One attempt
 * only (`retries` counts attempts): a repeat after a timeout would leave a
 * second, unused copy.
 */
export const forkArtifactCopier =
  (appConfig: AppConfig, req: AuthenticatedUserRequest): ArtifactCopier =>
  async (conversationId, refs) => {
    const outcome: ArtifactCopyOutcome = { copies: [], failed: [] };
    for (let i = 0; i < refs.length; i += FORK_ARTIFACT_COPY_BATCH) {
      const response = await executeConnectorCommand<ArtifactCopyOutcome>(
        `${appConfig.connectorBackend}/api/v1/artifacts/copy`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        {
          conversationId,
          artifacts: refs.slice(i, i + FORK_ARTIFACT_COPY_BATCH),
        },
        { retries: 1, timeoutMs: FORK_ARTIFACT_COPY_TIMEOUT_MS },
      );
      if (
        response.statusCode < 200 ||
        response.statusCode >= 300 ||
        !response.data
      ) {
        throw handleBackendError(response, 'copy artifacts for fork');
      }
      outcome.copies.push(...response.data.copies);
      outcome.failed.push(...response.data.failed);
    }
    return outcome;
  };

const runFork = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  target: ForkTarget,
): Promise<void> => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  const conversationId = req.params.conversationId ?? '';
  const messageId = req.params.messageId ?? '';
  // Allocated up front: the artifacts are copied under this id before the chat
  // exists, and a retried transaction must reuse them, not copy again.
  const forkId = new mongoose.Types.ObjectId();
  const copy = { started: false };
  try {
    const userId = req.user?.userId as string | undefined;
    const orgId = req.user?.orgId as string | undefined;
    if (!conversationId || !messageId) {
      throw new BadRequestError('conversationId and messageId are required');
    }
    if (!userId || !orgId) {
      throw new BadRequestError('User ID and Organization ID are required');
    }

    const source = await target.findSource(orgId, userId, conversationId);
    if (!source) {
      throw new NotFoundError('Conversation not found');
    }

    const fields: ForkSessionFields = {
      ...target.fields,
      projectId: await resolveForkProjectId(source, orgId, userId),
    };
    // Outside the transaction: this call can take minutes, longer than MongoDB
    // lets a transaction live.
    const artifacts = await copyArtifactsForFork(
      source,
      messageId,
      forkId,
      (id, refs) => {
        copy.started = true;
        return target.copyArtifacts(id, refs);
      },
    );
    const performFork = (
      txnSession: ClientSession | null,
    ): Promise<ForkChatSessionResult> =>
      copySessionUpToMessage(
        source,
        messageId,
        userId,
        orgId,
        fields,
        txnSession,
        artifacts,
      );

    let result: ForkChatSessionResult;
    if (rsAvailable) {
      session = await mongoose.startSession();
      result = await session.withTransaction(() => performFork(session));
    } else {
      result = await performFork(null);
    }

    res
      .status(HTTP_STATUS.CREATED)
      .json(
        forkConversationResponse(
          result,
          conversationId,
          messageId,
          requestId,
          startTime,
        ),
      );
  } catch (error) {
    const failure = error instanceof Error ? error : new Error(String(error));
    logger.error(`Error forking ${target.label}`, {
      requestId,
      conversationId,
      messageId,
      error: failure.message,
      stack: failure.stack,
      duration: Date.now() - startTime,
    });
    if (copy.started) {
      // The artifacts live outside MongoDB, so no rollback reaches them.
      try {
        await target.discardArtifacts(String(forkId));
      } catch (cleanupError) {
        logger.warn('Could not remove the artifacts of a failed fork', {
          requestId,
          forkId: String(forkId),
          error:
            cleanupError instanceof Error
              ? cleanupError.message
              : String(cleanupError),
        });
      }
    }
    next(error);
  } finally {
    if (session) {
      await session.endSession();
      session = null;
    }
  }
};

/**
 * Copy a conversation into a new private chat owned by the caller, including
 * every message up to and including `messageId`. The original is unchanged.
 *
 * Owner only: a shared recipient's access to the original's attachments and
 * artifacts is revoked on unshare, which would leave a fork pointing at files
 * it can no longer open.
 */
export const forkConversation =
  (appConfig: AppConfig) =>
  (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> =>
    runFork(req, res, next, {
      label: 'conversation',
      fields: { sessionType: 'chat' },
      copyArtifacts: forkArtifactCopier(appConfig, req),
      discardArtifacts: (forkId) =>
        deleteConversationArtifacts(appConfig, forkId, req),
      findSource: (orgId, userId, conversationId) =>
        ChatSession.findOne({
          ...(buildFilter(
            req,
            orgId,
            userId,
            conversationId,
            true,
            false,
          ) as QueryFilter<IChatSessionDocument>),
          ...EXCLUDE_AGENT,
        }),
    });

/** Owner only, for the same reason as `forkConversation`. */
export const forkAgentConversation =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const agentKey = req.params.agentKey ?? '';
    if (!agentKey) {
      next(new BadRequestError('agentKey is required'));
      return;
    }
    await runFork(req, res, next, {
      label: 'agent conversation',
      fields: { sessionType: 'agent', agentKey },
      copyArtifacts: forkArtifactCopier(appConfig, req),
      discardArtifacts: (forkId) =>
        deleteConversationArtifacts(appConfig, forkId, req),
      findSource: (orgId, userId, conversationId) =>
        ChatSession.findOne(
          buildAgentConversationFilter(
            req,
            orgId,
            userId,
            agentKey,
            conversationId,
          ) as QueryFilter<IChatSessionDocument>,
        ),
    });
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
    { _id: messageId, sessionId: conversation._id, orgId: conversation.orgId },
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
    conversation.orgId,
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
      _id: conversationId,
      orgId,
      isDeleted: false,
      ...EXCLUDE_AGENT,
      $or: [
        { initiator: userId },
        { isShared: true, 'sharedWith.userId': userId },
      ],
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

    logger.debug('Attempting to archive conversation', {
      requestId,
      message: 'Attempting to archive conversation',
      conversationId: req.params.conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performArchiveConversation(session?: ClientSession | null) {
      // Get conversation with access control
      const conversation = await ChatSession.findOne({
        _id: conversationId,
        userId,
        orgId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
        $or: [
          { initiator: userId },
          { 'sharedWith.userId': userId as string, 'sharedWith.accessLevel': 'write' },
        ],
      });

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no archive permission',
        );
      }

      if (conversation.isArchived) {
        throw new BadRequestError('Conversation already archived');
      }

      // Perform soft delete
      const updatedConversation: IChatSessionDocument | null =
        await ChatSession.findOneAndUpdate(
          { _id: conversationId, ...EXCLUDE_AGENT },
          {
            $set: {
              isArchived: true,
              archivedBy: userId,
              lastActivityAt: Date.now(),
            },
            // An archived chat is out of the list; restoring it should not
            // bring back a pin the owner no longer remembers setting.
            $unset: { isPinned: 1 },
          },
          {
            returnDocument: 'after',
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
      const conversation = await ChatSession.findOne({
        _id: conversationId,
        userId,
        orgId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
        $or: [
          { initiator: userId },
          { 'sharedWith.userId': userId as string, 'sharedWith.accessLevel': 'write' },
        ],
      });

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no unarchive permission',
        );
      }

      if (!conversation.isArchived) {
        throw new BadRequestError('Conversation is not archived');
      }

      // Perform soft delete
      const updatedConversation: IChatSessionDocument | null =
        await ChatSession.findOneAndUpdate(
          { _id: conversationId, ...EXCLUDE_AGENT },
          {
            $set: {
              isArchived: false,
              archivedBy: null,
              lastActivityAt: Date.now(),
            },
          },
          {
            returnDocument: 'after',
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

/**
 * Pin is the owner's own ordering of their chat list, so unlike archive it is
 * not open to people the chat is shared with. Re-pinning a pinned chat (or
 * unpinning an unpinned one) succeeds without change, so a double click or a
 * retry after a dropped response is safe.
 */
const setConversationPinned =
  (
    pinned: boolean,
  ): ((
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>) =>
  async (req, res, next) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    try {
      const { conversationId } = req.params;
      const userId = req.user?.userId as string | undefined;
      const orgId = req.user?.orgId as string | undefined;
      if (!userId || !orgId) {
        throw new BadRequestError('User ID and Organization ID are required');
      }

      const updated = await ChatSession.findOneAndUpdate(
        {
          _id: conversationId,
          userId,
          orgId,
          initiator: userId,
          isDeleted: false,
          // An archived chat is out of the list, so there is nothing to pin.
          // Unpinning stays allowed so a stale pin can always be cleared.
          ...(pinned ? { isArchived: false } : {}),
          ...EXCLUDE_AGENT,
        },
        // Unpinning removes the field rather than storing `false`; see the schema.
        pinned ? { $set: { isPinned: true } } : { $unset: { isPinned: 1 } },
        // Pinning is not an edit of the chat, so it must not change `updatedAt`.
        { returnDocument: 'after', timestamps: false },
      ).exec();

      if (!updated) {
        throw new NotFoundError(
          'Conversation not found or not owned by caller',
        );
      }

      res.status(HTTP_STATUS.OK).json({
        id: updated._id,
        isPinned: pinned,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      });
    } catch (error: unknown) {
      logger.error(`Error ${pinned ? 'pinning' : 'unpinning'} conversation`, {
        requestId,
        message: `Error ${pinned ? 'pinning' : 'unpinning'} conversation`,
        error: error instanceof Error ? error.message : String(error),
      });
      next(error);
    }
  };

export const pinConversation = setConversationPinned(true);
export const unpinConversation = setConversationPinned(false);

export const listAllArchivesConversation =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
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
      let contentMatchIds: mongoose.Types.ObjectId[] | undefined;
      if (req.query.search) {
        const escapedSearch = validateAndEscapeSearch(req.query.search);
        contentMatchIds = await findSessionIdsMatchingContent(
          orgId,
          escapedSearch,
        );
      }
      const filter = {
        ...buildFilter(
          req,
          orgId,
          userId,
          conversationId as string,
          true,
          true,
          contentMatchIds,
        ),
        isArchived: true, // Add archived filter
        archivedBy: { $exists: true }, // Ensure it was properly archived
      };
      const sortOptions = buildSortOptions(req);
      const sessionFilter = { ...filter, ...EXCLUDE_AGENT };

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
      const storedMessages = await ChatSessionMessage.find({
        sessionId: { $in: pageIds },
      })
        .sort({ seq: 1 })
        .lean()
        .exec();
      // Another viewer sees only what they may read of each message.
      const ownerBySession = new Map(
        conversations.map((c) => [String(c._id), String(c.initiator)]),
      );
      const allMessages = await redactMessagesFor(
        appConfig,
        viewerOf(userId, orgId),
        storedMessages,
        (m) => ownerBySession.get(String(m.sessionId)),
      );
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

      // Process results with computed fields and restructured citations
      const processedConversations = conversations.map((conversation: any) => {
        const sessionMessages =
          messagesBySession.get(conversation._id.toString()) || [];
        const conversationWithMessages = attachMessages(
          conversation,
          sessionMessages,
        );
        const conversationWithComputedFields = addComputedFields(
          conversationWithMessages as IConversation,
          userId,
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
        filters: buildFiltersMetadata(filter, req.query),
        summary: {
          totalArchived: totalCount,
          oldestArchive: processedConversations[0]?.archivedAt,
          newestArchive:
            processedConversations[processedConversations.length - 1]
              ?.archivedAt,
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

    // ── Shared base filter pieces ──────────────────────────────────
    const orgOid = new mongoose.Types.ObjectId(`${orgId}`);
    const userOid = new mongoose.Types.ObjectId(`${userId}`);

    // Message content now lives in chatSessionMessages; resolve matching
    // session ids up front instead of an inline `messages.content` regex.
    const contentMatchIds = await findSessionIdsMatchingContent(
      orgId,
      escapedSearch,
    );
    const titleOrContentMatch = {
      $or: [
        { title: { $regex: escapedSearch, $options: 'i' } },
        ...(contentMatchIds.length > 0
          ? [{ _id: { $in: contentMatchIds } }]
          : []),
      ],
    };

    // ── Per-type access predicates (both now against chatSessions) ──
    const assistantAccessPredicate: any = {
      ...EXCLUDE_AGENT,
      $or: [
        { userId: userOid },
        {
          $and: [
            { 'sharedWith.userId': userOid },
            { isShared: true },
          ],
        },
      ],
    };

    const agentAccessPredicate: any = {
      ...ONLY_AGENT,
      userId: userOid,
    };
    if (deletedAgentKeys !== null) {
      agentAccessPredicate.agentKey = { $nin: deletedAgentKeys };
    }

    const baseFilter = {
      orgId: orgOid,
      isDeleted: false,
      isArchived: true,
      archivedBy: { $exists: true },
      $and: [titleOrContentMatch],
    };

    // ── Execute: single $match ORing both per-type predicates, one
    // combined aggregation replacing the old cross-collection $unionWith,
    // and one countDocuments per predicate ──
    const [aggregateResult, assistantCount, agentCount] = await Promise.all([
      ChatSession.aggregate([
        {
          $match: {
            ...baseFilter,
            $or: [assistantAccessPredicate, agentAccessPredicate],
          },
        },
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
      ChatSession.countDocuments({ ...baseFilter, ...assistantAccessPredicate }),
      ChatSession.countDocuments({ ...baseFilter, ...agentAccessPredicate }),
    ]);

    const totalCount = assistantCount + agentCount;

    // ── Tag and apply computed fields (bounded to `limit` docs) ──────
    const paginatedResults = aggregateResult.map((c: any) => ({
      ...addComputedFields(
        (String((c as { userId: unknown }).userId) === String(userId)
          ? c
          : withoutOwnerOnlyFields(c)) as IConversation,
        userId,
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

/** Optional date bounds on `POST /search`, checked and applied by the AI service. */
const SEARCH_DATE_BOUNDS = [
  'createdAfter',
  'createdBefore',
  'modifiedAfter',
  'modifiedBefore',
] as const;

export const search =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const aiBackendUrl = appConfig.aiBackend;
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    try {
      const { query, limit, filters, rewriteQuery, timeline } = req.body;
      // Only the bounds the caller set, so a search without dates sends what it always did.
      const body = req.body as Record<string, unknown>;
      const dateBounds: Record<string, string> = {};
      for (const key of SEARCH_DATE_BOUNDS) {
        const value = body[key];
        if (typeof value === 'string') dateBounds[key] = value;
      }

      // The query is free text, like a chat question, so text in angle
      // brackets such as "<id>" is allowed here as it is in chat. It is not
      // rendered as HTML; see findQuestionField in
      // libs/middlewares/xss-sanitization.middleware.ts.
      if (query && typeof query === 'string') {
        validateNoFormatSpecifiers(query, 'search query');
      }

      logger.debug('Attempting to search', {
        requestId,
        queryLength: typeof query === 'string' ? query.length : 0,
        limit,
        ...buildFilterLogMetadata(filters),
        timestamp: new Date().toISOString(),
      });

      const aiCommand = new AIServiceCommand({
        uri: `${aiBackendUrl}/api/v1/search`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: {
          query,
          limit,
          filters,
          ...dateBounds,
          // Sent only when the caller set it, so other searches send what they always did.
          ...(typeof rewriteQuery === 'boolean' ? { rewriteQuery } : {}),
          ...(typeof timeline === 'string' ? { timeline } : {}),
        },
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

      // An agent searching on a user's behalf sends false: its searches are not
      // the user's history, and saving one stores a citation for every hit.
      if (body.saveHistory === false) {
        res.status(HTTP_STATUS.OK).json({
          searchResponse: buildSearchResponseForClient(
            aiResponse.data as AiSearchResponse & Record<string, unknown>,
          ),
        });
        return;
      }

      const results = aiResponse.data.searchResults;
      let citationIds;
      if (results) {
        // Save the citations to the citations collection in one ordered insert.
        // A search can return hundreds of hits, and saving each one on its own
        // cost a database round trip per hit. `ordered: true` returns the saved
        // documents in hit order, so citationIds lines up with searchResults.
        const savedCitations = await Citation.insertMany(
          results.map((result: ICitation) => ({
            content: result.content,
            chunkIndex: result.chunkIndex ?? 0, // fallback to 0 if not present
            citationType: result.citationType,
            metadata: result.metadata,
          })),
          { ordered: true },
        );
        citationIds = savedCitations.map((citation) => citation._id);
      }

      const recordsArray = aiResponse.data?.records || {};
      const recordsMap = new Map();

      if (Array.isArray(recordsArray)) {
        recordsArray.forEach((record) => {
          // Use either _id or _key as the unique key for each record
          const key = record._id || record._key;
          // `linkedRecords` was filtered for the user who ran this search. A
          // saved search can be shared, and its records are returned as
          // stored, so the links are kept out of what is saved.
          const stored: Record<string, unknown> = {
            ...(record as Record<string, unknown>),
          };
          delete stored.linkedRecords;
          // Convert the record object to a string since your schema expects Map<string, string>
          recordsMap.set(key, JSON.stringify(stored));
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
      logger[logLevelFor(error)]('Error searching query', {
        requestId,
        message: 'Error searching query',
        error: error.message,
      });
      next(error);
    }
  };

/**
 * POST /api/v1/retrieve: ranked chunks with citations, for a developer's own
 * RAG or agent. Unlike `search`, nothing is stored: no citation documents and
 * no search history, since an app may call this on every request it serves.
 */
export const retrieve =
  (
    appConfig: AppConfig,
  ): ((
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>) =>
  async (req, res, next) => {
    const requestId = req.context?.requestId;
    try {
      const body = req.body as Record<string, unknown>;
      if (typeof body.query === 'string') {
        validateNoFormatSpecifiers(body.query, 'retrieve query');
      }
      // Only the fields the caller set, so the AI service's defaults apply.
      const forwarded: Record<string, unknown> = {};
      for (const key of [
        'query',
        'filters',
        'limit',
        'maxChunksPerRecord',
        'rewriteQuery',
        ...SEARCH_DATE_BOUNDS,
      ]) {
        if (body[key] !== undefined) forwarded[key] = body[key];
      }

      // The body may be missing on a broken upstream answer, so it is typed so.
      const aiCommand = new AIServiceCommand<
        Record<string, unknown> | undefined
      >({
        uri: `${appConfig.aiBackend}/api/v1/retrieve`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: forwarded,
      });
      let aiResponse: AIServiceResponse<Record<string, unknown> | undefined>;
      try {
        aiResponse = await aiCommand.execute();
      } catch (error: unknown) {
        throw new InternalServerError(SERVICE_UNAVAILABLE_MESSAGE, {
          cause: error,
        });
      }
      const data = aiResponse.data;
      if (data === undefined) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(
          { response: { status: aiResponse.statusCode, data } },
          'Retrieve',
        );
      }
      res.status(HTTP_STATUS.OK).json({ requestId, ...data });
    } catch (error: unknown) {
      logger[logLevelFor(error)]('Error retrieving chunks', {
        requestId,
        error: error instanceof Error ? error.message : String(error),
      });
      next(error);
    }
  };

export const searchHistory =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
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
        EnterpriseSemanticSearch.countDocuments(
          filter as Record<string, unknown>,
        ),
      ]);

      const response = {
        searchHistory: await redactSearchesFor(
          appConfig,
          viewerOf(userId, orgId),
          searchHistory,
        ),
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

export const getSearchById =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
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

      res
        .status(HTTP_STATUS.OK)
        .json(
          await redactSearchesFor(appConfig, viewerOf(userId, orgId), search),
        );
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
      const existingSharedWith = search.sharedWith || [];

      // Create a map of existing users for quick lookup
      const existingUserMap = new Map(
        existingSharedWith.map((share: any) => [
          share.userId.toString(),
          share,
        ]),
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
      const existingSharedWith = search.sharedWith || [];

      // Remove specified users from sharedWith array
      const updatedSharedWith = existingSharedWith.filter(
        (share) => !userIds.includes(share.userId.toString()),
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

export const createAgent =
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

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/agent/create`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: req.body,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Create Agent');
      }
      const agent = aiResponse.data;
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
        throw handleBackendError(aiResponse, 'Update Agent');
      }
      const agent = aiResponse.data;
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

  export const streamAgentConversation =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest | AuthenticatedServiceRequest, res: Response) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    let userId: Types.ObjectId | undefined;
    let orgId: Types.ObjectId | undefined;
    if('user' in req) {
      const auth_req = req as AuthenticatedUserRequest;
      userId = auth_req.user?.userId;
      orgId = auth_req.user?.orgId;
    } else {
      const auth_req = req as AuthenticatedServiceRequest;
      userId = auth_req.tokenPayload?.userId;
      orgId = auth_req.tokenPayload?.orgId;
    }

    if (!userId) {
      throw new BadRequestError('User ID is required');
    }
    if (!orgId) {
      throw new BadRequestError('Organization ID is required');
    }
    const { agentKey } = req.params;

    let session: ClientSession | null = null;
    let savedConversation: IChatSessionDocument | null = null;

    const modelInfo = extractModelInfo(req.body);
    const protocol = resolveProtocol(
      req.body as Record<string, unknown>,
      req.query as Record<string, unknown>,
    );

    if (!req.body.query) {
      throw new BadRequestError('Query is required');
    }

    try {
      // Set SSE headers
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'X-Accel-Buffering': 'no',
      });

      // Create initial conversation record (before `connected` so the client
      // can link the stream to a real conversationId for URL/sidebar/parallel tabs)
      const userQueryMessage = buildUserQueryMessage(
        req.body.query,
        req.body.appliedFilters,
        req.body.chatMode,
        req.body.attachments,
      );

      const projectLink = await resolveProjectLink(
        orgId as unknown as string,
        userId as unknown as string,
        req.body as Record<string, unknown>,
      );

      const userConversationData: Partial<IChatSession> = {
        orgId,
        userId,
        initiator: userId,
        title: req.body.query.slice(0, 100),
        lastActivityAt: Date.now(),
        status: CONVERSATION_STATUS.INPROGRESS,
        agentKey,
        modelInfo,
        sessionType: 'agent',
        // Set explicitly: the legacy agentConversations schema defaulted this,
        // the unified chatSessions schema can't (it also backs plain chats).
        conversationSource: 'agent_chat',
        ...(projectLink.projectId
          ? {
              projectId: new mongoose.Types.ObjectId(projectLink.projectId),
              projectVisibility: projectLink.projectVisibility,
            }
          : {}),
      };

      // Start transaction if replica set is available
      if (rsAvailable) {
        session = await mongoose.startSession();
        await session.withTransaction(async () => {
          const conversation = new ChatSession(userConversationData);
          savedConversation = (await conversation.save({
            session,
          })) as IChatSessionDocument;
          await appendMessages(
            savedConversation._id as mongoose.Types.ObjectId,
            savedConversation.orgId,
            [userQueryMessage],
            session,
          );
        });
        // The stream outlives this request, so its listeners write without the session.
        await session.endSession();
        session = null;
      } else {
        const conversation = new ChatSession(userConversationData);
        savedConversation =
          (await conversation.save()) as IChatSessionDocument;
        await appendMessages(
          savedConversation._id as mongoose.Types.ObjectId,
          savedConversation.orgId,
          [userQueryMessage],
          session,
        );
      }

      if (!savedConversation) {
        throw new InternalServerError('Failed to create conversation');
      }

      const newAgentConversationId = savedConversation._id?.toString() || '';

      logger.debug('Initial conversation created', {
        requestId,
        conversationId: savedConversation._id,
        userId,
        agentKey,
      });

      // Send initial connection event with conversationId + title and flush.
      // Title mirrors the persisted agent-conversation row; avoids a heavy fetch
      // client-side just for the sidebar pending row. AG-UI mode: this becomes
      // `CUSTOM{name:"conversation_created"}` — it can never become `RUN_STARTED`
      // itself (Python emits the real one once it starts the run); this only
      // exists because Node creates the conversation row BEFORE contacting Python.
      res.write(
        isAGUI(protocol)
          ? frameAGUI(AGUIEventType.CUSTOM, {
              name: 'conversation_created',
              value: {
                conversationId: newAgentConversationId,
                title: savedConversation.title || undefined,
                ...(projectLink.projectId ? { projectId: projectLink.projectId } : {}),
              },
            })
          : `event: connected\ndata: ${JSON.stringify({
              message: 'SSE connection established',
              conversationId: newAgentConversationId,
              title: savedConversation.title || undefined,
              ...(projectLink.projectId ? { projectId: projectLink.projectId } : {}),
            })}\n\n`,
      );
      (res as any).flush?.();

      // A new conversation has no stored history; history is never taken from the client (CE-19).
      const aiRequest = buildAiChatRequest({ kind: 'agent', agentKey: agentKey as string }, req.body, {
        conversationId: newAgentConversationId,
        previousConversations: [],
        isNewConversation: true,
        project: projectLink.project,
      });
      const aiPayload = withStreamProtocol(aiRequest.payload, protocol);
      if (projectLink.projectId) {
        void ProjectService.touchActivity(projectLink.projectId);
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}${aiRequest.path}/stream`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: aiPayload,
      };

      // Variables to collect complete response data
      let completeData: IAIResponse | null = null;
      let buffer = '';
      /** True when AI backend already emitted a terminal `error` SSE we forwarded */
      let upstreamAiErrorEventForwarded = false;
      /** Guards `onAbandon` against also running after the normal
       * `stream.on('end')`/`'error'` path already finalized this run. */
      let streamSettled = false;
      const contentAccumulator = new StreamedContentAccumulator();

      const upstreamAbort = attachUpstreamAbort(res, requestId, () => {
        if (streamSettled || !savedConversation) return;
        if (completeData || upstreamAiErrorEventForwarded) {
          runGuarded(finalizeRun, { requestId });
          return;
        }
        streamSettled = true;
        void savePartialConversation(
          savedConversation,
          contentAccumulator.getText(),
          null,
          { parts: contentAccumulator.getParts() },
        ).catch((err: any) => {
          logger.error('Failed to save partial conversation on disconnect', {
            requestId,
            error: err?.message,
          });
        });
      });
      const stream = await startAIStream(
        aiCommandOptions,
        'Agent Chat Stream',
        { requestId, agentKey },
        upstreamAbort.signal,
      );

      if (!stream) {
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, savedConversation._id);

      // Process SSE events, capture complete event, and forward non-complete events
      stream.on('data', (chunk: Buffer) => {
        const chunkStr = chunk.toString();
        buffer += chunkStr;

        // Look for complete events in the buffer
        const events = buffer.split('\n\n');
        buffer = events.pop() || ''; // Keep incomplete event in buffer

        let filteredChunk = '';
        const agui = isAGUI(protocol);

        for (const event of events) {
          if (event.trim()) {
            // Check if this is a complete event
            const lines = event.split('\n');
            const eventType = lines
              .find((line) => line.startsWith('event:'))
              ?.replace('event:', '')
              .trim();
            const dataLines = lines
              .filter((line) => line.startsWith('data:'))
              .map((line) => line.replace(/^data: ?/, ''));
            const dataLine = dataLines.join('\n');
            if (
              agui &&
              eventType === AGUIEventType.TOOL_CALL_START &&
              dataLine
            ) {
              contentAccumulator.feedToolCallStart(dataLine);
            }

            if (agui && eventType === AGUIEventType.RUN_FINISHED && dataLine) {
              // Root RUN_FINISHED's `result` IS `completion_data` — see
              // `AGUIFormatter.answer_final`. Nested (sub-agent) RUN_FINISHED
              // frames carry no `result` and are just forwarded through below.
              try {
                const parsed = JSON.parse(dataLine);
                if (parsed.result) {
                  completeData = parsed.result;
                  logger.debug('Captured RUN_FINISHED result from AI backend', {
                    requestId,
                    conversationId: savedConversation?._id,
                    answer: completeData?.answer,
                    citationsCount: completeData?.citations?.length || 0,
                    agentKey,
                  });
                  // DO NOT forward the root RUN_FINISHED — Node re-emits its
                  // own enriched RUN_FINISHED after saving, same as `complete`.
                } else {
                  filteredChunk += event + '\n\n';
                }
              } catch (parseError: any) {
                logger.error('Failed to parse RUN_FINISHED event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                filteredChunk += event + '\n\n';
              }
            } else if (agui && eventType === AGUIEventType.TEXT_MESSAGE_CONTENT && dataLine) {
              // Feed the passive-disconnect accumulator so a partial answer
              // survives a dropped connection — see savePartialConversation.
              try {
                contentAccumulator.feedTextMessageContent(JSON.parse(dataLine));
              } catch {
                // Non-fatal: still forward the frame below.
              }
              filteredChunk += event + '\n\n';
            } else if (agui && eventType === AGUIEventType.RUN_ERROR && dataLine) {
              try {
                const errorData = JSON.parse(dataLine) as Record<string, unknown>;
                const errorMessage =
                  (typeof errorData.message === 'string' && errorData.message) ||
                  CHAT_ERROR_MESSAGES.failed;
                upstreamAiErrorEventForwarded = true;
                if (savedConversation) {
                  void markAgentConversationFailed(
                    savedConversation,
                    errorMessage,
                    session,
                    aguiErrorCodeFromPayload(errorData),
                    typeof errorData.stack === 'string' ? errorData.stack : undefined,
                  ).catch((markErr: any) => {
                    logger.error('Failed to mark agent conversation from AI RUN_ERROR SSE', {
                      requestId,
                      error: markErr?.message,
                    });
                  });
                }
                filteredChunk += event + '\n\n';
              } catch (parseError: any) {
                logger.error('Failed to parse RUN_ERROR event data from AI stream', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                filteredChunk += event + '\n\n';
              }
            } else if (agui && eventType === AGUIEventType.CUSTOM && dataLine && savedConversation) {
              try {
                const eventData = JSON.parse(dataLine);
                if (eventData?.name === 'ask_user_question' && eventData.value) {
                  const toolCallMessage = {
                    messageType: 'tool_call' as const,
                    content: '',
                    tools: [{
                      toolName: 'ask_user_question',
                      toolResult: eventData.value.toolData ?? eventData.value,
                    }],
                    createdAt: new Date(),
                    updatedAt: new Date(),
                  };
                  void appendMessages(
                    savedConversation._id as mongoose.Types.ObjectId,
                    savedConversation.orgId,
                    [toolCallMessage as unknown as IMessage],
                    session,
                  ).catch((saveErr: any) => {
                    logger.error('Failed to persist ask_user_question tool_call message', {
                      requestId,
                      conversationId: savedConversation?._id,
                      error: saveErr?.message,
                    });
                  });
                }
              } catch (parseErr: any) {
                logger.warn('Failed to parse CUSTOM event data', {
                  requestId,
                  error: parseErr?.message,
                });
              }
              filteredChunk += event + '\n\n';
            } else if (!agui && eventType === 'complete' && dataLine) {
              try {
                completeData = JSON.parse(dataLine);
                logger.debug('Captured complete event data from AI backend', {
                  requestId,
                  conversationId: savedConversation?._id,
                  answer: completeData?.answer,
                  citationsCount: completeData?.citations?.length || 0,
                  agentKey,
                });
                // DO NOT forward the complete event from AI backend
                // We'll send our own complete event after processing
              } catch (parseError: any) {
                logger.error('Failed to parse complete event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                // Forward the event if we can't parse it
                filteredChunk += event + '\n\n';
              }
            } else if (!agui && eventType === 'answer_chunk' && dataLine) {
              // `accumulated` is the running full text, not a delta — see
              // LegacyFormatter.answer_delta.
              try {
                const parsed = JSON.parse(dataLine) as Record<string, unknown>;
                if (typeof parsed.accumulated === 'string') {
                  contentAccumulator.setAccumulatedText(parsed.accumulated);
                }
              } catch {
                // Non-fatal: still forward the frame below.
              }
              filteredChunk += event + '\n\n';
            } else if (!agui && eventType === 'error' && dataLine) {
              try {
                const errorData = JSON.parse(dataLine) as Record<string, unknown>;
                const errorMessage =
                  (typeof errorData.message === 'string' && errorData.message) ||
                  (typeof errorData.error === 'string' && errorData.error) ||
                  CHAT_ERROR_MESSAGES.failed;
                upstreamAiErrorEventForwarded = true;
                if (savedConversation) {
                  const meta =
                    errorData.metadata && typeof errorData.metadata === 'object'
                      ? new Map(Object.entries(errorData.metadata as Record<string, unknown>))
                      : undefined;
                  void markAgentConversationFailed(
                    savedConversation,
                    errorMessage,
                    session,
                    'streaming_error',
                    typeof errorData.stack === 'string' ? errorData.stack : undefined,
                    meta,
                  ).catch((markErr: any) => {
                    logger.error('Failed to mark agent conversation from AI error SSE', {
                      requestId,
                      error: markErr?.message,
                    });
                  });
                }
                filteredChunk += event + '\n\n';
              } catch (parseError: any) {
                logger.error('Failed to parse error event data from AI stream', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                upstreamAiErrorEventForwarded = true;
                if (savedConversation) {
                  const parseFailMsg = CHAT_ERROR_MESSAGES.failed;
                  void markAgentConversationFailed(
                    savedConversation,
                    parseFailMsg,
                    session,
                    'parse_error',
                    parseError.stack,
                  ).catch((markErr: any) => {
                    logger.error('Failed to mark agent conversation after SSE parse error', {
                      requestId,
                      error: markErr?.message,
                    });
                  });
                }
                filteredChunk += event + '\n\n';
              }
            } else if (!agui && eventType === 'ask_user_question' && dataLine && savedConversation) {
              try {
                const eventData = JSON.parse(dataLine);
                if (eventData.status === 'success') {
                  const toolCallMessage = {
                    messageType: 'tool_call' as const,
                    content: '',
                    tools: [{
                      toolName: 'ask_user_question',
                      toolResult: eventData.toolData ?? eventData,
                    }],
                    createdAt: new Date(),
                    updatedAt: new Date(),
                  };
                  void appendMessages(
                    savedConversation._id as mongoose.Types.ObjectId,
                    savedConversation.orgId,
                    [toolCallMessage as unknown as IMessage],
                    session,
                  ).catch((saveErr: any) => {
                    logger.error('Failed to persist ask_user_question tool_call message', {
                      requestId,
                      conversationId: savedConversation?._id,
                      error: saveErr?.message,
                    });
                  });
                }
              } catch (parseErr: any) {
                logger.warn('Failed to parse ask_user_question event data', {
                  requestId,
                  error: parseErr?.message,
                });
              }
              filteredChunk += event + '\n\n';
            } else {
              // Forward all other non-complete events
              filteredChunk += event + '\n\n';
            }
          }
        }

        // Forward only non-complete events to client
        if (filteredChunk) {
          res.write(filteredChunk);
          (res as any).flush?.();
        }
      });

      const finalizeRun = async (): Promise<void> => {
        streamSettled = true;
        logger.debug('Stream ended successfully', { requestId });
        try {
          // Save the complete conversation data to database
          if (completeData && savedConversation) {
            const conversation = await saveCompleteAgentConversation(
              savedConversation,
              completeData,
              orgId?.toString(),
              session,
              modelInfo,
            );

            // Send the final conversation data in the same format as createConversation
            const responsePayload = {
              conversation: conversation,
              meta: {
                requestId,
                timestamp: new Date().toISOString(),
                duration: Date.now() - startTime,
              },
            };

            // Send final response event with the complete conversation data
            res.write(
              isAGUI(protocol)
                ? frameAGUI(AGUIEventType.RUN_FINISHED, { result: responsePayload })
                : `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
            );

            logger.debug(
              'Conversation completed and saved, sent custom complete event',
              {
                requestId,
                conversationId: savedConversation._id,
                duration: Date.now() - startTime,
                agentKey,
              },
            );
          } else if (!upstreamAiErrorEventForwarded) {
            // Mark as failed if no complete data received (and AI did not already send error SSE)
            if (savedConversation) {
              await markAgentConversationFailed(
                savedConversation,
                CHAT_ERROR_MESSAGES.interrupted,
                session,
                'no_response',
              );
            }

            // Send error event
            res.write(
              isAGUI(protocol)
                ? frameAGUI(AGUIEventType.RUN_ERROR, {
                    message: CHAT_ERROR_MESSAGES.interrupted,
                    code: 'no_response',
                  })
                : `event: error\ndata: ${JSON.stringify({
                    error: CHAT_ERROR_MESSAGES.interrupted,
                  })}\n\n`,
            );
          }
          } catch (dbError: any) {
          logger.error('Failed to save complete conversation', {
            requestId,
            conversationId: savedConversation?._id,
            agentKey,
            error: dbError.message,
          });

          if (savedConversation) {
            // Awaited but contained: a second failed write must not keep the stream open.
            await markAgentConversationFailed(
              savedConversation,
              CHAT_ERROR_MESSAGES.saveFailed,
              session,
              'save_error',
              dbError.stack,
            ).catch((markErr: unknown) => {
              logger.error(
                'Failed to mark agent conversation as failed after save error',
                {
                  requestId,
                  error: markErr instanceof Error ? markErr.message : markErr,
                },
              );
            });
          }

          // Send error event
          res.write(
            isAGUI(protocol)
              ? frameAGUI(AGUIEventType.RUN_ERROR, {
                  message: CHAT_ERROR_MESSAGES.saveFailed,
                  code: 'save_error',
                })
              : `event: error\ndata: ${JSON.stringify({
                  error: CHAT_ERROR_MESSAGES.saveFailed,
                })}\n\n`,
          );
        }

        res.end();
      };
      stream.on('end', () => {
        runGuarded(finalizeRun, { requestId });
      });

      stream.on('error', async (error: Error) => {
        if (upstreamAbort.isAborted()) {
          logger.debug('Stream abandoned after the detached-run cap', {
            requestId,
          });
          return;
        }
        streamSettled = true;
        logger.error('Stream error', { requestId, error: error.message });
        try {
          // Mark conversation as failed
          if (savedConversation) {
            await markAgentConversationFailed(
              savedConversation,
              userFacingChatError(error),
              session,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to mark conversation as failed', {
            requestId,
            conversationId: savedConversation?._id,
            agentKey,
            error: dbError.message,
          });
        }

        const errorEvent = isAGUI(protocol)
          ? frameAGUI(AGUIEventType.RUN_ERROR, {
              message: userFacingChatError(error),
              code: 'stream_error',
            })
          : `event: error\ndata: ${JSON.stringify({
              error: userFacingChatError(error),
            })}\n\n`;
        res.write(errorEvent);
        res.end();
      });
    } catch (error: any) {
      logger[logLevelFor(error)]('Error in streamChat', {
        requestId,
        error: error.message,
      });

      try {
        // Mark conversation as failed if it was created
        if (savedConversation) {
          await markAgentConversationFailed(
            savedConversation,
            userFacingChatError(error),
            session,
          );
        }
      } catch (dbError: any) {
        logger.error('Failed to mark conversation as failed in catch block', {
          requestId,
          error: dbError.message,
        });
      }

      if (!res.headersSent) {
        res.writeHead(500, {
          'Content-Type': 'text/event-stream; charset=utf-8',
        });
      }

      const errorEvent = isAGUI(protocol)
        ? frameAGUI(AGUIEventType.RUN_ERROR, {
            message: userFacingChatError(error),
            code: 'internal_error',
          })
        : `event: error\ndata: ${JSON.stringify({
            error: userFacingChatError(error),
          })}\n\n`;
      res.write(errorEvent);
      res.end();
    } finally {
      if (session) {
        session.endSession();
        session = null;
      }
    }
  };

export const streamAgentConversationInternal =
  (appConfig: AppConfig, keyValueStoreService?: KeyValueStoreService) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      await hydrateScopedRequestAsUser(req, appConfig, keyValueStoreService);
      await streamAgentConversation(appConfig)(
        req as AuthenticatedUserRequest | AuthenticatedServiceRequest,
        res,
      );
    } catch (error) {
      next(error);
    }
  };

export const addMessageStreamToAgentConversation =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next?: NextFunction,
  ): Promise<void> => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    let userId: Types.ObjectId | undefined;
    let orgId: Types.ObjectId | undefined;
    if ('user' in req) {
      const auth_req = req as AuthenticatedUserRequest;
      userId = auth_req.user?.userId;
      orgId = auth_req.user?.orgId;
    } else {
      const auth_req = req as AuthenticatedServiceRequest;
      userId = auth_req.tokenPayload?.userId;
      orgId = auth_req.tokenPayload?.orgId;
    }

    if (!userId) {
      return refuseBeforeStream(next, 'User ID is required');
    }
    if (!orgId) {
      return refuseBeforeStream(next, 'Organization ID is required');
    }
    const { conversationId, agentKey } = req.params;

    let session: ClientSession | null = null;
    let existingConversation: IChatSessionDocument | null = null;
    let reservation: RunReservation | null = null;

    const modelInfo = extractModelInfo(req.body);
    const protocol = resolveProtocol(
      req.body as Record<string, unknown>,
      req.query as Record<string, unknown>,
    );

    if (!req.body.query) {
      return refuseBeforeStream(next, 'Query is required');
    }

    // Helper function that contains the common conversation operations
    async function performAddMessageStream(
      session?: ClientSession | null,
    ): Promise<void> {
      // Get existing conversation
      const conversation = await ChatSession.findOne({
        _id: conversationId,
        agentKey,
        orgId,
        userId,
        isDeleted: false,
        ...ONLY_AGENT,
      });

      if (!conversation) {
        throw new NotFoundError('Conversation not found');
      }

      // Update status to processing when adding a new message
      conversation.status = CONVERSATION_STATUS.INPROGRESS as any;
      conversation.failReason = undefined; // Clear previous error if any

      const fieldsToUpdate: Array<keyof IAIModel> = [
        'modelKey',
        'modelName',
        'modelProvider',
        'chatMode',
        'modelFriendlyName',
        'reasoningEffort',
      ];
      for (const field of fieldsToUpdate) {
        const value = req.body[field];
        if (value !== undefined && value !== null) {
          assignAiModelField(conversation.modelInfo as IAIModel, field, value);
        }
      }

      // First, add the user message to the existing conversation
      await appendMessages(
        conversation._id as mongoose.Types.ObjectId,
        conversation.orgId,
        [
          buildUserQueryMessage(
            req.body.query,
            req.body.appliedFilters,
            req.body.chatMode,
            req.body.attachments,
          ),
        ],
        session,
      );
      conversation.lastActivityAt = Date.now();

      // Save the user message to the existing conversation first
      const savedConversation = session
        ? await conversation.save({ session })
        : ((await conversation.save()) as IChatSessionDocument);

      if (!savedConversation) {
        throw new InternalServerError(
          'Failed to update conversation with user message',
        );
      }

      existingConversation = savedConversation;

      logger.debug('User message added to conversation', {
        requestId,
        conversationId: existingConversation._id,
        agentKey,
        userId,
      });
    }

    try {
      // Set SSE headers
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'X-Accel-Buffering': 'no',
      });

      // Send initial connection event and flush. AG-UI mode: this becomes
      // `CUSTOM{name:"conversation_created"}` — see `streamAgentConversation`.
      res.write(
        isAGUI(protocol)
          ? frameAGUI(AGUIEventType.CUSTOM, {
              name: 'conversation_created',
              value: { conversationId },
            })
          : `event: connected\ndata: ${JSON.stringify({ message: 'SSE connection established' })}\n\n`,
      );
      (res as any).flush?.();

      logger.debug('Adding message to conversation via stream', {
        requestId,
        conversationId,
        userId,
        agentKey,
        ...buildAiRequestLogMetadata(req.body as Record<string, unknown>),
        timestamp: new Date().toISOString(),
      });

      reservation = await reserveRun({
        _id: conversationId,
        agentKey,
        orgId,
        userId,
        isDeleted: false,
        ...ONLY_AGENT,
      });

      // Get existing conversation and add user message
      if (rsAvailable) {
        session = await mongoose.startSession();
        await session.withTransaction(() => performAddMessageStream(session));
        // The stream outlives this request, so its listeners write without the session.
        await session.endSession();
        session = null;
      } else {
        await performAddMessageStream();
      }

      if (!existingConversation) {
        throw new NotFoundError('Conversation not found');
      }
      const confirmedConversation =
        existingConversation as IChatSessionDocument;

      // Format previous conversations for context (excluding the user message we just added)
      const allMessagesSoFar = (await getMessages(
        confirmedConversation._id,
        { orgId: confirmedConversation.orgId },
        session,
      )) as IMessage[];
      const previousConversations = formatPreviousConversations(
        allMessagesSoFar.slice(0, -1),
      );

      const followUpProject = await loadProjectForSession(
        confirmedConversation.orgId.toString(),
        confirmedConversation.userId.toString(),
        confirmedConversation.projectId,
      );
      const aiRequest = buildAiChatRequest(
        { kind: 'agent', agentKey: agentKey as string },
        req.body,
        {
          conversationId,
          previousConversations,
          isNewConversation: false,
          project: followUpProject,
        },
      );
      const aiPayload = withStreamProtocol(aiRequest.payload, protocol);
      if (confirmedConversation.projectId) {
        void ProjectService.touchActivity(
          confirmedConversation.projectId.toString(),
        );
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}${aiRequest.path}/stream`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: aiPayload,
      };

      // Variables to collect complete response data
      let completeData: IAIResponse | null = null;
      let buffer = '';
      /** True when AI backend already emitted a terminal `error` SSE we forwarded */
      let upstreamAiErrorEventForwarded = false;
      /** Guards `onAbandon` against also running after the normal
       * `stream.on('end')`/`'error'` path already finalized this run. */
      let streamSettled = false;
      const contentAccumulator = new StreamedContentAccumulator();

      const upstreamAbort = attachUpstreamAbort(res, requestId, () => {
        if (streamSettled || !existingConversation) return;
        if (completeData || upstreamAiErrorEventForwarded) {
          runGuarded(finalizeRun, { requestId });
          return;
        }
        streamSettled = true;
        void savePartialConversation(
          existingConversation,
          contentAccumulator.getText(),
          null,
          { parts: contentAccumulator.getParts() },
        ).catch((err: any) => {
          logger.error('Failed to save partial conversation on disconnect', {
            requestId,
            error: err?.message,
          });
        });
      });
      const stream = await startAIStream(
        aiCommandOptions,
        'Add Message Agent Stream',
        { requestId, agentKey },
        upstreamAbort.signal,
      );

      if (!stream) {
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, confirmedConversation._id, reservation?.runId);

      // Process SSE events, capture complete event, and forward non-complete events
      stream.on('data', (chunk: Buffer) => {
        const chunkStr = chunk.toString();
        buffer += chunkStr;

        // Look for complete events in the buffer
        const events = buffer.split('\n\n');
        buffer = events.pop() || ''; // Keep incomplete event in buffer

        let filteredChunk = '';
        const agui = isAGUI(protocol);

        for (const event of events) {
          if (event.trim()) {
            // Check if this is a complete event
            const lines = event.split('\n');
            const eventType = lines
              .find((line) => line.startsWith('event:'))
              ?.replace('event:', '')
              .trim();
            const dataLines = lines
              .filter((line) => line.startsWith('data:'))
              .map((line) => line.replace(/^data: ?/, ''));
            const dataLine = dataLines.join('\n');
            if (
              agui &&
              eventType === AGUIEventType.TOOL_CALL_START &&
              dataLine
            ) {
              contentAccumulator.feedToolCallStart(dataLine);
            }
            if (agui && eventType === AGUIEventType.RUN_FINISHED && dataLine) {
              // Root RUN_FINISHED's `result` IS `completion_data` — see
              // `AGUIFormatter.answer_final`. Nested (sub-agent) RUN_FINISHED
              // frames carry no `result` and are just forwarded through below.
              try {
                const parsed = JSON.parse(dataLine);
                if (parsed.result) {
                  completeData = parsed.result;
                  logger.debug('Captured RUN_FINISHED result from AI backend', {
                    requestId,
                    conversationId: existingConversation?._id,
                    answer: completeData?.answer,
                    citationsCount: completeData?.citations?.length || 0,
                  });
                  // DO NOT forward the root RUN_FINISHED — Node re-emits its
                  // own enriched RUN_FINISHED after saving, same as `complete`.
                } else {
                  filteredChunk += event + '\n\n';
                }
              } catch (parseError: any) {
                logger.error('Failed to parse RUN_FINISHED event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                filteredChunk += event + '\n\n';
              }
            } else if (
              agui &&
              eventType === AGUIEventType.TEXT_MESSAGE_CONTENT &&
              dataLine
            ) {
              // Feed the passive-disconnect accumulator so a partial answer
              // survives a dropped connection — see savePartialConversation.
              try {
                contentAccumulator.feedTextMessageContent(JSON.parse(dataLine));
              } catch {
                // Non-fatal: still forward the frame below.
              }
              filteredChunk += event + '\n\n';
            } else if (
              agui &&
              eventType === AGUIEventType.RUN_ERROR &&
              dataLine
            ) {
              try {
                const errorData = JSON.parse(dataLine);
                const errorMessage =
                  errorData.message || CHAT_ERROR_MESSAGES.failed;
                upstreamAiErrorEventForwarded = true;
                if (existingConversation) {
                  void markAgentConversationFailed(
                    existingConversation,
                    errorMessage,
                    session,
                    aguiErrorCodeFromPayload(
                      errorData as Record<string, unknown>,
                    ),
                    errorData.stack,
                  ).catch((markErr: any) => {
                    logger.error(
                      'Failed to mark agent conversation from AI RUN_ERROR SSE',
                      {
                        requestId,
                        error: markErr?.message,
                      },
                    );
                  });
                }
                filteredChunk += event + '\n\n';
              } catch (parseError: any) {
                logger.error('Failed to parse RUN_ERROR event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                filteredChunk += event + '\n\n';
              }
            } else if (
              agui &&
              eventType === AGUIEventType.CUSTOM &&
              dataLine &&
              existingConversation
            ) {
              try {
                const eventData = JSON.parse(dataLine);
                if (
                  eventData?.name === 'ask_user_question' &&
                  eventData.value
                ) {
                  const toolCallMessage = {
                    messageType: 'tool_call' as const,
                    content: '',
                    tools: [
                      {
                        toolName: 'ask_user_question',
                        toolResult: eventData.value.toolData ?? eventData.value,
                      },
                    ],
                    createdAt: new Date(),
                    updatedAt: new Date(),
                  };
                  void appendMessages(
                    existingConversation._id,
                    existingConversation.orgId,
                    [toolCallMessage as unknown as IMessage],
                    session,
                  ).catch((saveErr: any) => {
                    logger.error(
                      'Failed to persist ask_user_question tool_call message',
                      {
                        requestId,
                        conversationId: existingConversation?._id,
                        error: saveErr?.message,
                      },
                    );
                  });
                }
              } catch (parseErr: any) {
                logger.warn('Failed to parse CUSTOM event data', {
                  requestId,
                  error: parseErr?.message,
                });
              }
              filteredChunk += event + '\n\n';
            } else if (!agui && eventType === 'complete' && dataLine) {
              try {
                completeData = JSON.parse(dataLine);
                logger.debug('Captured complete event data from AI backend', {
                  requestId,
                  conversationId: existingConversation?._id,
                  answer: completeData?.answer,
                  citationsCount: completeData?.citations?.length || 0,
                });
                // DO NOT forward the complete event from AI backend
                // We'll send our own complete event after processing
              } catch (parseError: any) {
                logger.error('Failed to parse complete event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                // Forward the event if we can't parse it
                filteredChunk += event + '\n\n';
              }
            } else if (!agui && eventType === 'answer_chunk' && dataLine) {
              // `accumulated` is the running full text, not a delta — see
              // LegacyFormatter.answer_delta.
              try {
                const parsed = JSON.parse(dataLine) as Record<string, unknown>;
                if (typeof parsed.accumulated === 'string') {
                  contentAccumulator.setAccumulatedText(parsed.accumulated);
                }
              } catch {
                // Non-fatal: still forward the frame below.
              }
              filteredChunk += event + '\n\n';
            } else if (!agui && eventType === 'error' && dataLine) {
              try {
                const errorData = JSON.parse(dataLine);
                const errorMessage =
                  errorData.message ||
                  errorData.error ||
                  CHAT_ERROR_MESSAGES.failed;
                upstreamAiErrorEventForwarded = true;
                if (existingConversation) {
                  void markAgentConversationFailed(
                    existingConversation,
                    errorMessage,
                    session,
                    'streaming_error',
                    errorData.stack,
                    errorData.metadata
                      ? new Map(Object.entries(errorData.metadata))
                      : undefined,
                  ).catch((markErr: any) => {
                    logger.error(
                      'Failed to mark agent conversation from AI error SSE',
                      {
                        requestId,
                        error: markErr?.message,
                      },
                    );
                  });
                }
                filteredChunk += event + '\n\n';
              } catch (parseError: any) {
                logger.error('Failed to parse error event data', {
                  requestId,
                  parseError: parseError.message,
                  dataLine,
                });
                upstreamAiErrorEventForwarded = true;
                if (existingConversation) {
                  void markAgentConversationFailed(
                    existingConversation,
                    CHAT_ERROR_MESSAGES.failed,
                    session,
                    'parse_error',
                    parseError.stack,
                  ).catch((markErr: any) => {
                    logger.error(
                      'Failed to mark agent conversation after error-event parse failure',
                      {
                        requestId,
                        error: markErr?.message,
                      },
                    );
                  });
                }
                filteredChunk += event + '\n\n';
              }
            } else if (
              !agui &&
              eventType === 'ask_user_question' &&
              dataLine &&
              existingConversation
            ) {
              try {
                const eventData = JSON.parse(dataLine);
                if (eventData.status === 'success') {
                  const toolCallMessage = {
                    messageType: 'tool_call' as const,
                    content: '',
                    tools: [
                      {
                        toolName: 'ask_user_question',
                        toolResult: eventData.toolData ?? eventData,
                      },
                    ],
                    createdAt: new Date(),
                    updatedAt: new Date(),
                  };
                  void appendMessages(
                    existingConversation._id,
                    existingConversation.orgId,
                    [toolCallMessage as unknown as IMessage],
                    session,
                  ).catch((saveErr: any) => {
                    logger.error(
                      'Failed to persist ask_user_question tool_call message',
                      {
                        requestId,
                        conversationId: existingConversation?._id,
                        error: saveErr?.message,
                      },
                    );
                  });
                }
              } catch (parseErr: any) {
                logger.warn('Failed to parse ask_user_question event data', {
                  requestId,
                  error: parseErr?.message,
                });
              }
              filteredChunk += event + '\n\n';
            } else {
              // Forward all non-complete events
              filteredChunk += event + '\n\n';
            }
          }
        }

        // Forward only non-complete events to client
        if (filteredChunk) {
          res.write(filteredChunk);
          (res as any).flush?.();
        }
      });

      const finalizeRun = async (): Promise<void> => {
        logger.debug('Stream ended successfully', { requestId });
        streamSettled = true;
        try {
          // Save the AI response to the existing conversation
          if (completeData && existingConversation) {
            try {
              // Create and save citations
              const savedCitations: ICitation[] = await Promise.all(
                completeData.citations?.map(async (citation: ICitation) => {
                  const newCitation = new Citation({
                    content: citation.content,
                    chunkIndex: citation.chunkIndex,
                    citationType: citation.citationType,
                    metadata: {
                      ...citation.metadata,
                      orgId,
                    },
                  });
                  return newCitation.save();
                }) || [],
              );

              // Build AI response message using existing utility
              const aiResponseMessage = buildAIResponseMessage(
                { statusCode: 200, data: completeData },
                savedCitations,
                modelInfo,
              ) as IMessageDocument;

              // Add the AI message to the existing conversation
              const insertedAiMessage = (
                await appendMessages(
                  existingConversation._id,
                  existingConversation.orgId,
                  [aiResponseMessage],
                  session,
                )
              )[0];
              existingConversation.lastActivityAt = Date.now();
              recordClassifiedFailureOnSession(
                existingConversation,
                completeData,
              );

              // Save the updated conversation with AI response
              const updatedConversation = session
                ? await existingConversation.save({ session })
                : ((await existingConversation.save()) as IChatSessionDocument);

              if (!updatedConversation) {
                throw new InternalServerError(
                  'Failed to update conversation with AI response',
                );
              }

              // Return the updated conversation in the same format as addMessage
              const responseConversation = await attachPopulatedCitations(
                updatedConversation.toObject(),
                [insertedAiMessage!.toObject()],
                savedCitations,
                session,
              );

              // Send the final conversation data in the same format as addMessage
              const responsePayload = {
                conversation: responseConversation,
                recordsUsed: savedCitations.length,
                meta: {
                  requestId,
                  timestamp: new Date().toISOString(),
                  duration: Date.now() - startTime,
                  recordsUsed: savedCitations.length,
                },
              };

              // Send final response event with the complete conversation data
              res.write(
                isAGUI(protocol)
                  ? frameAGUI(AGUIEventType.RUN_FINISHED, {
                      result: responsePayload,
                    })
                  : `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
              );

              logger.debug(
                'Message added and conversation updated, sent custom complete event',
                {
                  requestId,
                  conversationId: existingConversation._id,
                  duration: Date.now() - startTime,
                },
              );
            } catch (error: any) {
              if (existingConversation) {
                await markAgentConversationFailed(
                  existingConversation,
                  userFacingChatError(error),
                  session,
                  'internal_error',
                  error.stack,
                );
              }

              if (error.cause && error.cause.code === 'ECONNREFUSED') {
                throw new InternalServerError(
                  SERVICE_UNAVAILABLE_MESSAGE,
                  error,
                );
              }
              throw error;
            }
          } else if (!upstreamAiErrorEventForwarded) {
            // Mark as failed if no complete data received (and AI did not already send error SSE)
            if (existingConversation) {
              await markAgentConversationFailed(
                existingConversation,
                CHAT_ERROR_MESSAGES.interrupted,
                session,
                'no_response',
              );
            }

            // Send error event
            res.write(
              isAGUI(protocol)
                ? frameAGUI(AGUIEventType.RUN_ERROR, {
                    message: CHAT_ERROR_MESSAGES.interrupted,
                    code: 'no_response',
                  })
                : `event: error\ndata: ${JSON.stringify({
                    error: CHAT_ERROR_MESSAGES.interrupted,
                  })}\n\n`,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to save AI response to conversation', {
            requestId,
            conversationId: existingConversation?._id,
            error: dbError.message,
          });

          // Send error event
          res.write(
            isAGUI(protocol)
              ? frameAGUI(AGUIEventType.RUN_ERROR, {
                  message: CHAT_ERROR_MESSAGES.saveFailed,
                  code: 'save_error',
                })
              : `event: error\ndata: ${JSON.stringify({
                  error: CHAT_ERROR_MESSAGES.saveFailed,
                })}\n\n`,
          );
        }

        res.end();
      };
      stream.on('end', () => {
        runGuarded(finalizeRun, { requestId });
      });

      stream.on('error', async (error: Error) => {
        if (upstreamAbort.isAborted()) {
          logger.debug('Stream abandoned after the detached-run cap', {
            requestId,
          });
          return;
        }
        streamSettled = true;
        logger.error('Stream error', { requestId, error: error.message });
        try {
          if (existingConversation) {
            // Awaited so the catch below actually observes a rejection.
            await markAgentConversationFailed(
              existingConversation,
              userFacingChatError(error),
              session,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to mark conversation as failed', {
            requestId,
            conversationId: existingConversation?._id,
            agentKey,
            error: dbError.message,
          });
        }

        const errorEvent = isAGUI(protocol)
          ? frameAGUI(AGUIEventType.RUN_ERROR, {
              message: userFacingChatError(error),
              code: 'stream_error',
            })
          : `event: error\ndata: ${JSON.stringify({
              error: userFacingChatError(error),
            })}\n\n`;
        res.write(errorEvent);
        res.end();
      });
    } catch (error: any) {
      logger[logLevelFor(error)]('Error in addMessageStream', {
        requestId,
        conversationId,
        agentKey,
        error: error.message,
      });

      try {
        if (existingConversation) {
          await markAgentConversationFailed(
            existingConversation as IChatSessionDocument,
            userFacingChatError(error),
            session,
            'internal_error',
            error.stack,
          );
        } else {
          await releaseRun(reservation);
        }
      } catch (dbError: any) {
        logger.error('Failed to mark conversation as failed in catch block', {
          requestId,
          conversationId,
          agentKey,
          error: dbError.message,
        });
      }

      if (!res.headersSent) {
        res.writeHead(500, {
          'Content-Type': 'text/event-stream; charset=utf-8',
        });
      }

      const errorEvent = isAGUI(protocol)
        ? frameAGUI(AGUIEventType.RUN_ERROR, {
            message: userFacingChatError(error),
            code: 'internal_error',
          })
        : `event: error\ndata: ${JSON.stringify({
            error: userFacingChatError(error),
          })}\n\n`;
      res.write(errorEvent);
      res.end();
    } finally {
      if (session) {
        session.endSession();
        session = null;
      }
    }
  };

export const addMessageStreamToAgentConversationInternal =
  (appConfig: AppConfig, keyValueStoreService?: KeyValueStoreService) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      await hydrateScopedRequestAsUser(req, appConfig, keyValueStoreService);
      await addMessageStreamToAgentConversation(appConfig)(
        req as AuthenticatedUserRequest | AuthenticatedServiceRequest,
        res,
      );
    } catch (error) {
      next(error);
    }
  };

export const regenerateAgentAnswers =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response) => {
    await regenerateAnswersInternal(appConfig, req, res, {
      isAgentSession: true,
      buildQueryFilter: (conversationId, orgId, userId, agentKey) => ({
        _id: conversationId,
        agentKey,
        orgId,
        userId,
        // Owner only: a chat shared with someone is read-only for them.
        initiator: userId,
        isDeleted: false,
        ...ONLY_AGENT,
      }),
      buildAIEndpoint: (appConfig, agentKey) =>
        `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey as string)}/chat/stream`,
    });
  };

/**
 * Agent-conversation counterpart of `cancelConversationStream` — same
 * `buildAgentConversationFilter` ownership check used by
 * `getAgentConversationById`/`deleteAgentConversationById`, then forwards to
 * the SAME Python `/chat/cancel` endpoint (registry is keyed by `runId`
 * alone, not by which route created it).
 */
export const cancelAgentConversationStream =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const { conversationId, agentKey } = req.params;
    const { runId } = req.body;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    try {
      const filter = buildAgentConversationFilter(
        req,
        orgId as string,
        userId as string,
        agentKey as string,
        conversationId as string,
      );
      const conversation = await ChatSession.findOne(filter);
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/chat/cancel`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        // See cancelConversationStream: conversationId is forwarded so
        // Python can scope the RunOwner check to it, not just user/org.
        body: { runId, conversationId },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
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
      queryParams: Object.keys(req.query),
    });

    const { skip, limit, page } = getPaginationParams(req);
    let contentMatchIds: mongoose.Types.ObjectId[] | undefined;
    if (req.query.search) {
      const escapedSearch = validateAndEscapeSearch(req.query.search, {
        formatSpecifiers: true,
      });
      contentMatchIds = await findSessionIdsMatchingContent(
        orgId,
        escapedSearch,
      );
    }
    const accessibleProjectIds = await ProjectService.getAccessibleProjectIds(
      orgId,
      userId,
    );
    const filter = {
      ...buildAgentConversationFilter(
        req,
        orgId,
        userId,
        agentKey as string,
        conversationId as string,
        contentMatchIds,
        accessibleProjectIds,
      ),
      // Sidebar / chat list: omit archived threads (archived view uses a dedicated route).
      isArchived: { $ne: true },
    };
    const sortOptions = buildSortOptions(req);

    // sharedWith Me Conversation
    const sharedWithMeFilter = {
      ...buildAgentSharedWithMeFilter(req, orgId, userId, agentKey as string),
      isArchived: { $ne: true },
    };

    // Execute query
    const [conversations, totalCount, sharedWithMeConversations] =
      await Promise.all([
        ChatSession.find(filter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select('-__v')
          .lean()
          .exec(),
        ChatSession.countDocuments(filter),
        ChatSession.find(sharedWithMeFilter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select('-__v')
          .select('-sharedWith')
          .lean()
          .exec(),
      ]);

    const processedConversations = conversations.map((conversation: any) => {
      const conversationWithComputedFields = addComputedFields(
        conversation as IAgentConversation,
        userId,
      );
      return {
        ...conversationWithComputedFields,
      };
    });
    // A project member sees another user's project-visible chat here; who it was shared with is
    // the owner's business.
    const visibleConversations = processedConversations.map((conversation) =>
      conversation.isOwner
        ? conversation
        : { ...conversation, sharedWith: undefined },
    );
    const processedSharedWithMeConversations = await attachSharedBy(
      sharedWithMeConversations.map((sharedWithMeConversation: any) =>
        addComputedFields(
          sharedWithMeConversation as IAgentConversation,
          userId,
        ),
      ),
      orgId as string,
    );

    // Build response metadata
    const response = {
      conversations: visibleConversations,
      sharedWithMeConversations: processedSharedWithMeConversations,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(filter, req.query),
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

const getAgentConversationByIdOf = async (
  appConfig: AppConfig,
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

    // Build the base filter with access control
    let contentMatchIds: mongoose.Types.ObjectId[] | undefined;
    if (req.query.search) {
      const escapedSearch = validateAndEscapeSearch(req.query.search, {
        formatSpecifiers: true,
      });
      contentMatchIds = await findSessionIdsMatchingContent(
        orgId,
        escapedSearch,
      );
    }
    const accessibleProjectIds = await ProjectService.getAccessibleProjectIds(
      orgId,
      userId,
    );
    const baseFilter = buildAgentConversationFilter(
      req,
      orgId,
      userId,
      agentKey as string,
      conversationId as string,
      contentMatchIds,
      accessibleProjectIds,
      true,
    );

    // Build message filter
    const messageFilter = buildMessageFilter(req);

    // Get sort options for messages
    const messageSortOptions = buildMessageSortOptions(
      sortBy as string,
      sortOrder as string,
    );

    const session = await ChatSession.findOne({
      ...baseFilter,
      ...ONLY_AGENT,
    })
      .select({
        orgId: 1,
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

    // A recipient may be on the conversation only through a share, so the
    // artifacts the owner created since sharing are granted on first view.
    const initiatorId = session.initiator.toString();
    const isRecipient = initiatorId !== userId;
    if (page === 1 && isRecipient) {
      await syncConversationRecordPermissions({
        appConfig,
        records: 'artifacts',
        method: HttpMethod.POST,
        grantorUserId: initiatorId,
        orgId: orgId as string,
        body: { conversationId, userIds: [userId] },
        requestId,
        conversationId,
        timeoutMs: CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS,
        maxAttempts: 1,
      });
    }

    const sessionId = session._id as unknown as Types.ObjectId;

    const totalMessages = await ChatSessionMessage.countDocuments({
      sessionId,
      orgId: session.orgId,
    });

    const { skip, limit: effectiveLimit } = olderMessagesWindow(
      totalMessages,
      page,
      limit,
    );

    const messages = await getMessages(sessionId, {
      orgId: session.orgId,
      skip,
      limit: effectiveLimit,
      populateCitations: true,
    });

    if (isRecipient) {
      const attachmentRecordIds = collectChatAttachmentRecordIds(messages);
      if (attachmentRecordIds.length > 0) {
        await syncConversationRecordPermissions({
          appConfig,
          records: 'attachments',
          method: HttpMethod.POST,
          grantorUserId: initiatorId,
          orgId: orgId as string,
          body: { userIds: [userId], recordIds: attachmentRecordIds },
          requestId,
          conversationId,
          timeoutMs: CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS,
          maxAttempts: 1,
        });
      }
    }

    const conversationWithMessages = attachMessages(session, messages);

    // Sort messages using existing helper
    const sortedMessages = sortMessages(
      (conversationWithMessages?.messages ||
        []) as unknown as IMessageDocument[],
      messageSortOptions as { field: keyof IMessage },
    );

    // Build conversation response using existing helper
    const builtResponse = buildConversationResponse(
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
    );
    // Another viewer (a project member, or a user it is shared with) sees only
    // what they may read of each message.
    builtResponse.messages = await redactMessagesFor(
      appConfig,
      viewerOf(userId, orgId),
      builtResponse.messages,
      () => initiatorId,
    );
    // The recipient list is the owner's business: a viewer is told who shared
    // the chat, never who else can read it.
    const conversationResponse = await attachSharedByIfRecipient(
      builtResponse.access.isOwner
        ? builtResponse
        : { ...builtResponse, sharedWith: undefined },
      orgId as string,
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

export const getAgentConversationById =
  (appConfig: AppConfig): ConversationHandler =>
  (req, res, next) =>
    getAgentConversationByIdOf(appConfig, req, res, next);

export const deleteAgentConversationById =
  (appConfig: AppConfig) =>
  (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> =>
    deleteAgentConversationOf(appConfig, req, res, next);

const deleteAgentConversationOf = async (
  appConfig: AppConfig,
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
      conversationId as string,
      agentKey as string,
      userId as string,
      orgId as string,
      () => deleteConversationArtifacts(appConfig, String(conversationId), req),
    );

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
          _id: conversationId,
          userId,
          orgId,
          agentKey,
          isDeleted: false,
          ...ONLY_AGENT,
        },
        null,
        { session: s },
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      if (conversation.isArchived) {
        throw new BadRequestError('Agent conversation already archived');
      }

      const updated = await ChatSession.findOneAndUpdate(
        { _id: conversationId, ...ONLY_AGENT },
        {
          $set: {
            isArchived: true,
            archivedBy: userId,
            lastActivityAt: Date.now(),
          },
        },
        { returnDocument: 'after', session: s, runValidators: true },
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
          _id: conversationId,
          userId,
          orgId,
          agentKey,
          isDeleted: false,
          ...ONLY_AGENT,
        },
        null,
        { session: s },
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      if (!conversation.isArchived) {
        throw new BadRequestError('Agent conversation is not archived');
      }

      const updated = await ChatSession.findOneAndUpdate(
        { _id: conversationId, ...ONLY_AGENT },
        {
          $set: {
            isArchived: false,
            archivedBy: null,
            lastActivityAt: Date.now(),
          },
        },
        { returnDocument: 'after', session: s, runValidators: true },
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

export const listAllArchivesAgentConversation =
  () =>
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
      let contentMatchIds: mongoose.Types.ObjectId[] | undefined;
      if (req.query.search) {
        const escapedSearch = validateAndEscapeSearch(req.query.search, {
          formatSpecifiers: true,
        });
        contentMatchIds = await findSessionIdsMatchingContent(
          orgId,
          escapedSearch,
        );
      }
      const filter = {
        ...buildAgentConversationFilter(
          req,
          orgId,
          userId,
          agentKey as string,
          undefined,
          contentMatchIds,
        ),
        isArchived: true,
        archivedBy: { $exists: true },
      };
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

      const processedConversations = conversations.map((conversation: any) => ({
        ...addComputedFields(conversation as IAgentConversation, userId),
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
        filters: buildFiltersMetadata(filter, req.query),
        summary: {
          totalArchived: totalCount,
          oldestArchive: processedConversations[0]?.archivedAt,
          newestArchive:
            processedConversations[processedConversations.length - 1]
              ?.archivedAt,
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
      _id: conversationId,
      orgId,
      userId,
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
      _id: conversationId,
      orgId,
      userId,
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

      const deletedAgentKeys = await fetchDeletedAgentKeysForUser(
        appConfig,
        req,
      );

      // Agent-level pagination params
      const agentPage = Math.max(
        1,
        parseInt(req.query.agentPage as string, 10) || 1,
      );
      const agentLimit = Math.min(
        100,
        Math.max(
          1,
          parseInt(req.query.agentLimit as string, 10) ||
            AGENT_ARCHIVES_INITIAL_AGENT_LIMIT,
        ),
      );
      const agentSkip = (agentPage - 1) * agentLimit;

      logger.debug('Fetching all agents archived conversations (grouped)', {
        requestId,
        userId,
        agentPage,
        agentLimit,
      });

      const filter: Record<string, unknown> = {
        orgId: new mongoose.Types.ObjectId(`${orgId}`),
        $or: [{ userId: new mongoose.Types.ObjectId(`${userId}`) }],
        isArchived: true,
        archivedBy: { $exists: true },
        isDeleted: false,
        ...ONLY_AGENT,
      };
      if (deletedAgentKeys !== null) {
        filter.agentKey = { $nin: deletedAgentKeys };
      }

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
              conversations: {
                $slice: ['$conversations', AGENT_ARCHIVES_INITIAL_CHAT_LIMIT],
              },
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
        pagination: buildPaginationMetadata(
          g.totalCount,
          1,
          AGENT_ARCHIVES_INITIAL_CHAT_LIMIT,
        ),
      }));

      logger.debug(
        'Successfully fetched grouped archived agent conversations',
        {
          requestId,
          agentGroupCount: groups.length,
          totalAgentCount,
          duration: Date.now() - startTime,
        },
      );

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

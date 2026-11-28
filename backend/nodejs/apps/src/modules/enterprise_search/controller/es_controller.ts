import { historyLabel } from '../../../libs/repositories/agent-harness/history-source/mongo';
import { withServedAssistantMode } from '../validators/es_validators';
import {
  buildMessageSortOptions,
  markConversationFailed,
  saveCompleteAgentConversation,
  saveCompleteConversation,
  markAgentConversationFailed,
  deleteAgentConversation,
  replaceMessageWithError,
  initializeSSEResponse,
  sendSSEErrorEvent,
  sendSSECompleteEvent,
  searchDegradedMeta,
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
import { Request, Response, NextFunction } from 'express';
import type { Readable } from 'stream';
import { refuseIfDraining } from '../../../libs/repositories/drain';
import { randomBytes } from 'crypto';
import type { Tx } from '../../../libs/repositories/uow';
import { newObjectId } from '../../../libs/repositories/ids';
import { createUnitOfWork } from '../../../libs/repositories/doc-store';
import {
  getConversationRepository,
  type ChatSessionDto,
  type ConversationRepository,
  type NewMessage,
  type NewSession,
  type SessionActor,
  type SessionScope,
} from '../../../libs/repositories/conversation';
import { idOf, isValidId } from '../../../libs/repositories/ids';
import { getUserRepository } from '../../../libs/repositories/user';
import {
  getSemanticSearchRepository,
  type SearchQuery,
  type SemanticSearchRepository,
} from '../../../libs/repositories/semantic-search';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import { logLevelFor } from '../../../libs/utils/error.middleware.utils';
import {
  handleBackendError,
  SERVICE_UNAVAILABLE_MESSAGE,
} from '../../../libs/errors/backend-error';
import { withCause } from '../../../libs/errors/base.error';
import {
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { upstreamFetch } from '../../../libs/http/upstream-call';
import { injectRequestHeaders } from '../../../libs/context/request-context';
import {
  AIServiceResponse,
  AiSearchResponse,
  IAgentConversation,
  IAIResponse,
  IChatAttachmentRef,
  IMessage,
  IMessagePart,
} from '../types/conversation.interfaces';
import { IConversation } from '../types/conversation.interfaces';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  addComputedFields,
  attachSharedBy,
  attachSharedByIfRecipient,
  withoutOwnerOnlyFields,
  buildFiltersMetadata,
  buildConversationResponse,
  buildSessionQuery,
  parseSessionQueryParams,
  editedTitles,
  dateRangeFilterOf,
  buildMessageFilter,
  buildPaginationMetadata,
  buildSortOptions,
  buildUserQueryMessage,
  conversationTitleFromQuery,
  extractModelInfo,
  formatPreviousConversations,
  getPaginationParams,
  olderMessagesWindow,
  sortMessages,
  attachPopulatedCitations,
  appendMessages,
  getMessages,
  REGENERATE_TAIL_MESSAGES,
  attachMessages,
  appendMessageFeedback,
  findSessionIdsMatchingContent,
  validateAndEscapeSearch,
  withoutErrorStacks,
  withoutErasureMarker,
  persistCompletedAnswer,
  savePartialConversation,
  keepRunAlive,
  releaseRun,
  reserveRun,
  type RunReservation,
} from '../utils/utils';
import {
  historyForModel,
  newReadable,
  messageActor,
  redactConversationFor,
  redactMessagesFor,
  redactSearchesFor,
  viewerOf,
  startsTurn,
  type ViewerMessage,
} from '../utils/viewer-redaction';
import { apiRecord, apiStoredRecords } from '../utils/internal-record-fields';
import {
  attachUpstreamAbort,
  DETACHED_RUN_MAX_MS,
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
import { emitTurnStarted } from '../utils/compare-turn';
import { ChatStreamRun } from '../utils/chat-stream-run';
import {
  readAIStreamChunk,
  type RunErrorInfo,
} from '../utils/chat-stream-reader';
import { notifyChatShared } from '../utils/share-notification';
import { IAMServiceCommand } from '../../../libs/commands/iam/iam.service.command';
import { AppConfig } from '../../tokens_manager/config/config';
import { SharedCitationService } from '../../agent-harness/application/shared-citations';
import { SharedArtifactService } from '../../agent-harness/application/shared-artifacts';
import { messageArtifactIds } from '../../agent-harness/projection/artifact-access';
import { messageSources } from '../../agent-harness/projection/citation-source';
import type {
  ParticipantsChanged,
  SessionParticipantRecorder,
} from '../../agent-harness/ports/session-participants';
import { SessionErasureQueue } from '../../agent-harness/ports/session-erasure';
import type { SessionRunStopperPort } from '../../agent-harness/application/session-run-stopper';
import type { RunFeedbackService } from '../../agent-harness/application/run-feedback';
import type { AgentDataEraser } from '../../agent-harness/ports/agent-erasures';
import type { AgentErasureQueue } from '../../agent-harness/ports/agent-erasures';
import { AGENT_DELETE_CONFIRM_AFTER_MS } from '../../agent-harness/domain/session-erasure';
import { principalRefFor } from '../../agent-harness/domain/run-spec';
import { resolvedProxyHeaders } from '../../agent-harness/transport/resolved-turn-header';
import {
  scheduledConversationOrigin,
  type ScheduledConversationOrigin,
  scheduledTurnDetachMs,
  scheduledTurnHeaders,
  scheduledTurnOf,
  scheduledTurnOrigin,
} from '../../agent-harness/transport/scheduled-turn-mark';
import { shapedForViewer } from '../../agent-harness/domain/token-redaction';
import {
  inDurableTransaction,
  type StoreSession,
} from '../../../libs/repositories/agent-harness/durable-transaction';
import { sessionParticipantsOf } from '../../../libs/repositories/agent-harness/session-roles/mongo';
import {
  harnessSessionView,
  markErasureRequested,
} from '../../../libs/repositories/agent-harness/session-fields';
import { deleteConversationArtifacts } from '../../artifacts/services/conversation-artifacts.service';
import { CONVERSATION_STATUS } from '../constants/constants';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { validateNoFormatSpecifiers } from '../../../utils/xss-sanitization';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import {
  applyProjectScope,
  loadProjectForSession,
  accessibleProjectIdsFor,
  projectCallerFromRequest,
  resolveProjectLink,
  ResolvedProjectLink,
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
  resolveForkOrigin,
  resolveForks,
  resolveForkProjectId,
} from '../utils/fork-conversation';
import { ArtifactCopier, ArtifactCopyOutcome } from '../utils/fork-artifacts';
import { withForeignRunsDetached } from '../utils/copied-runs';
import { executeConnectorCommand } from '../../tokens_manager/utils/connector.utils';
import { resolveCallerTeamIds } from '../../projects/utils/team-membership';
import { ProjectsFeature } from '../../projects/utils/projects-feature';
import { ProjectService } from '../../projects/services/project.service';
import {
  assignAgentCapabilitiesToPayload,
  assignToolsToPayload,
  buildAiChatRequest,
  parseChatMode,
} from '../utils/ai-chat-payload';
import { hydrateScopedRequestAsUser } from '../utils/scoped-request';
import { expandedChatCommand, regenerateQuery } from '../utils/chat-command';
import { keepsTypedOnly } from '../../agent-harness/domain/commands';
import { envFlag } from '../../../libs/utils/env-flags';
import { getCurrentOrgId } from '../../../libs/context/orgContext';
const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

/** Node hand-builds the AI request body, so the protocol has to ride in it: a header alone never reaches Python. */
const withStreamProtocol = (
  payload: Record<string, unknown>,
  protocol: ReturnType<typeof resolveProtocol>,
): Record<string, unknown> =>
  isAGUI(protocol) ? { ...payload, protocol: AGUI_PROTOCOL } : payload;

/** The `.catch` line of a root RUN_ERROR's failure write, per route family (the write rethrows). */
const CHAT_MARK_FAILED_LOG =
  'Failed to mark conversation from AI RUN_ERROR SSE';
const AGENT_MARK_FAILED_LOG =
  'Failed to mark agent conversation from AI RUN_ERROR SSE';

/** Saves a root RUN_ERROR on the conversation: its text, Python's code and its stack. */
const failFromRunError = (
  markFailed: (
    conversation: ChatSessionDto,
    failReason: string,
    session: Tx | null,
    errorType?: string,
    stack?: string,
  ) => Promise<void>,
  failedLog: string,
  conversation: ChatSessionDto,
  error: RunErrorInfo,
  session: Tx | null,
): void => {
  void markFailed(
    conversation,
    error.message,
    session,
    aguiErrorCodeFromPayload(error.data),
    error.stack,
  ).catch((markErr: unknown) => {
    logger.error(failedLog, { error: markErr });
  });
};

/** Saves a question the agent asked as its own tool_call message, so a reload still shows it. */
const saveAskUserQuestion = (
  conversation: ChatSessionDto,
  payload: unknown,
  session: Tx | null,
): void => {
  const toolCallMessage = {
    messageType: 'tool_call' as const,
    content: '',
    tools: [{ toolName: 'ask_user_question', toolResult: payload }],
    createdAt: new Date(),
    updatedAt: new Date(),
  };
  void appendMessages(
    conversation._id,
    conversation.orgId,
    [toolCallMessage as unknown as NewMessage],
    session,
  ).catch((saveErr: unknown) => {
    logger.error('Failed to persist ask_user_question tool_call message', {
      error: saveErr,
    });
  });
};
const unitOfWork = createUnitOfWork(envFlag('REPLICA_SET_AVAILABLE'));
const convRepo = (): ConversationRepository => getConversationRepository();

/** `origin` on a conversation a schedule's firing made (ADR-SCH-02 D3); nothing on any other. */
const scheduleOriginField = (
  metadata: Map<string, unknown> | Record<string, unknown> | undefined,
): { origin?: ScheduledConversationOrigin } => {
  const origin = scheduledConversationOrigin(metadata);
  return origin === null ? {} : { origin };
};

/** A schedule's classic turn (ADR-SCH-02 D3): its conversation is named after the schedule and records which made it. */
const scheduledSessionFields = (
  req: Request,
): Pick<NewSession, 'id' | 'title' | 'metadata'> => {
  const mark = scheduledTurnOf(req);
  if (mark === null) return {};
  return {
    id: mark.conversationId,
    title: mark.title,
    metadata: { origin: scheduledTurnOrigin(mark) },
  };
};
const searches = (): SemanticSearchRepository => getSemanticSearchRepository();

const CHAT_SCOPE: SessionScope = { kind: 'chat' };
const agentScope = (agentKey: string | undefined): SessionScope => ({
  kind: 'agent',
  agentKey,
});
const actorOf = (orgId: unknown, userId: unknown): SessionActor => ({
  orgId: String(orgId),
  userId: String(userId),
});

const unconfiguredCitations = new SharedCitationService(null, logger);
const unconfiguredArtifacts = new SharedArtifactService(null, logger);

/**
 * The harness's record of who may read a conversation (SS1), asked per
 * request; undefined while the harness is off. Given one, a share and its
 * record commit in one durable transaction, or neither does.
 */
export type ParticipantRecorderSource = () =>
  | SessionParticipantRecorder<StoreSession>
  | undefined
  | Promise<SessionParticipantRecorder<StoreSession> | undefined>;

const noParticipantRecorder: ParticipantRecorderSource = () => undefined;

/** The per-viewer checks of a conversation read; unbound ones hide everything they check. */
export interface ViewerChecks {
  citations?: SharedCitationService;
  artifacts?: SharedArtifactService;
}

/** Whose read it is, and whether its citation check could be made. */
interface ViewerRead {
  orgId: string;
  sessionId: string;
  checkFailed: boolean;
}

/** A message a collaborator sent, or their run answered (SS2). */
const sentByCollaborator = (message: unknown): boolean => {
  const sender = (message as { senderId?: unknown } | null)?.senderId;
  return sender !== undefined && sender !== null;
};

/**
 * A reader who does not own the conversation sees a cited record only when
 * they can read it now (P1b C-6), and an artifact only when they can read
 * every source it was derived from (SS0); the others become placeholders.
 * The owner reads a collaborator's turns the same way (SS2): an answer ran
 * with the collaborator's access, not theirs. One check of each per page
 * read, legacy and durable sessions alike: this GET is the only shared view
 * of a conversation's history. Every message that is not the viewer's own
 * leaves the server through the shared redactor (SEC4).
 */
async function withViewerChecks<
  T extends {
    messages: unknown[];
    access: { isOwner: boolean };
    initiator?: unknown;
  },
>(
  response: T,
  req: AuthenticatedUserRequest,
  checks: ViewerChecks,
  ids: ViewerRead,
): Promise<T> {
  const checked = await withAccessChecks(response, req, checks, ids);
  const viewer = String(req.user?.userId);
  const owner = String(response.initiator);
  return {
    ...checked,
    messages: checked.messages.map((message) =>
      messageActor(message, owner) === viewer
        ? message
        : shapedForViewer(message),
    ),
  };
}

async function withAccessChecks<
  T extends { messages: unknown[]; access: { isOwner: boolean } },
>(
  response: T,
  req: AuthenticatedUserRequest,
  checks: ViewerChecks,
  ids: ViewerRead,
): Promise<T> {
  if (response.access.isOwner) {
    const foreign = response.messages.filter(sentByCollaborator);
    if (foreign.length === 0) return response;
    const checked = await checkedForViewer(foreign, req, checks, ids);
    const byOriginal = new Map(
      foreign.map((message, i) => [message, checked[i]]),
    );
    return {
      ...response,
      messages: response.messages.map(
        (message) => byOriginal.get(message) ?? message,
      ),
    };
  }
  return {
    ...response,
    messages: await checkedForViewer(response.messages, req, checks, ids),
  };
}

async function checkedForViewer(
  messages: unknown[],
  req: AuthenticatedUserRequest,
  checks: ViewerChecks,
  ids: ViewerRead,
): Promise<unknown[]> {
  const viewer = {
    authorization: req.headers.authorization,
    orgId: ids.orgId,
    sessionId: ids.sessionId,
    ...(req.context?.requestId !== undefined
      ? { requestId: req.context.requestId }
      : {}),
    surface: 'conversation' as const,
  };
  const citations = checks.citations ?? unconfiguredCitations;
  const [access, withArtifacts] = await Promise.all([
    // Without a record-access port the citations are left to the conversation's record-access redaction
    // (redactMessagesFor), which asks for them itself: one access decision per citation, never two.
    citations.checksRecords()
      ? citations.decide(viewer, messageSources(messages))
      : null,
    (checks.artifacts ?? unconfiguredArtifacts).decide(
      viewer,
      messageArtifactIds(messages),
    ),
  ]);
  if (access?.unavailable === true) ids.checkFailed = true;
  return withArtifacts.messages(access?.messages(messages) ?? messages);
}

/** A share change and who may read the conversation before and after it (SS1). */
interface ParticipantChange {
  updated: ChatSessionDto;
  participants: ParticipantsChanged;
}

/**
 * Runs a share change with the harness's record of who may read the
 * conversation (SS1): the change and its record commit in one durable
 * transaction of the document store, or neither does.
 */
async function withParticipantRecord(
  recorder: SessionParticipantRecorder<StoreSession> | undefined,
  change: (tx: Tx) => Promise<ParticipantChange>,
): Promise<ChatSessionDto> {
  if (recorder === undefined) {
    return (await unitOfWork.run(change)).updated;
  }
  return inDurableTransaction(async (tx) => {
    const { updated, participants } = await change(tx);
    await recorder.participantsChanged(participants, tx);
    return updated;
  });
}

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

export function buildSearchResponseForClient(
  data: AiSearchResponse & Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...data };
  if (Array.isArray(data.records)) {
    out.records = (data.records as unknown[]).map((r: unknown) =>
      omitId(apiRecord(r)),
    );
  }
  // A date-bounded search also returns the best matches written outside its dates.
  const outside = data.outsideDates;
  if (
    outside !== null &&
    typeof outside === 'object' &&
    !Array.isArray(outside)
  ) {
    const { records } = outside as { records?: unknown };
    if (Array.isArray(records)) {
      out.outsideDates = {
        ...outside,
        records: (records as unknown[]).map((r: unknown) => apiRecord(r)),
      };
    }
  }
  const vmap = data.virtual_to_record_map;
  if (vmap && typeof vmap === 'object' && !Array.isArray(vmap)) {
    out.virtual_to_record_map = Object.fromEntries(
      Object.entries(vmap as Record<string, unknown>).map(([k, v]) => [
        k,
        omitId(apiRecord(v)),
      ]),
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

/** Rows read per step, and steps allowed, when a turn runs past the edge of a page. */
const TURN_READ_ROWS = 50;
const TURN_READ_STEPS = 10;

/**
 * `redactMessagesFor` for one page of a conversation. A turn's rows go with its
 * hidden answer wherever they sit, and a turn can run past either edge of the
 * page, so the rows back to the turn's question and forward to the next one are
 * read through the repository and judged with the page. Each side reads at most
 * `TURN_READ_STEPS` steps of `TURN_READ_ROWS`; past that the turn's rows on the
 * page are hidden as unconfirmed, never shown.
 */
const redactPageFor = async <M extends ViewerMessage>(
  appConfig: AppConfig,
  viewer: ReturnType<typeof viewerOf>,
  conversation: { _id: string; orgId: string },
  messages: M[],
  skip: number,
  total: number,
  ownerOf: (message: M) => string | undefined,
  // The harness decided the citations of this page's messages for the viewer; rows read past its edges it never saw.
  citationsDecided = false,
  checkFailed = false,
): Promise<M[]> => {
  const read = async (from: number, limit: number): Promise<M[]> =>
    (await getMessages(conversation._id, {
      orgId: conversation.orgId,
      skip: from,
      limit,
    })) as M[];
  let lead: M[] = [];
  let leadUnknown = false;
  if (skip > 0 && messages.length > 0 && !startsTurn(messages[0] as M)) {
    let end = skip;
    for (let steps = 0; end > 0; steps += 1) {
      if (steps >= TURN_READ_STEPS) {
        leadUnknown = true;
        break;
      }
      const from = Math.max(0, end - TURN_READ_ROWS);
      const rows = await read(from, end - from);
      lead = [...rows, ...lead];
      end = from;
      if (rows.some(startsTurn)) break;
    }
  }
  const trail: M[] = [];
  let trailUnknown = false;
  for (let from = skip + messages.length, steps = 0; from < total; steps += 1) {
    if (steps >= TURN_READ_STEPS) {
      trailUnknown = true;
      break;
    }
    const rows = await read(from, Math.min(TURN_READ_ROWS, total - from));
    if (rows.length === 0) break;
    const next = rows.findIndex(startsTurn);
    if (next >= 0) {
      trail.push(...rows.slice(0, next));
      break;
    }
    trail.push(...rows);
    from += rows.length;
  }
  const page = new Set<M>(messages);
  const out = await redactMessagesFor(
    appConfig,
    viewer,
    [...lead, ...messages, ...trail],
    ownerOf,
    {
      leadUnknown,
      trailUnknown,
      checkFailed,
      ...(citationsDecided && {
        citationsDecided: (m: M) => page.has(m),
      }),
    },
  );
  return out.slice(lead.length, lead.length + messages.length);
};

// Common helper to start AI streams with consistent error mapping and logging
export const startAIStream = async (
  options: AICommandOptions,
  operation: string,
  signal?: AbortSignal,
): Promise<Readable | undefined> => {
  const aiServiceCommand = new AIServiceCommand(options);
  try {
    return await aiServiceCommand.executeStream(signal);
  } catch (error: unknown) {
    // The caller's own cap abandoned the open (see `attachUpstreamAbort`): it already finalized the run.
    if (signal?.aborted) {
      logger.debug('AI service stream open abandoned');
      return undefined;
    }
    // The call line and the error middleware log it; handleBackendError only maps it.
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
  const invalidImageMessage = `${file.originalname} is not a valid JPEG, PNG or WebP image`;
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

const CHAT_ATTACHMENT_MIME_BY_EXTENSION: Record<string, string> = {
  pdf: 'application/pdf',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  txt: 'text/plain',
  md: 'text/markdown',
  mdx: 'text/mdx',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  csv: 'text/csv',
  tsv: 'text/tab-separated-values',
};

/** Types a client sends when it does not know the real one (curl, SDKs, some browsers). */
const GENERIC_UPLOAD_MIMETYPES = new Set([
  '',
  'application/octet-stream',
  'binary/octet-stream',
  'application/x-binary',
  'application/binary',
]);

/**
 * The supported attachment type for an upload, or null. A generic or missing
 * content type falls back to the file extension, so `curl -F files=@x.xlsx`
 * is not refused for a type the error message says is supported.
 */
export const resolveChatAttachmentMimeType = (
  file: Pick<Express.Multer.File, 'mimetype' | 'originalname'>,
): string | null => {
  const declared = (file.mimetype || '').trim().toLowerCase();
  if (SUPPORTED_CHAT_ATTACHMENT_MIMETYPES.has(declared)) return declared;
  if (!GENERIC_UPLOAD_MIMETYPES.has(declared)) return null;
  const name = file.originalname || '';
  const dot = name.lastIndexOf('.');
  if (dot < 0) return null;
  const extension = name.slice(dot + 1).toLowerCase();
  // Own keys only: a file named "x.constructor" would otherwise resolve to Object.
  return Object.prototype.hasOwnProperty.call(
    CHAT_ATTACHMENT_MIME_BY_EXTENSION,
    extension,
  )
    ? (CHAT_ATTACHMENT_MIME_BY_EXTENSION[extension] ?? null)
    : null;
};

export const uploadChatAttachments =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const files = (req.files as Express.Multer.File[]) || [];
      if (!Array.isArray(files) || files.length === 0) {
        throw new BadRequestError('At least one file is required');
      }

      for (const file of files) {
        const resolved = resolveChatAttachmentMimeType(file);
        if (!resolved) {
          const received = (file.mimetype || '').trim() || 'none';
          throw new BadRequestError(
            `Unsupported attachment type: ${file.originalname} (received content type: ${received}). Supported types: PDF, JPEG, PNG, TXT, MD, MDX, DOCX, XLSX, CSV, TSV.`,
          );
        }
        file.mimetype = resolved;
      }

      const conversationIdRaw = req.body?.conversationId;
      const conversationId =
        typeof conversationIdRaw === 'string' &&
        conversationIdRaw.trim().length > 0
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
      // An error answer that was not JSON (a proxy's page) is mapped, so it keeps the envelope and Retry-After.
      if (aiResponse.statusCode >= 400 && aiResponse.data === undefined) {
        throw handleBackendError(aiResponse, 'Upload Chat Attachments');
      }
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
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
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

      const aiRes = await upstreamFetch(aiUrl, {
        method: 'DELETE',
        headers: injectRequestHeaders(forwardHeaders),
      });
      res.status(aiRes.status || 204).end();
    } catch (error: any) {
      next(handleBackendError(error, 'Delete Chat Attachment'));
    }
  };

export const uploadChatAttachmentsInternal =
  (appConfig: AppConfig, keyValueStoreService?: KeyValueStoreService) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
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
        typeof conversationIdRaw === 'string' &&
        conversationIdRaw.trim().length > 0
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
      // An error answer that was not JSON (a proxy's page) is mapped, so it keeps the envelope and Retry-After.
      if (aiResponse.statusCode >= 400 && aiResponse.data === undefined) {
        throw handleBackendError(
          aiResponse,
          'Upload Chat Attachments (Internal)',
        );
      }
      const statusCode = aiResponse?.statusCode || 500;
      const responseData = aiResponse?.data || {};

      res.status(statusCode).json(responseData);
    } catch (error: any) {
      next(handleBackendError(error, 'Upload Chat Attachments (Internal)'));
    }
  };

export const streamChat =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next?: NextFunction,
  ): Promise<void> => {
    if (await refuseIfDraining(res)) return;
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const run = new ChatStreamRun(req, res);
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    // The stream outlives the request, so its listeners write outside any transaction.
    const session: Tx | null = null;
    let savedConversation: ChatSessionDto | null = null;

    const modelInfo = extractModelInfo(req.body);
    const protocol = resolveProtocol(
      req.body as Record<string, unknown>,
      req.query,
    );

    if (!req.body.query) {
      throw new BadRequestError('Query is required');
    }

    let projectLink: ResolvedProjectLink;
    try {
      // Before the stream opens, so a project the caller cannot use is refused
      // with its own status rather than a 200 stream that ends in RUN_ERROR.
      projectLink = await resolveProjectLink(
        String(orgId),
        String(userId),
        req.body as Record<string, unknown>,
        projectCallerFromRequest(req, appConfig),
      );
    } catch (error) {
      if (!next) throw error;
      next(error);
      return;
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
        expandedChatCommand(res),
      );

      const userConversationData: NewSession = {
        orgId: String(orgId),
        userId: String(userId),
        initiator: String(userId),
        title: conversationTitleFromQuery(String(req.body.query)),
        lastActivityAt: Date.now(),
        status: CONVERSATION_STATUS.INPROGRESS,
        // Store model and mode information
        modelInfo: modelInfo,
        sessionType: 'chat',
        ...scheduledSessionFields(req),
        ...(projectLink.projectId
          ? {
              projectId: projectLink.projectId,
              projectVisibility: projectLink.projectVisibility,
            }
          : {}),
      };

      // One transaction on a replica set, plain writes otherwise
      savedConversation = await unitOfWork.run((tx) =>
        convRepo().openSession(userConversationData, userQueryMessage, tx),
      );

      if (!savedConversation) {
        throw new InternalServerError('Failed to create conversation');
      }

      const newConversationId = savedConversation._id || '';
      run.bind({ conversationId: savedConversation._id });

      logger.debug('Initial conversation created');

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
                ...(projectLink.projectId
                  ? { projectId: projectLink.projectId }
                  : {}),
              },
            })
          : `event: connected\ndata: ${JSON.stringify({
              message: 'SSE connection established',
              conversationId: newConversationId,
              title: savedConversation.title || undefined,
              ...(projectLink.projectId
                ? { projectId: projectLink.projectId }
                : {}),
            })}\n\n`,
      );
      (res as any).flush?.();
      const createdOrgId = savedConversation.orgId;
      await emitTurnStarted(res, req.body, newConversationId, async () => {
        const [firstRow] = (await getMessages(newConversationId, {
          orgId: createdOrgId,
          limit: 1,
        })) as Array<{ _id?: string }>;
        return firstRow?._id;
      });

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
          ...(await resolvedProxyHeaders(req, {
            conversationId: newConversationId,
          })),
          'Content-Type': 'application/json',
        },
        body: aiPayload,
        scheduledTurnHeaders: scheduledTurnHeaders(req),
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

      const upstreamAbort = attachUpstreamAbort(
        res,
        () => {
          if (streamSettled || !savedConversation) return;
          if (completeData || upstreamAiErrorEventForwarded) {
            runGuarded(finalizeRun);
            return;
          }
          streamSettled = true;
          void savePartialConversation(
            savedConversation,
            contentAccumulator.getText(),
            null,
            { parts: contentAccumulator.getParts() },
          ).then(
            () => {
              run.end({ kind: 'abandoned' });
            },
            (error: unknown) => {
              run.end({ kind: 'failed', error });
            },
          );
        },
        scheduledTurnDetachMs(req) ?? DETACHED_RUN_MAX_MS,
        { detached: scheduledTurnDetachMs(req) !== null },
      );
      // Opening failed: no stream will ever close the detached cap, so it is ended here (a scheduled turn's).
      const stream = await run
        .open(() =>
          startAIStream(aiCommandOptions, 'Chat Stream', upstreamAbort.signal),
        )
        .catch((error: unknown) => {
          upstreamAbort.dispose();
          throw error;
        });

      if (!stream) {
        upstreamAbort.dispose();
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, savedConversation._id);

      // Forward the AI stream; the hooks save this route's answer, error and question.
      stream.on('data', (chunk: Buffer) => {
        run.observe(chunk);
        buffer = readAIStreamChunk(chunk, buffer, res, contentAccumulator, {
          onAnswer: (result) => {
            completeData = result;
            run.sawResult(result);
          },
          onRunError: (error) => {
            upstreamAiErrorEventForwarded = true;
            run.sawRunError(error.data.code);
            if (savedConversation) {
              failFromRunError(
                markConversationFailed,
                CHAT_MARK_FAILED_LOG,
                savedConversation,
                error,
                session,
              );
            }
          },
          onAskUserQuestion: (payload) => {
            if (savedConversation) {
              saveAskUserQuestion(savedConversation, payload, session);
            }
          },
        });
      });

      const finalizeRun = async (): Promise<void> => {
        streamSettled = true;
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
                ...searchDegradedMeta(completeData),
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
          run.end({ kind: 'answered' });
        } catch (dbError: any) {
          run.end({ kind: 'failed', error: dbError });

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
                { error: markErr },
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
        runGuarded(finalizeRun);
      });

      stream.on('error', async (error: Error) => {
        if (upstreamAbort.isAborted()) return;
        streamSettled = true;
        run.end({ kind: 'failed', error, fromStream: true });
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
            error: dbError,
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
          error: dbError,
        });
      }

      if (!res.headersSent) {
        res.writeHead(500, {
          'Content-Type': 'text/event-stream; charset=utf-8',
        });
      }
      run.end({ kind: 'failed', error });
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
      await streamChat(appConfig)(req, res);
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

/**
 * Whether the caller owns the conversation they are posting to, checked
 * before the stream opens: a project member posting into someone else's
 * project-shared chat used to get a `connected` event carrying that chat's
 * id, then a RUN_ERROR. A refusal or a failed lookup goes to `next` (or is
 * thrown, for the internal wrappers that pass none) and this returns false.
 */
const ownsConversationBeforeStream = async (
  scope: SessionScope,
  conversationId: string,
  actor: SessionActor,
  next: NextFunction | undefined,
): Promise<boolean> => {
  let error: unknown;
  try {
    const found = isValidId(conversationId)
      ? await convRepo().findForTurn(scope, conversationId, actor)
      : null;
    if (found !== null) return true;
    error = new NotFoundError('Conversation not found');
  } catch (lookupError) {
    error = lookupError;
  }
  if (!next) throw error;
  next(error);
  return false;
};

export const addMessageStream =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next?: NextFunction,
  ): Promise<void> => {
    if (await refuseIfDraining(res)) return;
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const run = new ChatStreamRun(req, res);
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { conversationId } = req.params;

    // The stream outlives the request, so its listeners write outside any transaction.
    const session: Tx | null = null;
    let existingConversation: ChatSessionDto | null = null;
    let addedQuestionId: string | undefined;
    let reservation: RunReservation | null = null;

    const modelInfo = extractModelInfo(req.body);
    const protocol = resolveProtocol(
      req.body as Record<string, unknown>,
      req.query,
    );

    if (!req.body.query) {
      refuseBeforeStream(next, 'Query is required');
      return;
    }
    if (
      !(await ownsConversationBeforeStream(
        CHAT_SCOPE,
        conversationId as string,
        actorOf(orgId, userId),
        next,
      ))
    ) {
      return;
    }

    // Helper function that contains the common conversation operations
    async function performAddMessageStream(tx?: Tx): Promise<void> {
      // Get existing conversation
      const conversation = await convRepo().findForTurn(
        CHAT_SCOPE,
        conversationId as string,
        actorOf(orgId, userId),
      );

      if (!conversation) {
        throw new NotFoundError('Conversation not found');
      }

      // First, add the user message to the existing conversation
      const [addedQuestion] = await appendMessages(
        conversation._id,
        conversation.orgId,
        [
          buildUserQueryMessage(
            req.body.query,
            req.body.appliedFilters,
            req.body.chatMode,
            req.body.attachments,
            expandedChatCommand(res),
          ),
        ],
        tx,
      );
      addedQuestionId = addedQuestion?._id;

      // Mark the run in progress (clearing a previous error), update the model
      // and mode information if provided, and save
      const savedConversation = await convRepo().recordRunState(
        conversation._id,
        {
          status: CONVERSATION_STATUS.INPROGRESS,
          failReason: null,
          modelInfo: req.body,
          lastActivityAt: Date.now(),
        },
        tx,
      );

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

      reservation = await reserveRun(
        CHAT_SCOPE,
        conversationId as string,
        actorOf(orgId, userId),
      );

      // Get existing conversation and add user message
      await unitOfWork.run((tx) => performAddMessageStream(tx));

      if (!existingConversation) {
        throw new NotFoundError('Conversation not found');
      }
      const confirmedConversation = existingConversation as ChatSessionDto;
      run.bind({ conversationId: confirmedConversation._id });
      await emitTurnStarted(res, req.body, confirmedConversation._id, () =>
        Promise.resolve(addedQuestionId),
      );

      // Format previous conversations for context (excluding the user message we just added)
      const accessMemo = newReadable();
      // The history's access check runs while the project loads: both come before the first token.
      const [allMessagesSoFar, followUpProject] = await Promise.all([
        historyForModel(
          appConfig,
          viewerOf(userId, orgId),
          (await getMessages(
            confirmedConversation._id,
            { orgId: confirmedConversation.orgId, withLabels: true },
            session,
          )) as IMessage[],
          accessMemo,
        ),
        loadProjectForSession(
          confirmedConversation.orgId,
          confirmedConversation.userId,
          confirmedConversation.projectId,
          {
            caller: projectCallerFromRequest(req, appConfig),
            conversationId: confirmedConversation._id,
          },
        ),
      ]);
      const previousConversations = formatPreviousConversations(
        allMessagesSoFar.slice(0, -1),
      );
      const previousLabel = historyLabel(allMessagesSoFar.slice(0, -1));
      const aiRequest = buildAiChatRequest({ kind: 'assistant' }, req.body, {
        conversationId,
        previousConversations,
        historyLabel: previousLabel,
        isNewConversation: false,
        project: followUpProject,
      });
      const aiPayload = withStreamProtocol(aiRequest.payload, protocol);
      if (confirmedConversation.projectId) {
        void ProjectService.touchActivity(confirmedConversation.projectId);
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}${aiRequest.path}/stream`,
        method: HttpMethod.POST,
        headers: {
          ...(await resolvedProxyHeaders(req, {
            conversationId: conversationId as string,
          })),
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

      const upstreamAbort = attachUpstreamAbort(res, () => {
        if (streamSettled || !existingConversation) return;
        if (completeData || upstreamAiErrorEventForwarded) {
          runGuarded(finalizeRun);
          return;
        }
        streamSettled = true;
        void savePartialConversation(
          existingConversation,
          contentAccumulator.getText(),
          null,
          {
            parts: contentAccumulator.getParts(),
          },
        ).then(
          () => {
            run.end({ kind: 'abandoned' });
          },
          (error: unknown) => {
            run.end({ kind: 'failed', error });
          },
        );
      });
      const stream = await run.open(() =>
        startAIStream(
          aiCommandOptions,
          'Add Message Stream',
          upstreamAbort.signal,
        ),
      );

      if (!stream) {
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, confirmedConversation._id, reservation?.runId);

      // Forward the AI stream; the hooks save this route's answer, error and question.
      stream.on('data', (chunk: Buffer) => {
        run.observe(chunk);
        buffer = readAIStreamChunk(chunk, buffer, res, contentAccumulator, {
          onAnswer: (result) => {
            completeData = result;
            run.sawResult(result);
          },
          onRunError: (error) => {
            upstreamAiErrorEventForwarded = true;
            run.sawRunError(error.data.code);
            if (existingConversation) {
              failFromRunError(
                markConversationFailed,
                CHAT_MARK_FAILED_LOG,
                existingConversation,
                error,
                session,
              );
            }
          },
          onAskUserQuestion: (payload) => {
            if (existingConversation) {
              saveAskUserQuestion(existingConversation, payload, session);
            }
          },
        });
      });

      const finalizeRun = async (): Promise<void> => {
        streamSettled = true;
        try {
          // Save the AI response to the existing conversation
          if (completeData && existingConversation) {
            try {
              // Save citations, the AI message and the conversation's new state
              const saved = await persistCompletedAnswer(
                existingConversation,
                completeData,
                String(orgId),
                session,
                {
                  messageModelInfo: modelInfo,
                  failureLabel:
                    'Failed to update conversation with AI response',
                },
              );
              const savedCitations = saved.citations;

              // Return the updated conversation in the same format as addMessage
              const responseConversation = await redactConversationFor(
                appConfig,
                viewerOf(userId, orgId),
                await attachPopulatedCitations(
                  saved.conversation,
                  [saved.message],
                  savedCitations,
                  session,
                ),
                { known: accessMemo, freshIds: [saved.message._id] },
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
                  ...searchDegradedMeta(completeData),
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
          run.end({ kind: 'answered' });
        } catch (dbError: any) {
          run.end({ kind: 'failed', error: dbError });

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
        runGuarded(finalizeRun);
      });

      stream.on('error', async (error: Error) => {
        if (upstreamAbort.isAborted()) return;
        streamSettled = true;
        run.end({ kind: 'failed', error, fromStream: true });
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
            error: dbError,
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
      run.end({ kind: 'failed', error });

      try {
        // Mark conversation as failed if it exists
        if (existingConversation) {
          await markConversationFailed(
            existingConversation,
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
          error: dbError,
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
      await addMessageStream(appConfig)(req, res);
    } catch (error) {
      next(error);
    }
  };

export const getAllConversations = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  // Passed by the router so project-shared chats reach team members; absent in direct calls.
  appConfig?: AppConfig,
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

    let contentMatchIds: string[] | undefined;
    if (req.query.search) {
      const escapedSearch = validateAndEscapeSearch(req.query.search);
      contentMatchIds = await findSessionIdsMatchingContent(
        orgId,
        escapedSearch,
      );
    }
    const accessibleProjectIds = !isOwned
      ? await accessibleProjectIdsFor(
          String(orgId),
          String(userId),
          appConfig ? projectCallerFromRequest(req, appConfig) : undefined,
        )
      : undefined;
    const query = buildSessionQuery(req, {
      orgId: String(orgId),
      userId: String(userId),
      scope: CHAT_SCOPE,
      id: conversationId as string | undefined,
      owned: isOwned,
      shared: !isOwned,
      contentMatchIds,
      accessibleProjectIds: accessibleProjectIds?.map(String),
      archived: 'no',
      sort: sortOptions as Record<string, 1 | -1>,
      skip,
      limit,
      omitSharedWith: !isOwned,
      // Shared chats include agent chats, so a recipient opens them from the sidebar without finding the agent first.
      sharedIncludesAgentChats: !isOwned,
    });
    const filter = dateRangeFilterOf(query);

    const { rows: conversations, total: totalCount } =
      await convRepo().listSessions(query);

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
  const sessionMessages =
    await convRepo().listAttachmentsAndCitedRecords(conversationId);
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

/** One call to the query service's record-permission route: resolves on any status, rejects on a transport failure. */
const requestConversationRecordPermissions = async ({
  appConfig,
  records,
  method,
  grantorUserId,
  orgId,
  body,
  timeoutMs = CONVERSATION_PERMISSION_SYNC_TIMEOUT_MS,
  maxAttempts,
}: ConversationPermissionSyncOptions): Promise<AIServiceResponse<unknown>> => {
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
  return new AIServiceCommand<unknown>({
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
};

const isSuccess = (statusCode: number): boolean =>
  statusCode >= 200 && statusCode < 300;

// Best-effort: a failure is logged and never fails the share or the view.
const syncConversationRecordPermissions = async (
  options: ConversationPermissionSyncOptions,
): Promise<void> => {
  const { requestId, conversationId, records, method } = options;
  const logContext = { requestId, conversationId, records, method };
  try {
    const response = await requestConversationRecordPermissions(options);
    if (!isSuccess(response.statusCode)) {
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

const UNSHARE_OPERATION = 'Unshare conversation';

/** An unshare's revoke: anything but a 2xx fails the unshare (an outage keeps its Retry-After). */
const requireRecordPermissionsRevoked = async (
  options: ConversationPermissionSyncOptions,
): Promise<void> => {
  let response: AIServiceResponse<unknown>;
  try {
    response = await requestConversationRecordPermissions(options);
  } catch (error) {
    throw handleBackendError(error, UNSHARE_OPERATION);
  }
  if (isSuccess(response.statusCode)) return;
  // Node writes this request's body and token, so a 4xx is our fault, not the owner's:
  // relaying a 401 would send the owner's browser to re-login.
  throw response.statusCode < 500
    ? withCause(
        markClientSafe(
          new InternalServerError(serverFailureMessage(UNSHARE_OPERATION)),
        ),
        { status: response.statusCode },
      )
    : handleBackendError(response, UNSHARE_OPERATION);
};

export const getConversationById =
  (appConfig: AppConfig, checks: ViewerChecks = {}) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
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
      let contentMatchIds: string[] | undefined;
      if (req.query.search) {
        const escapedSearch = validateAndEscapeSearch(req.query.search);
        contentMatchIds = await findSessionIdsMatchingContent(
          orgId,
          escapedSearch,
        );
      }
      const accessibleProjectIds = await accessibleProjectIdsFor(
        String(orgId),
        String(userId),
        projectCallerFromRequest(req, appConfig),
      );
      const sessionQuery = buildSessionQuery(req, {
        orgId: String(orgId),
        userId: String(userId),
        scope: CHAT_SCOPE,
        id: conversationId,
        owned: true,
        shared: true,
        contentMatchIds,
        accessibleProjectIds: accessibleProjectIds.map(String),
        archived: 'no',
        sort: {},
        skip: 0,
        limit: 1,
      });

      // Build message filter
      const messageFilter = buildMessageFilter(req);

      // Get sort options for messages
      const messageSortOptions = buildMessageSortOptions(
        sortBy as string,
        sortOrder as string,
      );

      const session = await convRepo().findVisibleSummary(sessionQuery);

      if (!session) {
        throw new NotFoundError('Conversation not found');
      }

      // Sharing only grants READER on artifacts that existed at share time, so a
      // shared viewer is caught up on artifacts created since. Awaited so the edges
      // exist before the client requests artifact content; older pages carry no
      // new artifacts and skip it.
      const initiatorId = session.initiator;
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

      const sessionId = session._id;
      const harness = await harnessSessionView(sessionId, session.orgId);

      const totalMessages = await convRepo().countMessages(
        sessionId,
        session.orgId,
      );

      const { skip, limit: effectiveLimit } = olderMessagesWindow(
        totalMessages,
        page,
        limit,
      );

      // A fork written before forks detached their copies names the original's runs (FORKFIX).
      const messages = await withForeignRunsDetached(
        { _id: sessionId, metadata: harness.metadata },
        String(orgId),
        await getMessages(sessionId, {
          orgId: session.orgId,
          skip,
          limit: effectiveLimit,
          populateCitations: true,
        }),
      );

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

      const viewerRead = {
        orgId: String(orgId),
        sessionId,
        checkFailed: false,
      };
      const conversationResponse = await attachSharedByIfRecipient(
        await withViewerChecks(
          buildConversationResponse(
            withServedAssistantMode({
              ...conversationWithMessages,
              harnessVersion: harness.harnessVersion,
            }) as unknown as ChatSessionDto & { harnessVersion?: string },
            userId,
            {
              page,
              limit,
              skip,
              totalMessages,
              hasNextPage: skip > 0,
              hasPrevPage: skip + effectiveLimit < totalMessages,
            },
            (conversationWithMessages as { messages?: IMessage[] }).messages ||
              [],
          ),
          req,
          checks,
          viewerRead,
        ),
        orgId,
      );
      // Hidden in stored order, then sorted as the client asked: a turn's tool rows follow their
      // answer, and `sortBy` must not separate them first.
      conversationResponse.messages = sortMessages(
        (await redactPageFor(
          appConfig,
          viewerOf(userId, orgId),
          session,
          conversationResponse.messages,
          skip,
          totalMessages,
          (m) => messageActor(m, initiatorId),
          checks.citations?.checksRecords() === true,
          viewerRead.checkFailed,
        )) as never,
        messageSortOptions as { field: keyof IMessage },
      ) as unknown as typeof conversationResponse.messages;

      // Build filters metadata using existing helper
      const filtersMetadata = buildFiltersMetadata(
        messageFilter,
        query,
        messageSortOptions,
      );

      const [forkedFrom, forks] = await Promise.all([
        resolveForkOrigin(session, String(userId)),
        resolveForks(session, String(userId)),
      ]);

      // Prepare response using existing format
      const response = {
        conversation: {
          ...conversationResponse,
          ...(forkedFrom && { forkedFrom }),
          ...(forks.length > 0 && { forks }),
          ...scheduleOriginField(session.metadata),
        },
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

/**
 * After a delete commits, its chat's run stops now rather than at the erasure job's first step (SLO #7, QA-LAT-9).
 * A failure is only logged: the erasure job's `cancel_runs` stops it, with the same command id.
 */
async function stopDeletedChatRun(
  stopper: SessionRunStopperPort | undefined,
  orgId: string,
  sessionId: string,
  userId: string,
  requestId: string | undefined,
): Promise<void> {
  if (stopper === undefined) return;
  try {
    await stopper.stop(orgId, sessionId, userId);
  } catch (error: unknown) {
    logger.warn('Deleted chat run not stopped; erasure stops it', {
      requestId,
      conversationId: sessionId,
      errorType: error instanceof Error ? error.name : typeof error,
    });
  }
}

const deleteConversation = async (
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  erasures?: SessionErasureQueue<StoreSession>,
  stopper?: SessionRunStopperPort,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
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

    const actor = actorOf(orgId, userId);

    // The chat's artifacts go first, outside the transaction (a network call
    // would hold it open): if they cannot be deleted the chat stays, so a
    // retry finishes the cleanup instead of orphaning them.
    if (!(await convRepo().findDeletable(conversationId as string, actor))) {
      throw new NotFoundError('Conversation not found');
    }
    await deleteConversationArtifacts(appConfig, String(conversationId), req);

    const result = await unitOfWork.run(async (tx) => {
      const deleted = await convRepo().softDelete(
        conversationId as string,
        actor,
        tx,
      );
      if (deleted && erasures !== undefined) {
        // In the delete's own unit of work, so a lost enqueue is still found (E7B).
        await markErasureRequested(
          String(conversationId),
          String(orgId),
          Date.now(),
          tx,
        );
        await erasures.enqueue(
          {
            orgId: String(orgId),
            sessionId: String(conversationId),
            reason: 'user_delete',
            by: principalRefFor(String(userId)),
          },
          tx,
        );
      }
      return deleted;
    });
    if (!result) {
      throw new NotFoundError('Conversation not found');
    }
    await stopDeletedChatRun(
      stopper,
      String(orgId),
      String(conversationId),
      String(userId),
      requestId,
    );

    const response = {
      id: conversationId,
      status: 'deleted',
      deletedAt: result.session.updatedAt,
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
    next(error);
  }
};

/**
 * DELETE a conversation: soft-deletes it and, in the same transaction,
 * enqueues the erasure of everything stored for it (E7); the response is
 * unchanged. Without a queue (the harness module unbound) only the soft
 * delete happens.
 */
export const deleteConversationByIdWith =
  (
    appConfig: AppConfig,
    erasures?: SessionErasureQueue<StoreSession>,
    stopper?: SessionRunStopperPort,
  ) =>
  (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> =>
    deleteConversation(appConfig, req, res, next, erasures, stopper);

export const deleteConversationById = (
  appConfig: AppConfig,
): ReturnType<typeof deleteConversationByIdWith> =>
  deleteConversationByIdWith(appConfig);

/**
 * Which sessions a share/unshare handler may touch. Plain chats and agent
 * chats live in one store, so the scope is what keeps
 * `/conversations/:id/share` from sharing an agent chat and vice versa.
 */
type ShareScope = (req: AuthenticatedUserRequest) => SessionScope;

const plainChatShareScope: ShareScope = () => CHAT_SCOPE;
const agentChatShareScope: ShareScope = (req) =>
  agentScope(req.params.agentKey);

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
    {
      readOnlyShares = false,
      participants = noParticipantRecorder,
    }: {
      readOnlyShares?: boolean;
      participants?: ParticipantRecorderSource;
    } = {},
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const { conversationId } = req.params;
    let { userIds, accessLevel } = req.body;
    const scope = scopeFor(req);
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const recorder = await participants();

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

      const ownedConversation = await convRepo().findForShare(
        conversationId as string,
        actorOf(orgId, userId),
        scope,
      );

      if (!ownedConversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
      // Read before the merge below, which edits the shares in place.
      const before = sessionParticipantsOf(ownedConversation);

      // The recipients are checked before the transaction opens: one query, and no network call
      // while the transaction is held.
      recipientIds.forEach((id) => {
        if (!isValidId(id)) {
          throw new BadRequestError(`Invalid user ID format: ${id}`);
        }
      });
      const orgUserIds = new Set(
        (
          await getUserRepository().listLiveByIds(String(orgId), recipientIds)
        ).map((user) => idOf(user._id)),
      );
      const unknownId = recipientIds.find((id) => !orgUserIds.has(id));
      if (unknownId) {
        throw new BadRequestError(`User not found: ${unknownId}`);
      }
      const validUsers = recipientIds.map((id) => ({
        userId: id,
        accessLevel: (accessLevel || 'read') as 'read' | 'write',
      }));

      // Only users who did not already have access are told; re-sharing or a level change is not news.
      const alreadySharedIds = new Set(
        (ownedConversation.sharedWith || []).map((share) =>
          share.userId.toLowerCase(),
        ),
      );
      const newlySharedIds = recipientIds.filter(
        (id) => !alreadySharedIds.has(id),
      );

      async function performShareConversation(
        tx?: Tx,
      ): Promise<ParticipantChange> {
        const existingSharedWith = ownedConversation!.sharedWith || [];

        const existingUserMap = new Map(
          existingSharedWith.map((share) => [share.userId, share]),
        );

        const mergedSharedWith = [...existingSharedWith];

        for (const newUser of validUsers) {
          const existingUser = existingUserMap.get(newUser.userId);
          if (existingUser) {
            // Update access level if user already exists
            existingUser.accessLevel = newUser.accessLevel;
          } else {
            mergedSharedWith.push(newUser);
          }
        }

        const updatedConversation = await convRepo().setSharing(
          scope,
          conversationId as string,
          { isShared: true, sharedWith: mergedSharedWith },
          tx,
        );

        if (!updatedConversation) {
          throw new InternalServerError(
            'Failed to update conversation sharing settings',
          );
        }
        return {
          updated: updatedConversation,
          participants: {
            orgId: String(orgId),
            sessionId: String(conversationId),
            actorId: String(userId),
            before,
            after: sessionParticipantsOf(updatedConversation),
            requestId,
          },
        };
      }

      const updatedConversation = await withParticipantRecord(
        recorder,
        performShareConversation,
      );

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
        agentKey: scope.kind === 'agent' ? scope.agentKey : undefined,
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
      next(error);
    }
  };

type ConversationHandler = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

export const shareConversationById = (
  appConfig: AppConfig,
  participants: ParticipantRecorderSource = noParticipantRecorder,
): ConversationHandler =>
  createShareConversationHandler(appConfig, plainChatShareScope, {
    participants,
  });

/** Owner-only, read-only share of an agent conversation. */
export const shareAgentConversation = (
  appConfig: AppConfig,
  participants: ParticipantRecorderSource = noParticipantRecorder,
): ConversationHandler =>
  createShareConversationHandler(appConfig, agentChatShareScope, {
    readOnlyShares: true,
    participants,
  });

const createUnshareConversationHandler =
  (
    appConfig: AppConfig,
    scopeFor: ShareScope,
    participants: ParticipantRecorderSource = noParticipantRecorder,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const { conversationId } = req.params;
    const scope = scopeFor(req);
    try {
      const { userIds } = req.body;
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const recorder = await participants();

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
        if (!isValidId(id)) {
          throw new BadRequestError(`Invalid user ID format: ${id}`);
        }
      });
      // An ObjectId is hex, so 'ABC…' and 'abc…' are the same user.
      const removedIds = (userIds as unknown[]).map((id) =>
        String(id).toLowerCase(),
      );

      const findOwnedConversation = (): Promise<ChatSessionDto | null> =>
        convRepo().findForUnshare(
          conversationId as string,
          actorOf(orgId, userId),
          scope,
        );

      async function performUnshareConversation(
        tx?: Tx,
      ): Promise<ParticipantChange> {
        const conversation = await findOwnedConversation();

        if (!conversation) {
          throw new NotFoundError('Conversation not found or unauthorized');
        }
        const before = sessionParticipantsOf(conversation);

        // Remove specified users from sharedWith
        const existingSharedWith = conversation.sharedWith || [];

        const updatedSharedWith = existingSharedWith.filter(
          (share) => !removedIds.includes(idOf(share.userId)),
        );

        // Nobody left: no longer shared
        const updatedConversation = await convRepo().setSharing(
          scope,
          conversationId as string,
          {
            sharedWith: updatedSharedWith,
            ...(updatedSharedWith.length === 0
              ? { isShared: false, clearShareLink: true }
              : {}),
          },
          tx,
        );

        if (!updatedConversation) {
          throw new InternalServerError(
            'Failed to update conversation sharing settings',
          );
        }
        return {
          updated: updatedConversation,
          participants: {
            orgId: String(orgId),
            sessionId: String(conversationId),
            actorId: String(userId),
            before,
            after: sessionParticipantsOf(updatedConversation),
            requestId,
          },
        };
      }

      // Revoke before the list changes: a refused revoke leaves the user listed, so the owner can retry.
      if (!(await findOwnedConversation())) {
        throw new NotFoundError('Conversation not found or unauthorized');
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
      const revokesFor = (
        recordIds: string[],
      ): ConversationPermissionSyncOptions[] => [
        ...(recordIds.length > 0
          ? [
              {
                ...grantor,
                records: 'attachments' as const,
                body: { userIds: removedIds, recordIds },
              },
            ]
          : []),
        {
          ...grantor,
          records: 'artifacts',
          body: { conversationId, userIds: removedIds },
        },
      ];
      for (const revoke of revokesFor(attachmentRecordIds)) {
        await requireRecordPermissionsRevoked(revoke);
      }

      const updatedConversation = await withParticipantRecord(
        recorder,
        performUnshareConversation,
      );

      // Again after the commit: a view that read the old list may have re-granted in between, also on an
      // attachment added while the revokes ran, so the attachments are read again. It only has to beat that
      // view's grant, so it gets the view's budget; the unshare is committed, so a failed read is only logged.
      const latestAttachmentRecordIds =
        await loadConversationChatAttachmentRecordIds(
          String(conversationId),
        ).catch((error: unknown) => {
          logger.warn(
            'Could not re-read chat attachments after unshare; repeating the revoke on the earlier list',
            {
              requestId,
              conversationId,
              errorType: error instanceof Error ? error.name : typeof error,
            },
          );
          return attachmentRecordIds;
        });
      for (const revoke of revokesFor(latestAttachmentRecordIds)) {
        await syncConversationRecordPermissions({
          ...revoke,
          timeoutMs: CONVERSATION_VIEW_PERMISSION_SYNC_TIMEOUT_MS,
          maxAttempts: 1,
        });
      }

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
      next(error);
    }
  };

export const unshareConversationById = (
  appConfig: AppConfig,
  participants: ParticipantRecorderSource = noParticipantRecorder,
): ConversationHandler =>
  createUnshareConversationHandler(
    appConfig,
    plainChatShareScope,
    participants,
  );

export const unshareAgentConversation = (
  appConfig: AppConfig,
  participants: ParticipantRecorderSource = noParticipantRecorder,
): ConversationHandler =>
  createUnshareConversationHandler(
    appConfig,
    agentChatShareScope,
    participants,
  );

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
  // Passed by the router so a team member can move a chat into the project.
  appConfig?: AppConfig,
) => {
  const requestId = req.context?.requestId;
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { conversationId, agentKey } = req.params;
    const { projectId } = req.body as { projectId: string | null };

    const scope = agentKey ? agentScope(agentKey) : CHAT_SCOPE;

    const conversation = await convRepo().findOwnedByInitiator(
      scope,
      conversationId as string,
      actorOf(orgId, userId),
    );
    if (!conversation) {
      throw new NotFoundError('Conversation not found or unauthorized');
    }

    let link: {
      projectId: string;
      projectVisibility: 'private' | 'project';
    } | null = null;
    if (projectId !== null) {
      await ProjectsFeature.assertEnabled();
      const { project } = await ProjectService.assertAccess(
        orgId,
        userId,
        projectId,
        'viewer',
        appConfig ? await resolveCallerTeamIds(req, appConfig) : [],
      );
      ProjectService.assertNotArchived(project);
      const projectVisibility =
        conversation.projectVisibility === 'project'
          ? 'project'
          : project.chatSharing === 'members'
            ? 'project'
            : 'private';
      link = { projectId, projectVisibility };
      void ProjectService.touchActivity(projectId);
    }

    const updated = await convRepo().setProjectLink(
      scope,
      conversationId as string,
      link,
    );
    if (!updated) {
      throw new InternalServerError(
        'Failed to update conversation project link',
      );
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

    const scope = agentKey ? agentScope(agentKey) : CHAT_SCOPE;

    const conversation = await convRepo().findOwnedByInitiator(
      scope,
      conversationId as string,
      actorOf(orgId, userId),
    );
    if (!conversation) {
      throw new NotFoundError('Conversation not found or unauthorized');
    }
    if (!conversation.projectId) {
      throw new BadRequestError('Conversation is not linked to a project');
    }

    const updated = await convRepo().setProjectVisibility(
      scope,
      conversationId as string,
      visibility,
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
  scopeOf: (agentKey?: string) => SessionScope;
  /** Only the owner may regenerate; a member the chat is shared with may not. */
  ownerOnly?: boolean;
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
  question: IMessage & { _id: string };
  /** Rows between that question and the turn's last answer: the card, its answers, earlier replies. */
  intermediateIds: string[];
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
  older: Array<IMessage & { _id: string }>,
): CardTurnQuestion | null {
  const intermediateIds: string[] = [];
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
  older: Array<IMessage & { _id: string }>,
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
  older: Array<IMessage & { _id: string }>,
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
  const run = new ChatStreamRun(req, res);
  // The stream outlives the request, so its listeners write outside any transaction.
  const session: Tx | null = null;
  const { conversationId, messageId, agentKey } = req.params;
  const userId = req.user?.userId;
  const orgId = req.user?.orgId;
  // Changed answers to the card this reply followed; they take the place of the saved ones.
  const regenAnswersBody = req.body as { answers?: unknown } | undefined;
  const replacementAnswers: string | undefined =
    typeof regenAnswersBody?.answers === 'string'
      ? regenAnswersBody.answers
      : undefined;

  let existingConversation: ChatSessionDto | null = null;
  let reservation: RunReservation | null = null;

  const modelInfo = extractModelInfo(req.body);
  const protocol = resolveProtocol(
    req.body as Record<string, unknown>,
    req.query,
  );

  // Helper function to validate and get conversation
  async function performRegenerateAnswersValidation(tx?: Tx): Promise<{
    conversation: ChatSessionDto;
    userQuery: IMessage & { _id: string };
    staleAskToolCallIds: string[];
    cardEdit: CardTurnQuestion | null;
    editBlocked: boolean;
    newerQuestion: boolean;
  }> {
    if (!conversationId) {
      throw new BadRequestError('Conversation ID is required');
    }

    // Get conversation with access control
    const conversation = await convRepo().findForTurn(
      config.scopeOf(agentKey),
      conversationId,
      actorOf(orgId, userId),
      tx,
    );

    if (
      !conversation ||
      (config.ownerOnly &&
        (conversation.userId !== String(userId) ||
          conversation.initiator !== String(userId)))
    ) {
      throw new NotFoundError('Conversation not found or unauthorized');
    }

    // Newest-first tail (not just the last 2 rows): an ask_user_question turn
    // ends with `tool_call` rows saved after its answer, so the answer being
    // regenerated is not necessarily the newest message.
    const recentMessages = (await getMessages(
      conversation._id,
      {
        orgId: conversation.orgId,
        limit: REGENERATE_TAIL_MESSAGES,
        sort: -1,
      },
      tx,
    )) as Array<IMessage & { _id: string }>;

    if (recentMessages.length === 0) {
      throw new BadRequestError('No messages found in conversation');
    }

    const lastBot = recentMessages.find(
      (msg) => msg.messageType === 'bot_response',
    );
    if (!lastBot || lastBot._id !== messageId) {
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
      (msg) => msg._id === lastBot._id,
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
      config.scopeOf(agentKey),
      conversationId,
      actorOf(orgId, userId),
    );

    // Validate conversation and message
    const validationResult = await unitOfWork.run((tx) =>
      performRegenerateAnswersValidation(tx),
    );

    if (!validationResult) {
      throw new NotFoundError('Conversation or message not found');
    }
    run.bind({ conversationId: validationResult.conversation._id, agentKey });
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
    const accessMemo = newReadable();
    const allMessagesForRegen = (await getMessages(
      existingConversation._id,
      { orgId: existingConversation.orgId, withLabels: true },
      session,
    )) as Array<IMessage & { _id?: string }>;
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
        (msg) => msg._id?.toString() === cardEdit.question._id,
      );
      if (questionIdx >= 0) regenUserIdx = questionIdx;
    }
    const regenHistory =
      regenUserIdx >= 0
        ? allMessagesForRegen.slice(0, regenUserIdx)
        : allMessagesForRegen.slice(0, -2);
    // Only the history that goes to the model is checked, not the turn being redone.
    const previousConversations = formatPreviousConversations(
      await historyForModel(
        appConfig,
        viewerOf(userId, orgId),
        regenHistory,
        accessMemo,
      ),
    );

    // The title of a chat is its first question, so editing that question moves the title too,
    // unless the user already renamed the chat.
    const titleFollowsEdit =
      regenUserIdx === 0 &&
      (existingConversation.title === shownQuestion.content.slice(0, 100) ||
        existingConversation.title ===
          conversationTitleFromQuery(shownQuestion.content));
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
        shownQuestion._id,
        editedQuery,
        existingConversation._id,
        existingConversation.orgId,
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
        existingConversation.title = conversationTitleFromQuery(editedQuery);
        editedTitles.set(existingConversation, existingConversation.title);
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
            userQuery._id,
            userQuery.content,
            existingConversation._id,
            existingConversation.orgId,
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
      editedTitles.delete(existingConversation);
      try {
        await updateUserQueryContent(
          shownQuestion._id,
          shownQuestion.content,
          existingConversation._id,
          existingConversation.orgId,
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

    // A question stored as typed (`/review`) is asked as the route expanded it again (LC2FIX).
    const storedQuery = (question: {
      content: string;
      command?: IMessage['command'];
    }): string =>
      (keepsTypedOnly(question.command) ? regenerateQuery(res) : undefined) ??
      question.content;

    // Prepare AI payload
    const aiPayload: Record<string, unknown> = {
      // An edit asks the edited question; a `/review` message keeps only what was typed: the route expanded it again (LC2FIX).
      query:
        editedQuery ??
        (turnQuery !== null ? storedQuery(shownQuestion) : undefined) ??
        replacementAnswers ??
        storedQuery(userQuery),
      previousConversations: previousConversations || [],
      historyLabel: historyLabel(regenHistory),
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
      regenerationId: randomBytes(12).toString('hex'),
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
      existingConversation.orgId,
      existingConversation.userId,
      existingConversation.projectId,
      {
        caller: projectCallerFromRequest(req, appConfig),
        conversationId: existingConversation._id,
      },
    );
    applyProjectScope(aiPayload, regenProject);
    if (existingConversation.projectId) {
      void ProjectService.touchActivity(existingConversation.projectId);
    }

    const regenEndpoint = regenIsAgentMode
      ? `${appConfig.aiBackend}/api/v1/agent/agentIdPlaceholder/chat/stream`
      : config.buildAIEndpoint(appConfig, agentKey);

    const aiCommandOptions: AICommandOptions = {
      uri: regenEndpoint,
      method: HttpMethod.POST,
      headers: {
        ...(await resolvedProxyHeaders(req, {
          agentKey: regenIsAgentMode ? null : agentKey,
          conversationId: conversationId,
        })),
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

    const upstreamAbort = attachUpstreamAbort(res, () => {
      if (streamSettled || !existingConversation) return;
      if (completeData || upstreamAiErrorEventForwarded) {
        runGuarded(finalizeRun);
        return;
      }
      streamSettled = true;
      const abandoned = existingConversation;
      const partialText = contentAccumulator.getText();
      // A partial answer was shown for the edited question, so the question is saved with it.
      void (partialText ? saveEditedQuestion() : Promise.resolve())
        .catch((err: unknown) => {
          logger.error('Failed to save the edited question on disconnect', {
            error: err,
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
        .then(
          () => {
            run.end({ kind: 'abandoned' });
          },
          (error: unknown) => {
            run.end({ kind: 'failed', error });
          },
        )
        .finally(() => releaseRun(reservation));
    });
    const stream = await run.open(() =>
      startAIStream(
        aiCommandOptions,
        'Regenerate Answers Stream',
        upstreamAbort.signal,
      ),
    );

    if (!stream) {
      if (upstreamAbort.isAborted()) return;
      throw new Error('Failed to get stream from AI service');
    }
    upstreamAbort.bindStream(stream);
    keepRunAlive(stream, existingConversation._id, reservation?.runId);

    // Process SSE events, capture complete event, and forward non-complete events
    stream.on('data', (chunk: Buffer) => {
      run.observe(chunk);
      buffer = handleRegenerationStreamData(
        chunk,
        buffer,
        existingConversation,
        messageId || null,
        session,
        res,
        (data: IAIResponse) => {
          completeData = data;
          run.sawResult(completeData);
        },
        contentAccumulator,
        (code: unknown) => {
          upstreamAiErrorEventForwarded = true;
          run.sawRunError(code);
        },
        (payload: unknown) => {
          askUserQuestionPayload = payload;
        },
      );
    });

    const finalizeRun = async (): Promise<void> => {
      streamSettled = true;
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
              userQuery._id,
              replacementAnswers,
              existingConversation._id,
              existingConversation.orgId,
              session,
            );
            if (!answersRow) {
              throw new InternalServerError(
                'Failed to save the replacement answers',
              );
            }
            answersReplaced = true;
          }
          const { conversation: regeneratedConversation, savedCitations } =
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
            const shown = regeneratedConversation as {
              messages: Array<{ _id?: unknown }>;
            };
            const gone = new Set(replacedTurnIds.map(String));
            shown.messages = shown.messages.filter(
              (row) => !gone.has(String(row._id)),
            );
          }

          const responseConversation = await redactConversationFor(
            appConfig,
            viewerOf(userId, orgId),
            regeneratedConversation,
            { known: accessMemo, freshIds: [messageId] },
          );

          // Send final response event with the complete conversation data
          sendSSECompleteEvent(
            res,
            responseConversation,
            savedCitations.length,
            requestId || '',
            startTime,
            protocol,
            searchDegradedMeta(completeData),
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
            const updatedConversation = await convRepo().findById(
              conversationId || '',
            );
            if (updatedConversation) {
              const messages = await getMessages(
                updatedConversation._id,
                { orgId: updatedConversation.orgId },
                session,
              );
              const plainConversation = await redactConversationFor(
                appConfig,
                viewerOf(userId, orgId),
                attachMessages(updatedConversation, messages),
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
        run.end({ kind: 'answered' });
      } catch (dbError: any) {
        run.end({ kind: 'failed', error: dbError });
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
            const updatedConversation = await convRepo().findById(
              conversationId || '',
            );
            if (updatedConversation) {
              const messages = await getMessages(
                updatedConversation._id,
                { orgId: updatedConversation.orgId },
                session,
              );
              const plainConversation = await redactConversationFor(
                appConfig,
                viewerOf(userId, orgId),
                attachMessages(updatedConversation, messages),
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
              { error: replaceError },
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
      runGuarded(finalizeRun);
    });

    stream.on('error', async (error: Error) => {
      if (upstreamAbort.isAborted()) return;
      streamSettled = true;
      run.end({ kind: 'failed', error, fromStream: true });
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
          { appConfig, userId, orgId },
        );
      } catch (dbError: any) {
        logger.error('Failed to replace message with error', {
          error: dbError,
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
    run.end({ kind: 'failed', error });

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
        { appConfig, userId, orgId },
      );
    } catch (dbError: any) {
      logger.error('Failed to mark conversation as failed in catch block', {
        error: dbError,
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
  }
}

export const regenerateAnswers =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response) => {
    if (await refuseIfDraining(res)) return;
    await regenerateAnswersInternal(appConfig, req, res, {
      isAgentSession: false,
      scopeOf: () => CHAT_SCOPE,
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
      const conversation = await convRepo().findForTurn(
        CHAT_SCOPE,
        conversationId as string,
        actorOf(orgId, userId),
      );
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
      const backendError = handleBackendError(
        error,
        'Cancel Conversation Stream',
      );
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

    const applyTitleUpdate = async (tx: Tx) => {
      const found = await convRepo().findOwned(
        CHAT_SCOPE,
        conversationId as string,
        actorOf(orgId, userId),
        tx,
      );

      if (!found) {
        throw new NotFoundError('Conversation not found');
      }

      const conv = await convRepo().setTitle(found._id, req.body.title, tx);
      if (!conv) {
        throw new NotFoundError('Conversation not found');
      }
      return conv;
    };

    const conversation = await unitOfWork.run((tx) => applyTitleUpdate(tx));

    const response = {
      conversation: {
        ...withoutErrorStacks(conversation),
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
  id: conversation._id,
  title: conversation.title,
  projectId: conversation.projectId,
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
  ) => Promise<ChatSessionDto | null>;
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
  const conversationId = req.params.conversationId ?? '';
  const messageId = req.params.messageId ?? '';
  // Allocated up front: the artifacts are copied under this id before the chat
  // exists, and a retried transaction must reuse them, not copy again.
  const forkId = newObjectId();
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
    const result = await unitOfWork.run((tx) =>
      copySessionUpToMessage(
        source,
        messageId,
        userId,
        orgId,
        fields,
        tx,
        artifacts,
      ),
    );

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
        await target.discardArtifacts(forkId);
      } catch (cleanupError) {
        logger.warn('Could not remove the artifacts of a failed fork', {
          requestId,
          forkId,
          error:
            cleanupError instanceof Error
              ? cleanupError.message
              : String(cleanupError),
        });
      }
    }
    next(error);
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
      // An archived chat is out of the owner's list and cannot be forked (404).
      findSource: async (orgId, userId, conversationId) => {
        const source = await convRepo().findOwned(
          CHAT_SCOPE,
          conversationId,
          actorOf(orgId, userId),
        );
        return source?.isArchived ? null : (source ?? null);
      },
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
        convRepo().findOwned(
          { kind: 'agent', agentKey },
          conversationId,
          actorOf(orgId, userId),
        ),
    });
  };

/** G-15: where a rating finds its run and agent version, and the rated-answer sink is told of it. */
export type FeedbackRuns = Pick<RunFeedbackService, 'link' | 'record'>;

/** A feedback request body as its validator passed it. */
type FeedbackBody = Record<string, unknown> & {
  metrics?: { userInteractionTime?: unknown; feedbackSessionId?: unknown };
};

/** The question the rated answer answered: the conversation's last question before it. */
async function questionBefore(
  message: { sessionId: string; orgId: string; seq: number },
  tx?: Tx,
): Promise<string> {
  const PAGE = 50;
  for (let skip = 0; ; skip += PAGE) {
    const rows = await convRepo().listMessages(
      message.sessionId,
      { orgId: message.orgId, skip, limit: PAGE, order: 'desc' },
      tx,
    );
    const question = rows.find(
      (row) =>
        row.dto.seq < message.seq && row.dto.messageType === 'user_query',
    );
    if (question !== undefined) return question.dto.content;
    if (rows.length < PAGE) return '';
  }
}

/** The durable run that wrote a message (UX5); the repository's row carries it untyped. */
const runIdOfMessage = (message: object): string | undefined => {
  const runId: unknown = (message as { runId?: unknown }).runId;
  return typeof runId === 'string' && runId !== '' ? runId : undefined;
};

async function performFeedbackUpdate(params: {
  scope: SessionScope;
  conversationId: string;
  orgId: string;
  messageId: string;
  // `req.user.userId` as it came: not always a string, so the sink's copy is converted.
  userId: unknown;
  body: FeedbackBody;
  userAgent: string | undefined;
  tx?: Tx;
  runs?: FeedbackRuns | undefined;
}): Promise<{
  updatedConversation: ChatSessionDto;
  feedbackEntry: Record<string, unknown>;
}> {
  const { scope, messageId, userId, body, userAgent, tx, runs } = params;

  const conversation = await convRepo().findForFeedback(
    scope,
    params.conversationId,
    actorOf(params.orgId, userId),
    tx,
  );
  if (!conversation) {
    throw new NotFoundError('Conversation not found');
  }

  const message = await convRepo().findMessage(
    conversation._id,
    conversation.orgId,
    messageId,
    tx,
  );
  if (!message) {
    throw new NotFoundError('Message not found');
  }

  if (message.messageType !== 'bot_response') {
    throw new BadRequestError('Feedback is only allowed for bot responses');
  }

  const orgId = conversation.orgId;
  const sessionId = conversation._id;
  const link =
    runs !== undefined
      ? await runs.link(orgId, sessionId, runIdOfMessage(message))
      : null;
  // Before the rating is stored, so a retried request tells the sink again.
  if (runs !== undefined) {
    await runs.record({
      orgId,
      userId: String(userId),
      sessionId,
      messageId,
      link,
      body: { ...body },
      question: () => questionBefore(message, tx),
      answer: message.content,
    });
  }

  const feedbackEntry = {
    ...body,
    ...(link !== null
      ? {
          runId: link.runId,
          ...(link.agentKey !== null ? { agentKey: link.agentKey } : {}),
          ...(link.agentVersion !== null
            ? { agentVersion: link.agentVersion }
            : {}),
        }
      : {}),
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
    tx,
  );
  if (!updatedMessage) {
    throw new InternalServerError('Failed to update feedback');
  }

  return { updatedConversation: conversation, feedbackEntry };
}

export const updateFeedbackWith =
  (runs?: FeedbackRuns) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    try {
      const { conversationId, messageId } = req.params;
      const userId: unknown = req.user?.userId;
      const orgId: unknown = req.user?.orgId;

      logger.debug('Attempting to update conversation feedback', {
        requestId,
        message: 'Attempting to update conversation feedback',
        conversationId: req.params.conversationId,
        userId,
        feedback: req.body.feedback,
        timestamp: new Date().toISOString(),
      });

      const { updatedConversation, feedbackEntry } = await unitOfWork.run(
        (tx) =>
          performFeedbackUpdate({
            scope: CHAT_SCOPE,
            conversationId: conversationId!,
            orgId: String(orgId),
            messageId: messageId!,
            userId,
            body: req.body as FeedbackBody,
            userAgent: req.headers['user-agent'],
            tx,
            runs,
          }),
      );

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
    }
  };

export const updateFeedback = updateFeedbackWith();

export const archiveConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
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

    async function performArchiveConversation(tx?: Tx) {
      const conversation = await convRepo().findArchivable(
        CHAT_SCOPE,
        conversationId as string,
        actorOf(orgId, userId),
        tx,
      );

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no archive permission',
        );
      }

      if (conversation.isArchived) {
        throw new BadRequestError('Conversation already archived');
      }

      const updatedConversation = await convRepo().setArchived(
        CHAT_SCOPE,
        conversationId as string,
        { by: String(userId) },
        tx,
      );

      if (!updatedConversation) {
        throw new InternalServerError('Failed to archive conversation');
      }

      return updatedConversation;
    }

    const updatedConversation = await unitOfWork.run((tx) =>
      performArchiveConversation(tx),
    );

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

    async function performUnarchiveConversation(tx?: Tx) {
      const conversation = await convRepo().findArchivable(
        CHAT_SCOPE,
        conversationId as string,
        actorOf(orgId, userId),
        tx,
      );

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no unarchive permission',
        );
      }

      if (!conversation.isArchived) {
        throw new BadRequestError('Conversation is not archived');
      }

      const updatedConversation = await convRepo().setArchived(
        CHAT_SCOPE,
        conversationId as string,
        null,
        tx,
      );

      if (!updatedConversation) {
        throw new InternalServerError('Failed to unarchive conversation');
      }
      return updatedConversation;
    }

    const updatedConversation = await unitOfWork.run((tx) =>
      performUnarchiveConversation(tx),
    );

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

      const updated = await convRepo().setPinned(
        conversationId as string,
        actorOf(orgId, userId),
        pinned,
      );

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
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
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
      let contentMatchIds: string[] | undefined;
      if (req.query.search) {
        const escapedSearch = validateAndEscapeSearch(req.query.search);
        contentMatchIds = await findSessionIdsMatchingContent(
          orgId,
          escapedSearch,
        );
      }
      const query = buildSessionQuery(req, {
        orgId: String(orgId),
        userId: String(userId),
        scope: CHAT_SCOPE,
        id: conversationId as string | undefined,
        owned: true,
        shared: true,
        contentMatchIds,
        archived: 'only',
        sort: buildSortOptions(req) as Record<string, 1 | -1>,
        skip,
        limit,
      });
      const filter = dateRangeFilterOf(query);

      const { rows: conversations, total: totalCount } =
        await convRepo().listSessions(query);

      // The page's messages come in one read.
      const messageRows = await convRepo().listMessagesOfSessions(
        conversations.map((c) => c._id),
        { orgId: String(orgId) },
      );
      // Another viewer sees only what they may read of each message.
      const ownerBySession = new Map(
        conversations.map((c: any) => [String(c._id), String(c.initiator)]),
      );
      const entries = [...messageRows].flatMap(([id, rows]) =>
        rows.map((r) => ({ id, dto: r.dto })),
      );
      const sessionOfMessage = new Map<unknown, string>(
        entries.map((e) => [e.dto, e.id]),
      );
      const visibleMessages = await redactMessagesFor(
        appConfig,
        viewerOf(userId, orgId),
        entries.map((e) => e.dto),
        (m) =>
          messageActor(
            m,
            ownerBySession.get(sessionOfMessage.get(m) ?? '') ?? '',
          ),
      );
      // redactMessagesFor keeps the order, so each visible message belongs to the session of the stored one at its place.
      const messagesBySession = new Map<string, any[]>();
      visibleMessages.forEach((m, i) => {
        const id = entries[i]?.id ?? '';
        messagesBySession.set(id, [...(messagesBySession.get(id) ?? []), m]);
      });

      const processedConversations = conversations.map((conversation: any) => {
        const sessionMessages =
          messagesBySession.get(conversation._id.toString()) || [];
        const conversationWithMessages = attachMessages(
          conversation,
          sessionMessages,
        ) as IConversation;
        const conversationWithComputedFields = addComputedFields(
          conversationWithMessages,
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

      const deletedAgentKeys = await fetchDeletedAgentKeysForUser(
        appConfig,
        req,
      );

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
      const contentMatchIds = await findSessionIdsMatchingContent(
        orgId,
        escapedSearch,
      );

      const {
        rows: aggregateResult,
        assistantCount,
        agentCount,
      } = await convRepo().searchArchived({
        orgId: String(orgId),
        userId: String(userId),
        escapedSearch,
        contentMatchIds,
        deletedAgentKeys,
        skip,
        limit,
      });

      const totalCount = assistantCount + agentCount;

      const paginatedResults = aggregateResult.map((c: any) => ({
        ...addComputedFields(
          // Kept here as well as in addComputedFields: the owner-only fields never reach another viewer.
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
      } catch (error: unknown) {
        throw handleBackendError(error, 'Search');
      }

      // A search that found nothing, or could not check access, says why as codes the client words. The error is
      // mapped as for any other failure, so the body keeps its envelope (code, message, requestId); only the codes are added.
      // An error answer that was not JSON has no data: its status is still mapped below.
      const noticed = (
        aiResponse?.data as { accessNotices?: unknown } | undefined
      )?.accessNotices;
      if (
        (aiResponse.statusCode === 404 || aiResponse.statusCode === 503) &&
        Array.isArray(noticed)
      ) {
        const mapped = handleBackendError(aiResponse, 'Search');
        throw Object.assign(mapped, {
          metadata: {
            ...(mapped as { metadata?: object }).metadata,
            accessNotices: noticed,
          },
        });
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Search');
      }
      if (!aiResponse.data) {
        throw new InternalServerError('Failed to get response from AI service');
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
      // The hits are saved in one ordered insert (a search can return hundreds,
      // and one round trip per hit was costly), so the saved ids line up with
      // searchResults.
      const hits = results
        ? results.map((result: AiSearchResponse['searchResults'][number]) => ({
            content: result.content,
            chunkIndex: result.chunkIndex ?? 0, // fallback to 0 if not present
            citationType: result.citationType,
            metadata: result.metadata as unknown as Record<string, unknown>,
          }))
        : undefined;

      const recordsArray = aiResponse.data?.records || {};
      const recordsMap = new Map();

      if (Array.isArray(recordsArray)) {
        recordsArray.forEach((record) => {
          // Use either _id or _key as the unique key for each record
          const key = record._id || record._key;
          // `linkedRecords` was filtered for the user who ran this search. A
          // saved search can be shared, and its records are returned as
          // stored, so the links are kept out of what is saved.
          const stored: Record<string, unknown> = apiRecord({
            ...(record as Record<string, unknown>),
          });
          delete stored.linkedRecords;
          // Convert the record object to a string since your schema expects Map<string, string>
          recordsMap.set(key, JSON.stringify(stored));
        });
      }
      // Save the entire search operation as a single document
      const searchRecord = await searches().create({
        query,
        limit,
        orgId: String(orgId),
        userId: String(userId),
        hits,
        records: recordsMap,
      });

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
        searchResponse: buildSearchResponseForClient(
          aiResponse.data as AiSearchResponse & Record<string, unknown>,
        ),
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
 * A record page link (`/record/<id>`) as a full URL on the web app. The AI
 * service links uploaded files and records without a source URL to their page
 * in the web app by path; a retrieve caller is not the web app and cannot open
 * a bare path. Full URLs and anything else are returned as they are.
 */
const absoluteRecordUrl = (
  url: unknown,
  frontendUrl: string | undefined,
): unknown => {
  if (typeof url !== 'string' || !url.startsWith('/') || url.startsWith('//')) {
    return url;
  }
  const base = (frontendUrl ?? '').trim().replace(/\/+$/, '');
  return /^https?:\/\//i.test(base) ? `${base}${url}` : url;
};

const withAbsoluteRecordUrls = (
  chunks: unknown[],
  frontendUrl: string | undefined,
): unknown[] =>
  chunks.map((chunk) => {
    if (chunk === null || typeof chunk !== 'object') return chunk;
    const citation = (chunk as { citation?: unknown }).citation;
    if (citation === null || typeof citation !== 'object') return chunk;
    const { url, recordUrl } = citation as {
      url?: unknown;
      recordUrl?: unknown;
    };
    return {
      ...chunk,
      citation: {
        ...citation,
        url: absoluteRecordUrl(url, frontendUrl),
        recordUrl: absoluteRecordUrl(recordUrl, frontendUrl),
      },
    };
  });

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
        throw handleBackendError(error, 'Retrieve');
      }
      const data = aiResponse.data;
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Retrieve');
      }
      if (data === undefined) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      res.status(HTTP_STATUS.OK).json({
        requestId,
        ...data,
        ...(Array.isArray(data.chunks)
          ? {
              chunks: withAbsoluteRecordUrls(
                data.chunks,
                appConfig.frontendUrl,
              ),
            }
          : {}),
      });
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
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
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
      const queryParts = parseSessionQueryParams(req);
      const filter = dateRangeFilterOf(queryParts);
      const [sortField] = Object.keys(sortOptions);

      logger.debug('Attempting to get search history', {
        requestId,
        timestamp: new Date().toISOString(),
      });

      const { rows: plain, total: totalCount } = await searches().listVisible(
        String(orgId),
        String(userId),
        {
          owned: true,
          shared: true,
          search: queryParts.search,
          startDate: queryParts.startDate,
          endDate: queryParts.endDate,
          isShared: queryParts.isSharedFilter,
          sort: {
            field: sortField as 'createdAt' | 'lastActivityAt' | 'title',
            direction: sortOptions[sortField as keyof typeof sortOptions] as
              1 | -1,
          },
          skip,
          limit,
        },
      );

      const response = {
        searchHistory: await redactSearchesFor(
          appConfig,
          viewerOf(userId, orgId),
          plain,
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

/** What the request's query string narrows a search read or change to (the same filters the list takes). */
const searchNarrowing = (
  q: ReturnType<typeof parseSessionQueryParams>,
): SearchQuery => ({
  search: q.search,
  startDate: q.startDate,
  endDate: q.endDate,
  isShared: q.isSharedFilter,
});

// A user a search was shared with may read it, but only its owner may change or delete it.
const ownedSearchNarrowing = (req: AuthenticatedUserRequest): SearchQuery => ({
  ...searchNarrowing(parseSessionQueryParams(req)),
  owned: true,
  shared: false,
});

export const getSearchById =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const searchId = req.params.searchId;

    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const narrowing = searchNarrowing(parseSessionQueryParams(req));

      logger.debug('Attempting to get search', {
        requestId,
        searchId,
        timestamp: new Date().toISOString(),
      });
      // The by-id read has always answered with a list (empty when nothing matched).
      const found = await searches().findVisible(
        String(orgId),
        String(userId),
        searchId as string,
        { populateCitations: true, query: narrowing },
      );
      const search = found ? [found] : [];
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
    const narrowing = ownedSearchNarrowing(req);

    logger.debug('Attempting to delete search', {
      requestId,
      searchId,
      timestamp: new Date().toISOString(),
    });

    const search = await searches().delete(
      String(orgId),
      String(userId),
      searchId as string,
      narrowing,
    );
    if (!search) {
      throw new NotFoundError('Search Id not found');
    }

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
      const narrowing = ownedSearchNarrowing(req);

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

      const search = await searches().findVisible(
        String(orgId),
        String(userId),
        searchId as string,
        { query: narrowing },
      );
      if (!search) {
        throw new NotFoundError('Search Id not found');
      }

      const shareLink = `${appConfig.frontendUrl}/api/v1/search/${search._id}`;

      // Validate all user IDs
      const validUsers = await Promise.all(
        userIds.map(async (id: string) => {
          if (!isValidId(id)) {
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

      // Update the search
      const updatedSearch = await searches().setSharing(searchId as string, {
        isShared: true,
        shareLink,
        sharedWith: mergedSharedWith,
      });

      if (!updatedSearch) {
        throw new InternalServerError(
          'Failed to update search sharing settings',
        );
      }

      const shared = updatedSearch as unknown as Record<string, unknown> & {
        toJSON?: () => Record<string, unknown>;
      };
      const sharedView = shared.toJSON?.() ?? shared;
      res.status(HTTP_STATUS.OK).json({
        ...sharedView,
        records: apiStoredRecords(sharedView.records),
      });
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
        throw new BadRequestError(
          'userIds is required and must be a non-empty array',
        );
      }

      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const narrowing = ownedSearchNarrowing(req);

      logger.debug('Attempting to unshare search', {
        requestId,
        searchId,
        userId,
        timestamp: new Date().toISOString(),
      });

      const search = await searches().findVisible(
        String(orgId),
        String(userId),
        searchId as string,
        { query: narrowing },
      );
      if (!search) {
        throw new NotFoundError('Search Id not found or unauthorized');
      }

      // Validate all user IDs
      await Promise.all(
        userIds.map(async (id: string) => {
          if (!isValidId(id)) {
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
        (share) => !userIds.includes(idOf(share.userId)),
      );

      // With nobody left the search stops being shared and loses its link;
      // the response reports the stored state after the change.
      const updatedSearch = await searches().unshare(
        searchId as string,
        updatedSharedWith,
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
    const narrowing = ownedSearchNarrowing(req);

    logger.debug('Attempting to archive search', {
      requestId,
      message: 'Attempting to archive search',
      searchId,
      userId,
      timestamp: new Date().toISOString(),
    });

    const search = await searches().findVisible(
      String(orgId),
      String(userId),
      searchId as string,
      { query: narrowing },
    );
    if (!search) {
      throw new NotFoundError('Search Id not found or no archive permission');
    }

    if (search.isArchived) {
      throw new BadRequestError('Search already archived');
    }

    const updatedSearch = await searches().archive(
      searchId as string,
      String(userId),
    );

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
    const narrowing = ownedSearchNarrowing(req);

    logger.debug('Attempting to unarchive conversation', {
      requestId,
      message: 'Attempting to unarchive search',
      searchId,
      userId,
      timestamp: new Date().toISOString(),
    });
    const search = await searches().findVisible(
      String(orgId),
      String(userId),
      searchId as string,
      { archived: true, query: narrowing },
    );
    if (!search) {
      throw new NotFoundError('Search Id not found or no unarchive permission');
    }

    if (!search.isArchived) {
      throw new BadRequestError('Search is not archived');
    }

    const updatedSearch = await searches().unarchive(
      String(orgId),
      String(userId),
      searchId as string,
      narrowing,
    );

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
    const deleteQuery = parseSessionQueryParams(req);

    logger.debug('Attempting to delete search history', {
      requestId,
      timestamp: new Date().toISOString(),
    });

    const deleted = await searches().deleteVisible(
      String(orgId),
      String(userId),
      {
        owned: true,
        shared: false,
        search: deleteQuery.search,
        startDate: deleteQuery.startDate,
        endDate: deleteQuery.endDate,
        isShared: deleteQuery.isSharedFilter,
      },
    );

    if (!deleted) {
      throw new NotFoundError('Search history not found');
    }

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
        const normalizedAgent = {
          ...(responsePayload.agent as Record<string, unknown>),
        };
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
      // A check that failed is not "no agents": the UI would offer the delete as safe.
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(
          aiResponse,
          'check which agents use this web search provider',
        );
      }
      res.status(HTTP_STATUS.OK).json(aiResponse.data);
    } catch (error: any) {
      logger.error('Error checking web search provider usage', {
        requestId,
        message: 'Error checking web search provider usage',
        error: error.message,
      });
      const backendError = handleBackendError(
        error,
        'check which agents use this web search provider',
      );
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
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(
          aiResponse,
          'check which agents use this model',
        );
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
      const backendError = handleBackendError(
        error,
        'check which agents use this model',
      );
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
      const { page, limit, search, sort_by, sort_order } = req.query as Record<
        string,
        string | undefined
      >;
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
        res.status(HTTP_STATUS.OK).json({
          success: true,
          agents: [],
          pagination: {
            currentPage: Number(page ?? 1),
            limit: Number(limit ?? 20),
            totalItems: 0,
            totalPages: 0,
            hasNext: false,
            hasPrev: false,
          },
        });
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

export type AgentVersionAction = 'list' | 'get' | 'publish' | 'rollback';

const AGENT_VERSION_ACTIONS: Record<
  AgentVersionAction,
  { method: HttpMethod; path: (version: string) => string; label: string }
> = {
  list: {
    method: HttpMethod.GET,
    path: () => '/versions',
    label: 'List Agent Versions',
  },
  get: {
    method: HttpMethod.GET,
    path: (v) => `/versions/${encodeURIComponent(v)}`,
    label: 'Get Agent Version',
  },
  publish: {
    method: HttpMethod.POST,
    path: () => '/versions',
    label: 'Publish Agent',
  },
  rollback: {
    method: HttpMethod.POST,
    path: (v) => `/versions/${encodeURIComponent(v)}/rollback`,
    label: 'Roll Back Agent',
  },
};

/**
 * An agent's published versions (OWD-14), answered by the agent authority with the caller's own
 * credential: it decides who may read, publish or roll back. A publish that changed nothing answers 200, a
 * new version 201.
 */
export const agentVersions =
  (appConfig: AppConfig, action: AgentVersionAction) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const route = AGENT_VERSION_ACTIONS[action];
    try {
      if (!getCurrentOrgId() || !req.user?.userId) {
        throw new BadRequestError('Organization and user are required');
      }
      const agentKey = encodeURIComponent(req.params.agentKey as string);
      const aiResponse = await new AIServiceCommand({
        uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}${route.path(req.params.version ?? '')}`,
        method: route.method,
        ...(route.method === HttpMethod.POST
          ? { body: (req.body as unknown) ?? {} }
          : {}),
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      }).execute();
      if (aiResponse.statusCode !== 200 && aiResponse.statusCode !== 201) {
        throw handleBackendError(aiResponse, route.label);
      }
      res.status(aiResponse.statusCode).json(aiResponse.data);
    } catch (error: unknown) {
      logger.error(`Error: ${route.label}`, {
        requestId: req.context?.requestId,
        error: error instanceof Error ? error.message : undefined,
      });
      next(handleBackendError(error, route.label));
    }
  };

export const deleteAgent =
  (
    appConfig: AppConfig,
    agentData: AgentDataEraser | null = null,
    retries: AgentErasureQueue | null = null,
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId as string | undefined;
      const userId = req.user?.userId as string | undefined;
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
      // The intent goes first (H5): a crash anywhere after it leaves an entry the retention sweeper confirms with
      // the query service, so the memories of a deleted agent are erased whatever happens to this request.
      if (agentKey && retries) {
        try {
          await retries.intend(orgId, agentKey, AGENT_DELETE_CONFIRM_AFTER_MS);
        } catch (error) {
          logger.warn('memory.agent_erase_unrecorded', {
            requestId,
            orgId,
            agentKey,
            errorType: error instanceof Error ? error.name : typeof error,
          });
          throw new ServiceUnavailableError(
            'The agent could not be deleted right now. Please try again.',
            { reasonCode: 'agent_erasure_unrecorded' },
          );
        }
      }
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (!aiResponse) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse, 'Delete Agent');
      }
      const agent = aiResponse.data;
      // After the delete: only the query service decides who may delete an agent (C-14 AGENT_DATA_STORES).
      try {
        const erased = agentKey
          ? await agentData?.eraseAgent(orgId, agentKey)
          : undefined;
        if (erased !== undefined) {
          logger.info('memory.agent_erased', {
            requestId,
            orgId,
            agentKey,
            erased,
          });
          if (agentKey) await retries?.settle(orgId, agentKey);
        }
      } catch (error) {
        // The agent is gone, so its memories are never recalled again; the retention sweeper retries the erasure.
        const errorType = error instanceof Error ? error.name : typeof error;
        let choice = retries ? 'confirm_by_lookup' : 'left_for_admin';
        if (agentKey && retries) {
          try {
            await retries.enqueue(orgId, agentKey, errorType);
            choice = 'retry_queued';
          } catch {
            // The intent written before the delete stays: the sweeper confirms the delete, then erases.
          }
        }
        logger.warn('memory.agent_erase_failed', {
          requestId,
          orgId,
          agentKey,
          errorType,
          choice,
        });
      }
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
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next?: NextFunction,
  ): Promise<void> => {
    if (await refuseIfDraining(res)) return;
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const run = new ChatStreamRun(req, res);
    let userId: string | undefined;
    let orgId: string | undefined;
    if ('user' in req) {
      const auth_req = req;
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

    // The stream outlives the request, so its listeners write outside any transaction.
    const session: Tx | null = null;
    let savedConversation: ChatSessionDto | null = null;

    const modelInfo = extractModelInfo(req.body);
    const protocol = resolveProtocol(
      req.body as Record<string, unknown>,
      req.query,
    );

    if (!req.body.query) {
      throw new BadRequestError('Query is required');
    }

    let projectLink: ResolvedProjectLink;
    try {
      // Before the stream opens; see streamChat.
      projectLink = await resolveProjectLink(
        orgId,
        userId,
        req.body as Record<string, unknown>,
        projectCallerFromRequest(req, appConfig),
      );
    } catch (error) {
      if (!next) throw error;
      next(error);
      return;
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
        expandedChatCommand(res),
      );

      const userConversationData: NewSession = {
        orgId: orgId,
        userId: userId,
        initiator: userId,
        title: conversationTitleFromQuery(String(req.body.query)),
        lastActivityAt: Date.now(),
        status: CONVERSATION_STATUS.INPROGRESS,
        agentKey,
        modelInfo,
        sessionType: 'agent',
        // Set explicitly: the legacy agentConversations schema defaulted this,
        // the unified chatSessions schema can't (it also backs plain chats).
        conversationSource: 'agent_chat',
        ...scheduledSessionFields(req),
        ...(projectLink.projectId
          ? {
              projectId: projectLink.projectId,
              projectVisibility: projectLink.projectVisibility,
            }
          : {}),
      };

      // One transaction on a replica set, plain writes otherwise
      savedConversation = await unitOfWork.run((tx) =>
        convRepo().openSession(userConversationData, userQueryMessage, tx),
      );

      if (!savedConversation) {
        throw new InternalServerError('Failed to create conversation');
      }

      const newAgentConversationId = savedConversation._id || '';
      run.bind({ conversationId: savedConversation._id, agentKey });

      logger.debug('Initial conversation created');

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
                ...(projectLink.projectId
                  ? { projectId: projectLink.projectId }
                  : {}),
              },
            })
          : `event: connected\ndata: ${JSON.stringify({
              message: 'SSE connection established',
              conversationId: newAgentConversationId,
              title: savedConversation.title || undefined,
              ...(projectLink.projectId
                ? { projectId: projectLink.projectId }
                : {}),
            })}\n\n`,
      );
      (res as any).flush?.();

      // A new conversation has no stored history; history is never taken from the client (CE-19).
      const aiRequest = buildAiChatRequest(
        { kind: 'agent', agentKey: agentKey as string },
        req.body,
        {
          conversationId: newAgentConversationId,
          previousConversations: [],
          isNewConversation: true,
          project: projectLink.project,
        },
      );
      const aiPayload = withStreamProtocol(aiRequest.payload, protocol);
      if (projectLink.projectId) {
        void ProjectService.touchActivity(projectLink.projectId);
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}${aiRequest.path}/stream`,
        method: HttpMethod.POST,
        headers: {
          ...(await resolvedProxyHeaders(req, {
            agentKey: agentKey,
            conversationId: newAgentConversationId,
          })),
          'Content-Type': 'application/json',
        },
        body: aiPayload,
        scheduledTurnHeaders: scheduledTurnHeaders(req),
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

      const upstreamAbort = attachUpstreamAbort(
        res,
        () => {
          if (streamSettled || !savedConversation) return;
          if (completeData || upstreamAiErrorEventForwarded) {
            runGuarded(finalizeRun);
            return;
          }
          streamSettled = true;
          void savePartialConversation(
            savedConversation,
            contentAccumulator.getText(),
            null,
            { parts: contentAccumulator.getParts() },
          ).then(
            () => {
              run.end({ kind: 'abandoned' });
            },
            (error: unknown) => {
              run.end({ kind: 'failed', error });
            },
          );
        },
        scheduledTurnDetachMs(req) ?? DETACHED_RUN_MAX_MS,
        { detached: scheduledTurnDetachMs(req) !== null },
      );
      // Opening failed: no stream will ever close the detached cap, so it is ended here (a scheduled turn's).
      const stream = await run
        .open(() =>
          startAIStream(
            aiCommandOptions,
            'Agent Chat Stream',
            upstreamAbort.signal,
          ),
        )
        .catch((error: unknown) => {
          upstreamAbort.dispose();
          throw error;
        });

      if (!stream) {
        upstreamAbort.dispose();
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, savedConversation._id);

      // Forward the AI stream; the hooks save this route's answer, error and question.
      stream.on('data', (chunk: Buffer) => {
        run.observe(chunk);
        buffer = readAIStreamChunk(chunk, buffer, res, contentAccumulator, {
          onAnswer: (result) => {
            completeData = result;
            run.sawResult(result);
          },
          onRunError: (error) => {
            upstreamAiErrorEventForwarded = true;
            run.sawRunError(error.data.code);
            if (savedConversation) {
              failFromRunError(
                markAgentConversationFailed,
                AGENT_MARK_FAILED_LOG,
                savedConversation,
                error,
                session,
              );
            }
          },
          onAskUserQuestion: (payload) => {
            if (savedConversation) {
              saveAskUserQuestion(savedConversation, payload, session);
            }
          },
        });
      });

      const finalizeRun = async (): Promise<void> => {
        streamSettled = true;
        try {
          // Save the complete conversation data to database
          if (completeData && savedConversation) {
            const conversation = await saveCompleteAgentConversation(
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
                ...searchDegradedMeta(completeData),
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
          run.end({ kind: 'answered' });
        } catch (dbError: any) {
          run.end({ kind: 'failed', error: dbError });

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
                { error: markErr },
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
        runGuarded(finalizeRun);
      });

      stream.on('error', async (error: Error) => {
        if (upstreamAbort.isAborted()) return;
        streamSettled = true;
        run.end({ kind: 'failed', error, fromStream: true });
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
            error: dbError,
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
      run.end({ kind: 'failed', error });

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
          error: dbError,
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
      await streamAgentConversation(appConfig)(req, res);
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
  ) => {
    if (await refuseIfDraining(res)) return;
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const run = new ChatStreamRun(req, res);
    let userId: string | undefined;
    let orgId: string | undefined;
    if ('user' in req) {
      const auth_req = req;
      userId = auth_req.user?.userId;
      orgId = auth_req.user?.orgId;
    } else {
      const auth_req = req as AuthenticatedServiceRequest;
      userId = auth_req.tokenPayload?.userId;
      orgId = auth_req.tokenPayload?.orgId;
    }

    if (!userId) {
      refuseBeforeStream(next, 'User ID is required');
      return;
    }
    if (!orgId) {
      refuseBeforeStream(next, 'Organization ID is required');
      return;
    }
    const { conversationId, agentKey } = req.params;

    // The stream outlives the request, so its listeners write outside any transaction.
    const session: Tx | null = null;
    let existingConversation: ChatSessionDto | null = null;
    let reservation: RunReservation | null = null;

    const modelInfo = extractModelInfo(req.body);
    const protocol = resolveProtocol(
      req.body as Record<string, unknown>,
      req.query,
    );

    if (!req.body.query) {
      refuseBeforeStream(next, 'Query is required');
      return;
    }
    if (
      !(await ownsConversationBeforeStream(
        agentScope(agentKey),
        conversationId as string,
        actorOf(orgId, userId),
        next,
      ))
    ) {
      return;
    }

    // Helper function that contains the common conversation operations
    async function performAddMessageStream(tx?: Tx): Promise<void> {
      // Get existing conversation
      const conversation = await convRepo().findForTurn(
        agentScope(agentKey),
        conversationId as string,
        actorOf(orgId, userId),
      );

      if (!conversation) {
        throw new NotFoundError('Conversation not found');
      }

      // First, add the user message to the existing conversation
      await appendMessages(
        conversation._id,
        conversation.orgId,
        [
          buildUserQueryMessage(
            req.body.query,
            req.body.appliedFilters,
            req.body.chatMode,
            req.body.attachments,
            expandedChatCommand(res),
          ),
        ],
        tx,
      );

      // Mark the run in progress (clearing a previous error), update the model
      // and mode information if provided, and save
      const savedConversation = await convRepo().recordRunState(
        conversation._id,
        {
          status: CONVERSATION_STATUS.INPROGRESS,
          failReason: null,
          modelInfo: req.body,
          lastActivityAt: Date.now(),
        },
        tx,
      );

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

      reservation = await reserveRun(
        agentScope(agentKey),
        conversationId as string,
        actorOf(orgId, userId),
      );

      // Get existing conversation and add user message
      await unitOfWork.run((tx) => performAddMessageStream(tx));

      if (!existingConversation) {
        throw new NotFoundError('Conversation not found');
      }
      const confirmedConversation = existingConversation as ChatSessionDto;
      run.bind({ conversationId: confirmedConversation._id, agentKey });

      // Format previous conversations for context (excluding the user message we just added)
      const accessMemo = newReadable();
      // The history's access check runs while the project loads: both come before the first token.
      const [allMessagesSoFar, followUpProject] = await Promise.all([
        historyForModel(
          appConfig,
          viewerOf(userId, orgId),
          (await getMessages(
            confirmedConversation._id,
            { orgId: confirmedConversation.orgId, withLabels: true },
            session,
          )) as IMessage[],
          accessMemo,
        ),
        loadProjectForSession(
          confirmedConversation.orgId,
          confirmedConversation.userId,
          confirmedConversation.projectId,
          {
            caller: projectCallerFromRequest(req, appConfig),
            conversationId: confirmedConversation._id,
          },
        ),
      ]);
      const previousConversations = formatPreviousConversations(
        allMessagesSoFar.slice(0, -1),
      );
      const previousLabel = historyLabel(allMessagesSoFar.slice(0, -1));
      const aiRequest = buildAiChatRequest(
        { kind: 'agent', agentKey: agentKey as string },
        req.body as Record<string, unknown>,
        {
          conversationId,
          previousConversations,
          historyLabel: previousLabel,
          isNewConversation: false,
          project: followUpProject,
        },
      );
      const aiPayload = withStreamProtocol(aiRequest.payload, protocol);
      if (confirmedConversation.projectId) {
        void ProjectService.touchActivity(confirmedConversation.projectId);
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}${aiRequest.path}/stream`,
        method: HttpMethod.POST,
        headers: {
          ...(await resolvedProxyHeaders(req, {
            agentKey: agentKey,
            conversationId: conversationId as string,
          })),
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

      const upstreamAbort = attachUpstreamAbort(res, () => {
        if (streamSettled || !existingConversation) return;
        if (completeData || upstreamAiErrorEventForwarded) {
          runGuarded(finalizeRun);
          return;
        }
        streamSettled = true;
        void savePartialConversation(
          existingConversation,
          contentAccumulator.getText(),
          null,
          {
            parts: contentAccumulator.getParts(),
          },
        ).then(
          () => {
            run.end({ kind: 'abandoned' });
          },
          (error: unknown) => {
            run.end({ kind: 'failed', error });
          },
        );
      });
      const stream = await run.open(() =>
        startAIStream(
          aiCommandOptions,
          'Add Message Agent Stream',
          upstreamAbort.signal,
        ),
      );

      if (!stream) {
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, confirmedConversation._id, reservation?.runId);

      // Forward the AI stream; the hooks save this route's answer, error and question.
      stream.on('data', (chunk: Buffer) => {
        run.observe(chunk);
        buffer = readAIStreamChunk(chunk, buffer, res, contentAccumulator, {
          onAnswer: (result) => {
            completeData = result;
            run.sawResult(result);
          },
          onRunError: (error) => {
            upstreamAiErrorEventForwarded = true;
            run.sawRunError(error.data.code);
            if (existingConversation) {
              failFromRunError(
                markAgentConversationFailed,
                AGENT_MARK_FAILED_LOG,
                existingConversation,
                error,
                session,
              );
            }
          },
          onAskUserQuestion: (payload) => {
            if (existingConversation) {
              saveAskUserQuestion(existingConversation, payload, session);
            }
          },
        });
      });

      const finalizeRun = async (): Promise<void> => {
        streamSettled = true;
        try {
          // Save the AI response to the existing conversation
          if (completeData && existingConversation) {
            try {
              // Save citations, the AI message and the conversation's new state
              const saved = await persistCompletedAnswer(
                existingConversation,
                completeData,
                orgId,
                session,
                {
                  messageModelInfo: modelInfo,
                  failureLabel:
                    'Failed to update conversation with AI response',
                },
              );
              const savedCitations = saved.citations;

              // Return the updated conversation in the same format as addMessage
              const responseConversation = await redactConversationFor(
                appConfig,
                viewerOf(userId, orgId),
                await attachPopulatedCitations(
                  saved.conversation,
                  [saved.message],
                  savedCitations,
                  session,
                ),
                { known: accessMemo, freshIds: [saved.message._id] },
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
                  ...searchDegradedMeta(completeData),
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
          run.end({ kind: 'answered' });
        } catch (dbError: any) {
          run.end({ kind: 'failed', error: dbError });

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
        runGuarded(finalizeRun);
      });

      stream.on('error', async (error: Error) => {
        if (upstreamAbort.isAborted()) return;
        streamSettled = true;
        run.end({ kind: 'failed', error, fromStream: true });
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
            error: dbError,
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
      run.end({ kind: 'failed', error });

      try {
        if (existingConversation) {
          await markAgentConversationFailed(
            existingConversation,
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
          error: dbError,
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
      await addMessageStreamToAgentConversation(appConfig)(req, res);
    } catch (error) {
      next(error);
    }
  };

export const regenerateAgentAnswers =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response) => {
    if (await refuseIfDraining(res)) return;
    await regenerateAnswersInternal(appConfig, req, res, {
      isAgentSession: true,
      scopeOf: (agentKey) => agentScope(agentKey),
      // Owner only: an agent chat shared with someone is read-only for them.
      ownerOnly: true,
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
      const conversation = await convRepo().findForTurn(
        agentScope(agentKey),
        conversationId as string,
        actorOf(orgId, userId),
      );
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
        throw handleBackendError(
          aiResponse,
          'Cancel Agent Conversation Stream',
        );
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
      const backendError = handleBackendError(
        error,
        'Cancel Agent Conversation Stream',
      );
      next(backendError);
    }
  };

export const getAllAgentConversations = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  // Passed by the router so project-shared chats reach team members; absent in direct calls.
  appConfig?: AppConfig,
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
    let contentMatchIds: string[] | undefined;
    if (req.query.search) {
      const escapedSearch = validateAndEscapeSearch(req.query.search, {
        formatSpecifiers: true,
      });
      contentMatchIds = await findSessionIdsMatchingContent(
        orgId,
        escapedSearch,
      );
    }
    const accessibleProjectIds = await accessibleProjectIdsFor(
      String(orgId),
      String(userId),
      appConfig ? projectCallerFromRequest(req, appConfig) : undefined,
    );
    const sortOptions = buildSortOptions(req);
    const query = buildSessionQuery(
      req,
      {
        orgId: String(orgId),
        userId: String(userId),
        scope: agentScope(agentKey),
        id: conversationId,
        contentMatchIds,
        accessibleProjectIds: accessibleProjectIds.map(String),
        archived: 'notTrue',
        sort: sortOptions as Record<string, 1 | -1>,
        skip,
        limit,
      },
      { formatSpecifiers: true },
    );
    const filter = dateRangeFilterOf(query);

    const [
      { rows: conversations, total: totalCount },
      sharedWithMeConversations,
    ] = await Promise.all([
      convRepo().listSessions(query),
      convRepo().listAgentSharedWithMe({
        orgId: String(orgId),
        userId: String(userId),
        agentKey: agentKey as string,
        status: req.query.status as string | undefined,
        sort: sortOptions as Record<string, 1 | -1>,
        skip,
        limit,
      }),
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

export const getAgentConversationById =
  (appConfig: AppConfig, checks: ViewerChecks = {}): ConversationHandler =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
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
      let contentMatchIds: string[] | undefined;
      if (req.query.search) {
        const escapedSearch = validateAndEscapeSearch(req.query.search, {
          formatSpecifiers: true,
        });
        contentMatchIds = await findSessionIdsMatchingContent(
          orgId,
          escapedSearch,
        );
      }
      const accessibleProjectIds = await accessibleProjectIdsFor(
        String(orgId),
        String(userId),
        projectCallerFromRequest(req, appConfig),
      );
      const sessionQuery = buildSessionQuery(
        req,
        {
          orgId: String(orgId),
          userId: String(userId),
          scope: agentScope(agentKey),
          id: conversationId,
          contentMatchIds,
          accessibleProjectIds: accessibleProjectIds.map(String),
          sharedAccess: 'read',
          archived: 'any',
          sort: {},
          skip: 0,
          limit: 1,
        },
        { formatSpecifiers: true },
      );

      const messageFilter = buildMessageFilter(req);

      // Get sort options for messages
      const messageSortOptions = buildMessageSortOptions(
        sortBy as string,
        sortOrder as string,
      );

      const session = await convRepo().findVisibleSummary(sessionQuery);

      if (!session) {
        throw new NotFoundError('Conversation not found');
      }

      // A recipient may be on the conversation only through a share, so the
      // artifacts the owner created since sharing are granted on first view.
      const initiatorId = session.initiator;
      const isRecipient = initiatorId !== String(userId);
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

      const sessionId = session._id;
      const harness = await harnessSessionView(sessionId, session.orgId);

      const totalMessages = await convRepo().countMessages(
        sessionId,
        session.orgId,
      );

      const { skip, limit: effectiveLimit } = olderMessagesWindow(
        totalMessages,
        page,
        limit,
      );

      // A fork written before forks detached their copies names the original's runs (FORKFIX).
      const messages = await withForeignRunsDetached(
        { _id: sessionId, metadata: harness.metadata },
        String(orgId),
        await getMessages(sessionId, {
          orgId: session.orgId,
          skip,
          limit: effectiveLimit,
          populateCitations: true,
        }),
      );

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

      // Build conversation response using existing helper
      const viewerRead = {
        orgId: String(orgId),
        sessionId,
        checkFailed: false,
      };
      const builtResponse = await withViewerChecks(
        buildConversationResponse(
          {
            ...conversationWithMessages,
            harnessVersion: harness.harnessVersion,
          },
          userId,
          {
            page,
            limit,
            skip,
            totalMessages,
            hasNextPage: skip > 0,
            hasPrevPage: skip + effectiveLimit < totalMessages,
          },
          (conversationWithMessages as { messages?: IMessage[] }).messages ||
            [],
        ),
        req,
        checks,
        viewerRead,
      );
      // The recipient list is the owner's business: a viewer is told who shared
      // the chat, never who else can read it.
      const conversationResponse = await attachSharedByIfRecipient(
        builtResponse.access.isOwner
          ? builtResponse
          : { ...builtResponse, sharedWith: undefined },
        orgId as string,
      );
      // Hidden in stored order, then sorted as the client asked: a turn's tool rows follow their
      // answer, and `sortBy` must not separate them first.
      conversationResponse.messages = sortMessages(
        (await redactPageFor(
          appConfig,
          viewerOf(userId, orgId),
          session,
          conversationResponse.messages,
          skip,
          totalMessages,
          (m) => messageActor(m, initiatorId),
          checks.citations?.checksRecords() === true,
          viewerRead.checkFailed,
        )) as never,
        messageSortOptions as { field: keyof IMessage },
      ) as unknown as typeof conversationResponse.messages;

      // Build filters metadata using existing helper
      const filtersMetadata = buildFiltersMetadata(
        messageFilter,
        query,
        messageSortOptions,
      );

      const [forkedFrom, forks] = await Promise.all([
        resolveForkOrigin(session, String(userId)),
        resolveForks(session, String(userId)),
      ]);

      // Prepare response using existing format
      const response = {
        conversation: {
          ...conversationResponse,
          ...(forkedFrom && { forkedFrom }),
          ...(forks.length > 0 && { forks }),
          ...scheduleOriginField(session.metadata),
        },
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

const deleteAgentConversationOf = async (
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  erasures?: SessionErasureQueue<StoreSession>,
  stopper?: SessionRunStopperPort,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  const { conversationId, agentKey } = req.params;
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    // Missing, foreign and read-shared ids stay an idempotent 200 that deletes nothing.
    const conversation = await deleteAgentConversation(
      conversationId as string,
      agentKey as string,
      userId as string,
      orgId as string,
      erasures === undefined
        ? undefined
        : async (tx) => {
            await erasures.enqueue(
              {
                orgId: String(orgId),
                sessionId: String(conversationId),
                reason: 'user_delete',
                by: principalRefFor(String(userId)),
              },
              tx,
            );
          },
      () => deleteConversationArtifacts(appConfig, String(conversationId), req),
    );
    if (conversation) {
      await stopDeletedChatRun(
        stopper,
        String(orgId),
        String(conversationId),
        String(userId),
        requestId,
      );
    }

    res.status(200).json({
      message: 'Conversation deleted successfully',
      conversation:
        conversation && withoutErasureMarker(withoutErrorStacks(conversation)),
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

/** DELETE an agent conversation; as `deleteConversationByIdWith`, erasure is enqueued with the delete. */
export const deleteAgentConversationByIdWith =
  (
    appConfig: AppConfig,
    erasures?: SessionErasureQueue<StoreSession>,
    stopper?: SessionRunStopperPort,
  ) =>
  (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> =>
    deleteAgentConversationOf(appConfig, req, res, next, erasures, stopper);

export const deleteAgentConversationById = (
  appConfig: AppConfig,
): ReturnType<typeof deleteAgentConversationByIdWith> =>
  deleteAgentConversationByIdWith(appConfig);

export const archiveAgentConversation = async (
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

    logger.debug('Attempting to archive agent conversation', {
      requestId,
      message: 'Attempting to archive agent conversation',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performArchive(tx?: Tx) {
      const conversation = await convRepo().findArchivable(
        agentScope(agentKey),
        conversationId as string,
        actorOf(orgId, userId),
        tx,
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      if (conversation.isArchived) {
        throw new BadRequestError('Agent conversation already archived');
      }

      const updated = await convRepo().setArchived(
        agentScope(undefined),
        conversationId as string,
        { by: String(userId) },
        tx,
      );

      if (!updated) {
        throw new InternalServerError('Failed to archive agent conversation');
      }
      return updated;
    }

    const updated = await unitOfWork.run((tx) => performArchive(tx));

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
  }
};

export const unarchiveAgentConversation = async (
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

    logger.debug('Attempting to unarchive agent conversation', {
      requestId,
      message: 'Attempting to unarchive agent conversation',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performUnarchive(tx?: Tx) {
      const conversation = await convRepo().findArchivable(
        agentScope(agentKey),
        conversationId as string,
        actorOf(orgId, userId),
        tx,
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      if (!conversation.isArchived) {
        throw new BadRequestError('Agent conversation is not archived');
      }

      const updated = await convRepo().setArchived(
        agentScope(undefined),
        conversationId as string,
        null,
        tx,
      );

      if (!updated) {
        throw new InternalServerError('Failed to unarchive agent conversation');
      }
      return updated;
    }

    const updated = await unitOfWork.run((tx) => performUnarchive(tx));

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
  }
};

export const listAllArchivesAgentConversation =
  () =>
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
      const { agentKey } = req.params;

      logger.debug('Fetching all archived agent conversations', {
        requestId,
        userId,
        agentKey,
      });

      const { skip, limit, page } = getPaginationParams(req);
      let contentMatchIds: string[] | undefined;
      if (req.query.search) {
        const escapedSearch = validateAndEscapeSearch(req.query.search, {
          formatSpecifiers: true,
        });
        contentMatchIds = await findSessionIdsMatchingContent(
          orgId,
          escapedSearch,
        );
      }
      const query = buildSessionQuery(
        req,
        {
          orgId: String(orgId),
          userId: String(userId),
          scope: agentScope(agentKey),
          contentMatchIds,
          archived: 'only',
          sort: buildSortOptions(req) as Record<string, 1 | -1>,
          skip,
          limit,
        },
        { formatSpecifiers: true },
      );
      const filter = dateRangeFilterOf(query);

      const { rows: conversations, total: totalCount } =
        await convRepo().listSessions(query);

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

    const found = await convRepo().findOwned(
      agentScope(agentKey),
      conversationId as string,
      actorOf(orgId, userId),
    );

    if (!found) {
      throw new NotFoundError('Agent conversation not found');
    }

    const title = req.body.title?.trim();
    if (!title) {
      throw new BadRequestError('Title is required and cannot be empty');
    }

    const conversation = await convRepo().setTitle(found._id, title);
    if (!conversation) {
      throw new NotFoundError('Agent conversation not found');
    }

    logger.debug('Agent conversation title updated successfully', {
      requestId,
      message: 'Agent conversation title updated successfully',
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      conversation: {
        ...withoutErrorStacks(conversation),
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

export const updateAgentFeedbackWith =
  (runs?: FeedbackRuns) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    try {
      const { agentKey, conversationId, messageId } = req.params;
      const userId: unknown = req.user?.userId;
      const orgId: unknown = req.user?.orgId;

      logger.debug('Attempting to update agent conversation feedback', {
        requestId,
        message: 'Attempting to update agent conversation feedback',
        conversationId,
        agentKey,
        userId,
        timestamp: new Date().toISOString(),
      });

      const { updatedConversation, feedbackEntry } = await unitOfWork.run(
        (tx) =>
          performFeedbackUpdate({
            scope: agentScope(agentKey),
            conversationId: conversationId!,
            orgId: String(orgId),
            messageId: messageId!,
            userId,
            body: req.body as FeedbackBody,
            userAgent: req.headers['user-agent'],
            tx,
            runs,
          }),
      );

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
    }
  };

export const updateAgentFeedback = updateAgentFeedbackWith();

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

      const { groups: rawGroups, totalAgents: totalAgentCount } =
        await convRepo().listArchivedAgentGroups({
          orgId: String(orgId),
          userId: String(userId),
          deletedAgentKeys,
          agentSkip,
          agentLimit,
          chatsPerAgent: AGENT_ARCHIVES_INITIAL_CHAT_LIMIT,
        });

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

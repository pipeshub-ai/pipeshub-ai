import { historyLabel } from '../../../libs/repositories/agent-harness/history-source/mongo';
import { withServedAssistantMode } from '../validators/es_validators';
import {
  buildMessageSortOptions,
  deleteAgentConversation,
  replaceMessageWithError,
  initializeSSEResponse,
  sendSSEErrorEvent,
  sendSSECompleteEvent,
  searchDegradedMeta,
  handleRegenerationStreamData,
  handleRegenerationSuccess,
  updateUserQueryContent,
  editedTitles,
  isOwnersTurn,
  retireMessagesById,
  hasAskUserQuestionTool,
  isClassifiedFailureAnswer,
  staleAskUserQuestionToolCallIds,
  handleRegenerationError,
  reloadForErrorFrame,
  streamFrameOf,
} from './../utils/utils';
import {
  assertRasterImage,
  RasterImageFormat,
  sharp,
} from '../../../libs/utils/image.utils';
import { Response, NextFunction } from 'express';
import { refuseIfDraining } from '../../../libs/repositories/drain';
import {
  getConversationRepository,
  type ConversationRepository,
} from '../../../libs/repositories/conversation';
import { idOf, isValidId } from '../../../libs/repositories/ids';
import {
  getSemanticSearchRepository,
  type SearchQuery,
  type SemanticSearchRepository,
} from '../../../libs/repositories/semantic-search';
import mongoose, { ClientSession, QueryFilter, Types } from 'mongoose';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  GatewayTimeoutError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import { logLevelFor } from '../../../libs/utils/error.middleware.utils';
import { handleBackendError } from '../../../libs/errors/backend-error';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { upstreamFetch } from '../../../libs/http/upstream-call';
import { injectRequestHeaders } from '../../../libs/context/request-context';
import { mapAgentHandleError } from '../utils/agent-handle-error';
import {
  AIServiceResponse,
  AiSearchResponse,
  IAgentConversation,
  IAIResponse,
  IChatAttachmentRef,
  IChatSessionDocument,
  IMessage,
  IMessageCitation,
  IMessageDocument,
  IMessagePart,
} from '../types/conversation.interfaces';
import { IConversation } from '../types/conversation.interfaces';
import { ChatSession } from '../schema/chat.session.schema';
import {
  ChatSessionMessage,
  NOT_REMOVED,
} from '../schema/chat.session.message.schema';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  addComputedFields,
  attachSharedBy,
  attachSharedByIfRecipient,
  buildFiltersMetadata,
  buildConversationResponse,
  parseSessionQueryParams,
  dateRangeFilterOf,
  buildListQuery,
  buildMessageFilter,
  buildPaginationMetadata,
  buildSortOptions,
  extractModelInfo,
  getPaginationParams,
  olderMessagesWindow,
  sortMessages,
  getMessages,
  attachMessages,
  appendMessageFeedback,
  composeListFilter,
  findSessionIdsMatchingContent,
  listSearchClause,
  validateAndEscapeSearch,
  withoutSharedWith,
  withoutErrorStacks,
  withoutErasureMarker,
  savePartialConversation,
  keepRunAlive,
  reserveRun,
  type RunReservation,
  conversationForViewer,
} from '../utils/utils';
import {
  historyForModel,
  newReadable,
  messageActor,
  redactMessagesFor,
  redactSearchesFor,
  redactWithTurns,
  viewerOf,
  startsTurn,
  type Readable,
  type ViewerMessage,
} from '../utils/viewer-redaction';
import {
  idOf as idOfValue,
  includableRows,
  readerView,
  repliedRowOf,
  unlabelledAnswers,
  type Reader,
  type ReaderSession,
} from '../services/conversation/privacy/visibility';
import { apiRecord, apiStoredRecords } from '../utils/internal-record-fields';
import {
  IUserDirectory,
  MongoUserDirectory,
} from '../../user_management/services/user-directory.service';
import { AGUI_PROTOCOL, isAGUI, resolveProtocol } from '../utils/agui';
import {
  accessViewOf,
  conversationGrantOf,
  conversationContextOf,
  guardedProjectOf,
  kindFilterOf,
  listFilterOf,
  sharedListFilterOf,
  type ConversationRequestContext,
} from '../services/conversation/http/conversation-context';
import {
  ConversationExtensions,
  conversationExtensions,
  type ProjectPlacement,
} from '../services/conversation/conversation-extensions';
import { attachUpstreamAbort, runGuarded } from '../utils/stream-lifecycle';
import { ChatStreamRun, type StreamEnd } from '../utils/chat-stream-run';
import { IAMServiceCommand } from '../../../libs/commands/iam/iam.service.command';
import { AppConfig } from '../../tokens_manager/config/config';
import { SharedCitationService } from '../../agent-harness/application/shared-citations';
import { SharedArtifactService } from '../../agent-harness/application/shared-artifacts';
import { messageArtifactIds } from '../../agent-harness/projection/artifact-access';
import { messageSources } from '../../agent-harness/projection/citation-source';
import type { SessionParticipantRecorder } from '../../agent-harness/ports/session-participants';
import { SessionErasureQueue } from '../../agent-harness/ports/session-erasure';
import type { SessionRunStopperPort } from '../../agent-harness/application/session-run-stopper';
import type { RunFeedbackService } from '../../agent-harness/application/run-feedback';
import type { AgentDataEraser } from '../../agent-harness/ports/agent-erasures';
import type { AgentErasureQueue } from '../../agent-harness/ports/agent-erasures';
import { AGENT_DELETE_CONFIRM_AFTER_MS } from '../../agent-harness/domain/session-erasure';
import { principalRefFor } from '../../agent-harness/domain/run-spec';
import { resolvedProxyHeaders } from '../../agent-harness/transport/resolved-turn-header';
import { shapedForViewer } from '../../agent-harness/domain/token-redaction';
import type { StoreSession } from '../../../libs/repositories/agent-harness/durable-transaction';
import { MongoTx } from '../../../libs/repositories/mongo';
import type { Tx } from '../../../libs/repositories/uow';
import {
  harnessSessionView,
  markErasureRequested,
} from '../../../libs/repositories/agent-harness/session-fields';
import { deleteConversationArtifacts } from '../../artifacts/services/conversation-artifacts.service';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import {
  ConversationSharing,
  IConversationSharing,
} from '../services/conversation/sharing/conversation-sharing';
import Citation from '../schema/citation.schema';
import { EXCLUDE_AGENT, ONLY_AGENT } from '../constants/constants';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { validateNoFormatSpecifiers } from '../../../utils/xss-sanitization';
import {
  applyProjectScope,
  loadProjectForSession,
} from '../utils/project-context';
import { buildFilterLogMetadata } from '../utils/ai-request-log';
import { ownerProjectOptions } from '../utils/follow-up-turn';
import { schemaExtensions } from '../../../edition-schemas';
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
  parseChatMode,
} from '../utils/ai-chat-payload';
import { hydrateScopedRequestAsUser } from '../utils/scoped-request';
import { startAIStream } from '../utils/ai-stream';
import { isReplicaSet } from '../../../libs/utils/replica-set';
import { firstSendStream } from './first-send-stream.controller';
import { followUpStream } from './follow-up-stream.controller';
import { ConversationTurnDeps } from '../services/conversation/turn/turn-deps';
import {
  openTurnGate,
  reservedGate,
  outcomeForError,
  TurnGate,
} from '../services/conversation/turn/turn-gate';
import {
  outcomeForStatus,
  TurnOutcome,
} from '../services/conversation/turn/turn-lifecycle';
import { TurnRun } from '../services/conversation/turn/turn-run';
import type { TurnAnswerHooks } from '../services/conversation/turn/turn-extension';
import { LeaseLostError } from '../services/conversation/turn/run-lease';
import {
  gateTellingLive,
  RunWatchdog,
  SHUTDOWN_FRAME,
  STOPPED_FRAME,
  stoppedAnswer,
  StreamedTranscript,
  tellLiveAnswer,
  TIMEOUT_FRAME,
} from '../utils/turn-stream';
import { refuseRunWhileShuttingDown, trackRun } from '../utils/active-runs';
import { catchAsync } from '../../../libs/middlewares/catch-async.middleware';
import { regenerateQuery } from '../utils/chat-command';
import { keepsTypedOnly } from '../../agent-harness/domain/commands';
import { getCurrentOrgId } from '../../../libs/context/orgContext';
const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

/** Node hand-builds the AI request body, so the protocol has to ride in it: a header alone never reaches Python. */
const convRepo = (): ConversationRepository => getConversationRepository();

/**
 * A chat deleted or archived for everyone takes its Compare models branches with it. Run after the change commits:
 * a branch left behind by a failure is hidden everywhere, and must not undo the change itself.
 */
const discardCompareBranches = async (
  orgId: unknown,
  parentId: unknown,
): Promise<void> => {
  try {
    await convRepo().discardBranches(String(orgId), String(parentId));
  } catch (error) {
    logger.warn('Could not remove the Compare branches of a conversation', {
      parentId,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};
const searches = (): SemanticSearchRepository => getSemanticSearchRepository();

const listViews = (): ConversationExtensions['lists'] =>
  conversationExtensions().lists;
const aclVersionBump = (): Record<string, unknown> =>
  conversationExtensions().acl.versionBump();

/** A chat's stored project and its visibility to it, as `acl.projectPlacement` compares them. */
const placementOf = (conversation: {
  projectId?: mongoose.Types.ObjectId | string | null;
  projectVisibility?: 'private' | 'project';
}): ProjectPlacement => ({
  ...(conversation.projectId != null && {
    projectId: String(conversation.projectId),
  }),
  ...(conversation.projectVisibility !== undefined && {
    visibility: conversation.projectVisibility,
  }),
});

/** What moving a chat between project placements also stores, as `$set` fields and a `$unset` document. */
const placementChange = (
  before: ProjectPlacement,
  after: ProjectPlacement,
): { set: Record<string, unknown>; unset: Record<string, ''> } => {
  const { set, unset } = conversationExtensions().acl.projectPlacement(
    before,
    after,
    new Date(),
  );
  return {
    set,
    unset: Object.fromEntries(unset.map((field) => [field, ''] as const)),
  };
};

const rsAvailable = isReplicaSet();

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
/** Rows of the regenerated turn read from its question on, to find the cards around its answer; a longer turn keeps them. */
const REGENERATE_TURN_ROWS = 50;
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

export { startAIStream };

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
 * The rows a page's first and last turns hold past its edges. A turn's rows go with its hidden answer wherever they
 * sit, and a turn can run past either edge of the page, so the rows back to the turn's question and forward to the
 * next one are read through the repository and judged with the page. Each side reads at most `TURN_READ_STEPS` steps
 * of `TURN_READ_ROWS`; past that the turn's rows on the page are hidden as unconfirmed, never shown.
 */
interface TurnEdges<M> {
  lead: M[];
  trail: M[];
  leadUnknown: boolean;
  trailUnknown: boolean;
}

const turnEdgesOf = async <M extends ViewerMessage>(
  conversation: { _id: string; orgId: string },
  messages: M[],
  skip: number,
  total: number,
): Promise<TurnEdges<M>> => {
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
  return { lead, trail, leadUnknown, trailUnknown };
};

/** Session paths a find never returns unless asked (`select: false`); an aggregate has to leave them out itself. */
const unselectedSessionPaths = (): Record<string, 0> => {
  const hidden: Record<string, 0> = {};
  ChatSession.schema.eachPath((path, type) => {
    if (
      (type as { options?: { select?: unknown } }).options?.select === false
    ) {
      hidden[path] = 0;
    }
  });
  return hidden;
};

/** Edition-only session fields an archive row leaves out unless collaborative chats are on, as list rows do. */
const editionFieldsHidden = (
  req: AuthenticatedUserRequest,
): Record<string, 0> =>
  conversationContextOf(req).extended === true
    ? {}
    : Object.fromEntries(
        schemaExtensions.editionSessionFields.map((field) => [
          field,
          0 as const,
        ]),
      );

/** The `select` of an archive list: the fields the edition names for its rows, else a list's default. */
const archiveSelect = (ctx: ConversationRequestContext): string => {
  const fields = conversationExtensions().archives.rowFields(ctx);
  return fields ? fields.join(' ') : listViews().select(ctx);
};

/**
 * The `$project` of an archive aggregate: the fields the edition names for its rows, with those the pipeline adds
 * before it (`computed`); else everything but what a find would leave out, `hidden`, and the edition's own fields.
 */
const archiveProject = (
  req: AuthenticatedUserRequest,
  computed: readonly string[] = [],
  hidden: Record<string, 0> = {},
): Record<string, 0 | 1> => {
  const fields = conversationExtensions().archives.rowFields(
    conversationContextOf(req),
  );
  return fields
    ? Object.fromEntries(
        [...fields, ...computed].map((field) => [field, 1 as const]),
      )
    : {
        __v: 0,
        ...unselectedSessionPaths(),
        ...hidden,
        ...editionFieldsHidden(req),
      };
};

/**
 * Whose a stored row is, for the reader check: who wrote the question or note, or asked for the answer,
 * else who sent it (a collaborator's turn, SS2: `messageActor`), else (a row from before any of these was stored)
 * the chat's owner.
 */
const rowOwnerOf =
  (ownerId: string | undefined) =>
  (message: object): string | undefined => {
    const { messageType, authorUserId, requestedBy } = message as {
      messageType?: unknown;
      authorUserId?: { toString(): string } | null;
      requestedBy?: { toString(): string } | null;
    };
    const by =
      messageType === 'user_query' || messageType === 'note'
        ? authorUserId
        : requestedBy;
    if (by !== undefined && by !== null) return by.toString();
    return ownerId === undefined
      ? (message as { senderId?: { toString(): string } | null }).senderId?.toString()
      : messageActor(message, ownerId);
  };

/** The person a request reads for: a member of the chat its guard let them read. */
const readerOfRequest = (req: AuthenticatedUserRequest): Reader => ({
  userId: String(req.user?.userId),
  orgId: String(req.user?.orgId),
  member: true,
  identity: callerIdentityOf(req),
});

/** The chat a guarded request reads, as the reader check needs it: who owns it and who it is shared with. */
const readerSessionOf = (
  req: AuthenticatedUserRequest,
  fallback: { _id: string; orgId: string; userId?: string },
): ReaderSession => {
  let granted: { _id?: unknown } | undefined;
  try {
    granted = conversationContextOf(req).grant?.session;
  } catch {
    // A route with no conversation guard (a list) has no grant: the chat as the handler read it.
    granted = undefined;
  }
  return granted && idOfValue(granted._id) === fallback._id
    ? granted
    : fallback;
};

/**
 * Rows as the reader may see them (`readerView`): an answer is shown whole only to readers who can open everything
 * its label names, its text alone to those it was shared with, and to nobody else; another person's steps never.
 * The answers no label decided alone (an asker's own, and those from before labels) also pass the per-record view,
 * each with its whole turn: a labelled answer is judged by its label, which
 * names the chat's uploads it used, and those open for the chat's members (the chat's own file rule). Every chat is
 * read this way, shared or not: whether a chat is shared is no reason to show a reader more. Rows of several chats
 * (the archives) are judged chat by chat.
 */
const redactForViewer = async <M extends object>(
  req: AuthenticatedUserRequest,
  appConfig: AppConfig,
  messages: M[],
  ownerOf: (message: M) => string | undefined,
  chats: ReadonlyMap<
    string,
    { userId?: string; initiator?: string }
  > = new Map(),
): Promise<M[]> => {
  const bySession = new Map<string, number[]>();
  messages.forEach((m, i) => {
    const sid = idOfValue((m as { sessionId?: unknown }).sessionId) ?? '';
    const list = bySession.get(sid) ?? [];
    list.push(i);
    bySession.set(sid, list);
  });
  const out = [...messages];
  // One reader, one read: every chat's check shares what was settled and the read's one deadline.
  const known = newReadable();
  for (const [sid, indexes] of bySession) {
    const rows = indexes.map((i) => messages[i] as M);
    const chat = chats.get(sid);
    const { rows: shown } = await readerView(
      readerOfRequest(req),
      readerSessionOf(req, {
        _id: sid,
        orgId: String(req.user?.orgId),
        ...(chat?.userId !== undefined && { userId: chat.userId }),
      }),
      rows,
      {
        member: true,
        appConfig,
        known,
        ...(chat?.initiator !== undefined && { ownerId: chat.initiator }),
        perRecordFor: unlabelledAnswers,
        perRecordView: async <R extends object>(list: R[], settled: Readable) =>
          redactWithTurns(
            appConfig,
            viewerOf(req.user?.userId, req.user?.orgId),
            list,
            ownerOf as unknown as (
              message: ViewerMessage,
            ) => string | undefined,
            { known: settled, run: rows, sessionId: sid },
          ),
      },
    );
    const named = withAuthorsOf(rows, shown);
    indexes.forEach((i, n) => {
      out[i] = named[n] as M;
    });
  }
  return out;
};

/**
 * The rows the reader check gave back, with the author each had when it was handed over. The edition names rows on
 * the detail before the check, and the check rebuilds a hidden or shared answer from its stored fields alone; the
 * feed names rows after the check, so both reads give such a row its author (who asked it).
 */
const withAuthorsOf = <M extends object>(
  given: readonly M[],
  shown: M[],
): M[] =>
  shown.map((m, i) => {
    const author = (given[i] as { author?: unknown } | undefined)?.author;
    if (
      author === undefined ||
      (m as { author?: unknown }).author !== undefined
    ) {
      return m;
    }
    const named: M = { ...m, author };
    return named;
  });

/** `redactForViewer` for one page of a conversation, a turn that runs past either edge of it read with it. */
const pickFrom = <R extends object>(
  list: R[],
  all: readonly object[],
  judged: readonly object[],
): R[] => {
  const at = new Map(all.map((m, i) => [m, judged[i] ?? m]));
  return list.map((m) => (at.get(m) ?? m) as R);
};

const redactPageForViewer = async <M extends object>(
  req: AuthenticatedUserRequest,
  appConfig: AppConfig,
  conversation: {
    _id: string;
    orgId: string;
    userId?: string;
    initiator?: string;
  },
  messages: M[],
  skip: number,
  total: number,
  ownerOf: (message: M) => string | undefined,
  citationsDecided = false,
  // The harness's citation check could not be made: another person's answers are unconfirmed.
  checkFailed = false,
): Promise<M[]> => {
  // The reader check sees the page's first and last turns whole, as it sees a whole chat: a card goes with its
  // turn's answer, and an answer with its turn's question, wherever the page's edge falls.
  const edges = await turnEdgesOf(
    conversation,
    messages as unknown as ViewerMessage[],
    skip,
    total,
  );
  const lead = edges.lead as unknown as M[];
  const all = [...lead, ...messages, ...(edges.trail as unknown as M[])];
  const page = new Set<object>(messages);
  const { rows } = await readerView(
    readerOfRequest(req),
    readerSessionOf(req, conversation),
    all,
    {
      member: true,
      appConfig,
      ...(conversation.initiator !== undefined && {
        ownerId: conversation.initiator,
      }),
      // The per-record view for the answers no label decided alone, each with its turn; the rest as their label and
      // the reader check have them.
      perRecordFor: unlabelledAnswers,
      perRecordView: async <R extends object>(list: R[], known: Readable) =>
        pickFrom(
          list,
          all,
          await redactMessagesFor(
            appConfig,
            viewerOf(req.user?.userId, req.user?.orgId),
            all as unknown as ViewerMessage[],
            ownerOf as unknown as (
              message: ViewerMessage,
            ) => string | undefined,
            {
              known,
              leadUnknown: edges.leadUnknown,
              trailUnknown: edges.trailUnknown,
              checkFailed,
              // The harness decided the citations of this page's messages for the viewer; rows read past its edges it never saw.
              ...(citationsDecided && {
                citationsDecided: (m: ViewerMessage) => page.has(m),
              }),
            },
          ),
        ),
    },
  );
  return withAuthorsOf(
    messages,
    rows.slice(lead.length, lead.length + messages.length),
  );
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
      await factory(req, res, next);
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
): ReturnType<typeof asInternal> =>
  asInternal(streamChat(appConfig, deps), appConfig);

export const addMessageStream = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): ReturnType<typeof followUpStream> =>
  followUpStream(appConfig, deps, 'assistant');

export const addMessageStreamInternal = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): ReturnType<typeof asInternal> =>
  asInternal(addMessageStream(appConfig, deps), appConfig);

/** Lists count matches up to this many; one row past the page tells whether another page follows. */
const LIST_COUNT_CAP = 1000;

/** The caller's own pins: their `isPinned` as creator, and the ones `pins.setOwn` keeps. */
const pinnedByCaller = (
  userId: string,
  ownPinIds: readonly mongoose.Types.ObjectId[],
): QueryFilter<IChatSessionDocument> => ({
  $or: [
    { isPinned: true, userId: new mongoose.Types.ObjectId(userId) },
    { _id: { $in: [...ownPinIds] } },
  ],
});

/**
 * `filter` and `clause`, with `clause` added to each branch when `filter` is one top-level `$or` (as
 * `composeListFilter` builds a list's): an `$or` under an `$and` is read in full and sorted in memory, where a
 * top-level one is read branch by branch in index order.
 */
const andInEachBranch = (
  filter: QueryFilter<IChatSessionDocument>,
  clause: QueryFilter<IChatSessionDocument>,
): QueryFilter<IChatSessionDocument> => {
  const branches = (filter as { $or?: unknown }).$or;
  if (Object.keys(filter).length !== 1 || !Array.isArray(branches)) {
    return { $and: [filter, clause] };
  }
  return {
    $or: branches.map((branch: QueryFilter<IChatSessionDocument>) =>
      Object.keys(branch).length === 1 && Array.isArray(branch.$and)
        ? { $and: [...branch.$and, clause] }
        : { $and: [branch, clause] },
    ),
  };
};

/**
 * Rows `skip` to `skip + count` of `filter` with the `pinned` ones first, each part in `sort` order:
 * what one sort with the pin as its first key returns, for a pin `find` can't sort by.
 */
const pinnedFirstRows = async (
  filter: QueryFilter<IChatSessionDocument>,
  pinned: QueryFilter<IChatSessionDocument>,
  sort: Record<string, 1 | -1>,
  skip: number,
  count: number,
  select: string,
): Promise<object[]> => {
  const pinnedRows = await ChatSession.find({ $and: [filter, pinned] })
    .sort(sort)
    .limit(LIST_COUNT_CAP)
    .select(select)
    .lean()
    .exec();
  const head = pinnedRows.slice(skip, skip + count);
  if (head.length === count) return head;
  const pinnedIds = pinnedRows.map((row) => row._id);
  const rest = await ChatSession.find(
    pinnedIds.length === 0
      ? filter
      : andInEachBranch(filter, { _id: { $nin: pinnedIds } }),
  )
    .sort(sort)
    .skip(Math.max(0, skip - pinnedRows.length))
    .limit(count - head.length)
    .select(select)
    .lean()
    .exec();
  return [...head, ...rest];
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
    const ctx = conversationContextOf(req);
    const stateFilter = {
      isArchived: false,
      // Chats shared with the user include agent chats, so the recipient can open them
      // from the sidebar without first finding the agent. Owned chats stay plain-chat only:
      // agent chats are listed per agent.
      ...(isOwned && EXCLUDE_AGENT),
      // A Compare models branch is opened by its id, never listed.
      ...(conversationId
        ? { _id: new mongoose.Types.ObjectId(conversationId as string) }
        : { compareOf: { $exists: false } }),
    };
    const { filter, queryConstraints } = await buildListQuery(req, {
      orgId,
      listFilter: listFilterOf(req),
      stateFilter,
    });
    // Other people's pins of a shared chat are theirs alone, so they order only their own shared list.
    const ownPinIds = isOwned
      ? undefined
      : await conversationExtensions().pins.ownIds(ctx);
    const select = listViews().select(ctx);
    const rowsRead =
      ownPinIds === undefined
        ? ChatSession.find(filter)
            .sort(sortOptions as any)
            .skip(skip)
            .limit(limit + 1)
            .select(select)
            .lean()
            .exec()
        : pinnedFirstRows(
            filter,
            pinnedByCaller(String(userId), ownPinIds),
            sortOptions as Record<string, 1 | -1>,
            skip,
            limit + 1,
            select,
          );

    const [rows, totalCount] = await Promise.all([
      rowsRead,
      ChatSession.countDocuments(filter, { limit: LIST_COUNT_CAP }),
    ]);
    const conversations = rows.slice(0, limit);
    const ownPinned = new Set(ownPinIds?.map(String));

    const views = await listViews().accessViews(ctx, conversations);
    const decorate = await listViews().rowDecorator(ctx, conversations);
    let processedConversations = conversations.map((conversation: any) => {
      const row = conversation as IConversation;
      const id = String(conversation._id);
      const listed = decorate(
        addComputedFields(
          isOwned ? row : withoutSharedWith(row),
          userId,
          views.get(id),
        ),
      );
      return ownPinned.has(id) ? { ...listed, isPinned: true } : listed;
    });
    if (!isOwned) {
      processedConversations = await attachSharedBy(
        processedConversations,
        orgId,
      );
    }

    const response = {
      conversations: processedConversations,
      source,
      pagination: {
        ...buildPaginationMetadata(totalCount, page, limit),
        hasNextPage: rows.length > limit,
      },
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
  (
    appConfig: AppConfig,
    users: IUserDirectory = defaultUsers,
    checks: ViewerChecks = {},
  ) =>
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
        ...kindFilterOf(req, EXCLUDE_AGENT),
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
          // The newest saved failure tells whose run `failReason` is from; the response never carries it.
          conversationErrors: { $slice: -1 },
          // Who asked the run that failed, so `Failed` is said to them alone; never sent.
          failedRunBy: 1,
          modelInfo: 1,
          projectId: 1,
          projectVisibility: 1,
          // Where a fork came from and its agent, for `resolveForkOrigin` / `resolveForks`.
          metadata: 1,
          agentKey: 1,
          // Read before the rows: a change that lands while they are read moves `rev` past this
          // value, so a reader that resumes from it still gets that change.
          rev: 1,
        })
        .lean()
        .exec();

      if (!session) {
        throw new NotFoundError('Conversation not found');
      }

      const sessionId = session._id;
      const harness = await harnessSessionView(
        String(sessionId),
        String(session.orgId),
      );

      const totalMessages = await ChatSessionMessage.countDocuments({
        sessionId,
        orgId,
        ...NOT_REMOVED,
      });

      const { skip, limit: effectiveLimit } = olderMessagesWindow(
        totalMessages,
        page,
        limit,
      );

      // A fork written before forks detached their copies names the original's runs (FORKFIX).
      const messages = await withForeignRunsDetached(
        { _id: String(sessionId), metadata: harness.metadata },
        String(orgId),
        await getMessages(sessionId, {
          orgId,
          skip,
          limit: effectiveLimit,
          populateCitations: true,
        }),
      );

      await conversationExtensions().content.viewed({
        appConfig,
        conversationId: String(sessionId),
        ownerId: session.initiator?.toString() ?? '',
        viewerId: String(userId),
        orgId: String(orgId),
        page,
        messages,
        requestId,
      });

      const conversationWithMessages = attachMessages(
        session,
        messages,
        userId,
        {
          asStored: true,
        },
      );

      // Whose read it is, and whether the harness's citation check could be made (set by withViewerChecks).
      const viewerRead = {
        orgId: String(orgId),
        sessionId: String(sessionId),
        checkFailed: false,
      };
      const baseResponse = await attachSharedByIfRecipient(
        await withViewerChecks(
          buildConversationResponse(
            withServedAssistantMode({
              ...conversationWithMessages,
              harnessVersion: harness.harnessVersion,
            }) as unknown as IChatSessionDocument & {
              harnessVersion?: string;
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
            conversationWithMessages?.messages || [],
            accessViewOf(req),
          ),
          req,
          checks,
          viewerRead,
        ),
        orgId,
      );

      const conversationResponse =
        await conversationExtensions().details.decorate(
          req,
          { ...baseResponse, rev: session.rev ?? 0 },
          messages,
          { users, ownerId: grant.session.userId.toString() },
        );
      // Hidden in stored order, then sorted as the client asked: a turn's tool rows follow their
      // answer, and `sortBy` must not separate them first.
      const initiatorId = session.initiator?.toString();
      const shown = await redactPageForViewer(
        req,
        appConfig,
        {
          _id: String(sessionId),
          orgId: String(session.orgId),
          initiator: initiatorId,
        },
        conversationResponse.messages,
        skip,
        totalMessages,
        rowOwnerOf(initiatorId),
        checks.citations?.checksRecords() === true,
        viewerRead.checkFailed,
      );
      const redacted = conversationExtensions().details.afterReaderCheck(
        conversationResponse.messages,
        shown,
      );
      conversationResponse.messages = sortMessages(
        redacted as unknown as IMessageDocument[],
        messageSortOptions as { field: keyof IMessage },
      ) as unknown as typeof redacted;

      // Build filters metadata using existing helper
      const filtersMetadata = buildFiltersMetadata(
        messageFilter,
        query,
        messageSortOptions,
      );

      const forkView = {
        _id: String(sessionId),
        orgId: String(session.orgId),
        metadata: session.metadata,
        agentKey: session.agentKey,
      };
      const [forkedFrom, forks] = await Promise.all([
        resolveForkOrigin(forkView, String(userId)),
        resolveForks(forkView, String(userId)),
      ]);

      // Prepare response using existing format
      const response = {
        conversation: {
          ...conversationResponse,
          ...(forkedFrom && { forkedFrom }),
          ...(forks.length > 0 && { forks }),
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

    const deletable: Record<string, unknown> = {
      _id: conversationGrantOf(req).session._id,
      orgId,
      isDeleted: false,
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
          ...aclVersionBump(),
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
      if (erasures !== undefined) {
        // In the delete's own unit of work, so a lost enqueue is still found (E7B).
        const tx = new MongoTx(session ?? undefined);
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

      await conversationExtensions().events.conversationDeleted(
        req,
        conversationGrantOf(req),
        session ?? undefined,
      );

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
    await stopDeletedChatRun(
      stopper,
      String(orgId),
      String(conversationId),
      String(userId),
      requestId,
    );
    await discardCompareBranches(orgId, conversationId);

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
      void session.endSession();
      session = null;
    }
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

export const shareConversationById =
  (
    appConfig: AppConfig,
    sharing: IConversationSharing = new ConversationSharing(
      appConfig,
      rsAvailable,
    ),
    participants: ParticipantRecorderSource = noParticipantRecorder,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const { conversationId } = req.params;
    const { userIds, accessLevel } = req.body;
    try {
      const recorder = await participants();
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

      const shared = await sharing.share(
        conversationGrantOf(req),
        callerIdentityOf(req),
        userIds,
        accessLevel === 'write' ? 'write' : 'read',
        recorder,
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
    sharing: IConversationSharing = new ConversationSharing(
      appConfig,
      rsAvailable,
    ),
    participants: ParticipantRecorderSource = noParticipantRecorder,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const { conversationId } = req.params;
    try {
      const { userIds } = req.body;
      const recorder = await participants();
      logger.debug('Attempting to unshare conversation', {
        requestId,
        conversationId,
        userId: req.user?.userId,
        timestamp: new Date().toISOString(),
      });
      if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
        throw new BadRequestError('userIds is required and must be an array');
      }

      const unshared = await sharing.unshare(
        conversationGrantOf(req),
        callerIdentityOf(req),
        userIds,
        recorder,
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

/** An agent chat's share: read-only and owner-only with collaborative chats off (see `ConversationSharing`). */
export const shareAgentConversation = shareConversationById;

export const unshareAgentConversation = unshareConversationById;

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
  // Passed by the router so a team member can move a chat into the project.
  appConfig?: AppConfig,
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
      const placed = placementChange(placementOf(conversation), {});
      update = {
        $unset: { projectId: '', projectVisibility: '', ...placed.unset },
        ...aclVersionBump(),
      };
    } else {
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
      const placed = placementChange(placementOf(conversation), {
        projectId: String(new mongoose.Types.ObjectId(projectId)),
        visibility: projectVisibility,
      });
      update = {
        $set: {
          projectId: new mongoose.Types.ObjectId(projectId),
          projectVisibility,
          ...placed.set,
        },
        ...(Object.keys(placed.unset).length > 0 && { $unset: placed.unset }),
        ...aclVersionBump(),
      };
      void ProjectService.touchActivity(projectId);
    }

    const updated = await ChatSession.findOneAndUpdate(
      { _id: conversationId, ...sessionTypeFilter },
      update,
      { returnDocument: 'after' },
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

    const before = placementOf(conversation);
    const placed = placementChange(before, { ...before, visibility });
    const updated = await ChatSession.findOneAndUpdate(
      { _id: conversationId, ...sessionTypeFilter },
      {
        $set: { projectVisibility: visibility, ...placed.set },
        ...(Object.keys(placed.unset).length > 0 && { $unset: placed.unset }),
        ...aclVersionBump(),
      },
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
interface CardTurnQuestion<Id = string> {
  question: IMessage & { _id: Id };
  /** Rows between that question and the turn's last answer: the card, its answers, earlier replies. */
  intermediateIds: Id[];
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
export function findQuestionBehindCard<Id = string>(
  older: Array<IMessage & { _id: Id }>,
): CardTurnQuestion<Id> | null {
  const intermediateIds: Id[] = [];
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
  older: Array<IMessage & { _id: unknown }>,
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
  older: Array<IMessage & { _id: unknown }>,
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
/** A row of the turn being regenerated, as the lean reads return it. */
type TurnRow = IMessage & { _id: mongoose.Types.ObjectId; seq: number };

/** What the regenerate request was checked against before the run starts. */
interface RegenerationValidation {
  conversation: IChatSessionDocument;
  userQuery: TurnRow;
  staleAskToolCallIds: mongoose.Types.ObjectId[];
  cardEdit: CardTurnQuestion<mongoose.Types.ObjectId> | null;
  editBlocked: boolean;
  newerQuestion: boolean;
}

/** A row nobody asked or answered: a system line (a notice about the chat) or a `feedback` row. */
const isNotice = (row: Pick<IMessage, 'messageType'>): boolean =>
  row.messageType === 'system' || row.messageType === 'feedback';

/**
 * Regenerates the answer of a chat or an agent conversation in place. Under an extended
 * guard policy it runs under the lease `runLease()` acquired, on behalf of the caller (who the
 * guard proved asked the question); otherwise the same steps run unfenced.
 */
async function regenerateAnswersInternal(
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  req: AuthenticatedUserRequest,
  res: Response,
  config: RegenerationConfig,
): Promise<void> {
  refuseRunWhileShuttingDown();
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  const streamRun = new ChatStreamRun(req, res);
  let session: ClientSession | null = null;
  const { conversationId, messageId, agentKey } = req.params;
  const userId = req.user?.userId;
  const orgId = req.user?.orgId;
  // The agent that wrote the answer being regenerated, when it is not the conversation's own.
  const turns = conversationExtensions().turns;
  const regenGuest = turns.respondingAgentKeyOf(req);
  // Changed answers to the card this reply followed; they take the place of the saved ones.
  const regenAnswersBody = req.body as { answers?: unknown } | undefined;
  const replacementAnswers: string | undefined =
    typeof regenAnswersBody?.answers === 'string'
      ? regenAnswersBody.answers
      : undefined;

  let existingConversation: IChatSessionDocument | null = null;
  let reservation: RunReservation | null = null;
  let run: TurnRun | undefined;
  /** Set once the AI service opened the stream: from here a failure is the run's, and replaces its answer. */
  let runStarted = false;

  const modelInfo = extractModelInfo(req.body);
  const protocol = resolveProtocol(
    req.body as Record<string, unknown>,
    req.query,
  );
  const frame = streamFrameOf(req);

  // Every way the turn can end claims it first, so exactly one of them writes the outcome and settles.
  let finished = false;
  const claim = (): boolean => {
    if (finished) return false;
    finished = true;
    live.stopWatching?.();
    return true;
  };
  let completeData: IAIResponse | null = null;
  const contentAccumulator = new StreamedTranscript();
  // Writes the stream handler starts without awaiting; the terminal write waits for them.
  const pendingWrites: Array<Promise<void>> = [];
  const live: {
    abort?: () => void;
    /** Finishes the run as the stream's end would, once its answer (or error) has arrived. */
    finalizeAnswered?: () => boolean;
    /** Set once streaming: a shutdown stops waiting for the run, and its time limits stop counting. */
    stopWatching?: () => void;
    /** Set once streaming: what a stopped answer is saved through. */
    answer?: TurnAnswerHooks;
    /** Set once the request is accepted: saves an edited question with the answer shown for it. */
    saveEditedQuestion?: () => Promise<void>;
    /** Removes the rows a card turn asked again replaced, once its new answer is saved. */
    dropReplacedTurn?: () => Promise<boolean>;
    /** Puts back the question an edit rewrote, when the answer for it could not be saved. */
    restoreOriginalQuestion?: () => Promise<void>;
  } = {};

  /** The end line of a run stopped before its answer: what streamed is its stopped answer. */
  const stoppedEnding = (): StreamEnd => {
    streamRun.sawResult(stoppedAnswer(contentAccumulator));
    return { kind: 'answered' };
  };
  const stopped = async (): Promise<void> => {
    if (!claim()) return;
    streamRun.end(stoppedEnding());
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
  const saveStopped = async (
    ending: StreamEnd = { kind: 'abandoned' },
  ): Promise<void> => {
    try {
      if (existingConversation) {
        const streamed = {
          text: contentAccumulator.getText(),
          parts: contentAccumulator.getParts(),
        };
        const { text, parts } =
          live.answer?.redactStopped?.(streamed.text, streamed.parts) ??
          streamed;
        // A partial answer was shown for the edited question, so the question is saved with it.
        if (text) {
          await live.saveEditedQuestion?.().catch((err: unknown) => {
            logger.error('Failed to save the edited question on disconnect', {
              requestId,
              error: err instanceof Error ? err.message : String(err),
            });
          });
        }
        await savePartialConversation(existingConversation, text, null, {
          replaceMessageId: messageId,
          run,
          parts,
        });
        await live.dropReplacedTurn?.();
      }
      streamRun.end(ending);
    } catch (err: unknown) {
      streamRun.end({ kind: 'failed', error: err });
      // Without its answer, the edited question goes back to what the reader had.
      await live.restoreOriginalQuestion?.();
    }
    await gate.settle(existingConversation ? 'stopped' : 'unstarted');
  };
  // Before any await, so a client that leaves during validation still releases the lease.
  const upstreamAbort = attachUpstreamAbort(res, () => {
    if (live.finalizeAnswered?.()) return;
    if (!claim()) return;
    void saveStopped();
  });
  live.abort = () => {
    upstreamAbort.abort();
  };
  /** The server ends the run (a time limit ran out, or this process is stopping), as `TurnStreamPump` does. */
  const endByServer = async (
    frame: string,
    timedOut = false,
  ): Promise<void> => {
    upstreamAbort.abort();
    if (live.finalizeAnswered?.()) return;
    if (!claim()) return;
    await Promise.all(pendingWrites);
    // A run that went quiet past its limit ended upstream; a shutdown stops it as a Stop would.
    const ending: StreamEnd = timedOut
      ? { kind: 'failed', error: new GatewayTimeoutError('The run reached its time limit'), fromStream: true }
      : stoppedEnding();
    if (
      timedOut &&
      !contentAccumulator.hasContent() &&
      existingConversation &&
      messageId !== undefined
    ) {
      try {
        await replaceMessageWithError(
          existingConversation,
          messageId,
          CHAT_ERROR_MESSAGES.timedOut,
          null,
          'timeout',
          undefined,
          undefined,
          run,
        );
        streamRun.end(ending);
      } catch (err: unknown) {
        streamRun.end({ kind: 'failed', error: err });
      }
      await gate.settle('failed');
    } else {
      await saveStopped(ending);
    }
    res.write(frame);
    res.end();
  };

  // Helper function to validate and get conversation
  async function performRegenerateAnswersValidation(
    session?: ClientSession | null,
  ): Promise<RegenerationValidation> {
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

    const rowsOf = (
      filter: QueryFilter<IMessage>,
      sort: 1 | -1,
      limit: number,
    ): Promise<TurnRow[]> => {
      let query = ChatSessionMessage.find({
        sessionId: conversation._id,
        orgId: conversation.orgId,
        ...filter,
        ...NOT_REMOVED,
      })
        .sort({ seq: sort })
        .limit(limit);
      if (session) {
        query = query.session(session);
      }
      return query.lean<TurnRow[]>().exec();
    };
    // The newest answer and its question, however many rows came after them. An ask_user_question
    // turn ends with `tool_call` rows saved after its answer, so the answer is not always the newest row.
    const [lastBot] = await rowsOf({ messageType: 'bot_response' }, -1, 1);
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

    const [userQuery] = await rowsOf(
      { messageType: 'user_query', seq: { $lt: lastBot.seq } },
      -1,
      1,
    );
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
      ? findQuestionBehindCard(
          await rowsOf({ seq: { $lt: lastBot.seq } }, -1, REGENERATE_TURN_ROWS),
        )
      : null;
    // Only a stored card makes a card turn: a question the reader typed with the same first words has none.
    const cardEdit = behindCard?.hasCard ? behindCard : null;
    const editBlocked = looksLikeCardAnswer && behindCard === null;
    // A question after the newest answer has none of its own (a run that never got to answer), so
    // the answer found here belongs to an older question than the last one.
    const newerQuestion =
      (
        await rowsOf(
          { messageType: 'user_query', seq: { $gt: lastBot.seq } },
          1,
          1,
        )
      ).length > 0;
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

    // The turn as it stands before the regeneration overwrites it, oldest first.
    const staleAskToolCallIds = staleAskUserQuestionToolCallIds(
      await rowsOf({ seq: { $gte: userQuery.seq } }, 1, REGENERATE_TURN_ROWS),
      lastBot._id,
    ).map((id) => new mongoose.Types.ObjectId(id));

    return {
      conversation,
      userQuery,
      staleAskToolCallIds,
      cardEdit,
      editBlocked,
      newerQuestion,
    };
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
        plainConversation = await reloadForErrorFrame(
          conversationId ?? '',
          messageId ?? '',
          run,
          frame,
          session,
        );
        // The conversation an error event carries is hidden for its reader as in a fetch; its error row is this
        // run's own, as an answer frame's new answer is.
        if (plainConversation) {
          plainConversation = await conversationForViewer(
            appConfig,
            viewerOf(userId, orgId),
            plainConversation as { messages?: unknown },
            {
              ...(messageId !== undefined && { freshIds: [messageId] }),
              identity: callerIdentityOf(req),
            },
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

    if (!conversationId) {
      throw new BadRequestError('Conversation ID is required');
    }
    // A lease already holds the thread for this run.
    if (!gate.lease) {
      reservation = await reserveRun(
        config.buildQueryFilter(
          conversationId,
          orgId as string | undefined,
          userId as string | undefined,
          agentKey,
        ) as QueryFilter<IChatSessionDocument>,
      );
      gate = reservedGate(reservation);
    }

    // Validate conversation and message
    let validationResult: RegenerationValidation | null = null;
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
    streamRun.bind({ conversationId: String(validationResult.conversation._id), agentKey });
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
    const shownQuestion =
      (cardEdit?.question as TurnRow | undefined) ?? userQuery;
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
    const turnRun: TurnRun = {
      lease: gate.lease,
      requestedBy: new mongoose.Types.ObjectId(String(userId)),
      inReplyTo: userQuery._id,
      ...(regenGuest !== undefined && { respondingAgentKey: regenGuest }),
      replaces: messageId,
      ...(editedQuery !== null && { editedQuestion: editedQuery }),
    };
    run = turnRun;

    // The history is what came before the question being answered again; the turn itself (its
    // question, its answer and any `tool_call` rows beside them) is left out by position.
    // A card turn asked again starts from its first question, so its card rows are not history.
    const askedQuestion =
      turnQuery !== null && cardEdit ? shownQuestion : userQuery;
    const regenHistory = await deps.feed.historyBefore(
      existingConversation._id,
      askedQuestion.seq,
      { withLabels: true },
    );
    // Only the history that goes to the model is checked, not the turn being redone: a hidden answer is never repeated.
    const accessMemo = newReadable();
    const regenContext = await turns.regenerationContext({
      req,
      deps,
      conversation: existingConversation,
      // As asked now: an edit's words and files, not the stored ones they replace.
      question:
        turnQuery === null
          ? askedQuestion
          : {
              ...askedQuestion,
              content: turnQuery,
              ...(editedAttachments && { attachments: editedAttachments }),
            },
      // The history filter first (it reads what each answer names), then only what the reader check lets reach
      // the asker's model, sealed with each row's version.
      history: await includableRows(
        {
          userId: String(userId),
          orgId: String(orgId),
          member: true,
          identity: callerIdentityOf(req),
        },
        existingConversation as unknown as ReaderSession,
        await historyForModel(
          appConfig,
          viewerOf(userId, orgId),
          regenHistory,
          accessMemo,
        ),
        {
          appConfig,
          known: accessMemo,
          ...(typeof existingConversation.agentKey === 'string' &&
            existingConversation.agentKey !== '' && {
              usableAgents: [existingConversation.agentKey],
            }),
          // As the follow-up it answers again: the row its question replies to is the sender's pick.
          ...(repliedRowOf(askedQuestion) !== undefined && {
            replyTargetId: repliedRowOf(askedQuestion),
          }),
        },
      ),
      storedHistory: regenHistory,
      gate,
    });
    const previousConversations = regenContext.previousConversations;

    // The title of a chat is its first question, so editing that question moves the title too,
    // unless the user already renamed the chat. Only the owner's edit does: where others may edit too,
    // as in a shared chat, one of them would otherwise rename it for everyone. A notice stored before the
    // question does not make it a later one.
    let editedTitle: string | undefined;
    if (
      editedQuery !== null &&
      regenHistory.every(isNotice) &&
      isOwnersTurn(turnRun, existingConversation)
    ) {
      const titleOf = await turns.titleRule({
        req,
        deps,
        question: shownQuestion,
      });
      // A chat titled before titles skipped a quoted excerpt has the question's first 100 characters.
      if (
        existingConversation.title === titleOf(shownQuestion.content) ||
        existingConversation.title === shownQuestion.content.slice(0, 100)
      ) {
        editedTitle = titleOf(editedQuery);
      }
    }
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
      if (editedTitle !== undefined) {
        existingConversation.title = editedTitle;
        editedTitles.set(existingConversation, editedTitle);
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
    // The rows the new answer replaced go once it is saved, and never fail it. They stay as
    // removed markers, so a reader that already holds them learns they are gone.
    const dropReplacedTurn = async (): Promise<boolean> => {
      if (!turnAnswered || !existingConversation) return false;
      if (replacedTurnIds.length === 0) return false;
      try {
        await retireMessagesById(
          replacedTurnIds,
          {
            sessionId: existingConversation._id,
            orgId: existingConversation.orgId,
          },
          session,
          turnRun.requestedBy,
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
    live.saveEditedQuestion = saveEditedQuestion;
    live.dropReplacedTurn = dropReplacedTurn;
    live.restoreOriginalQuestion = restoreOriginalQuestion;

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
      regenerationId: new Types.ObjectId().toString(),
      timezone: req.body.timezone || null,
      currentTime: req.body.currentTime || null,
      runId:
        gate.lease?.runId ?? (req.body as { runId?: string }).runId ?? null,
      ...regenContext.aiPayload,
      ...(isAGUI(protocol) ? { protocol: AGUI_PROTOCOL } : {}),
    };
    if (regenGuest === undefined && (agentKey || regenIsAgentMode)) {
      assignToolsToPayload(aiPayload, req.body.tools);
      assignAgentCapabilitiesToPayload(
        aiPayload,
        req.body as Record<string, unknown>,
      );
    }
    // With the lease the guard already proved the asker's own project access; without it the owner is the caller.
    const regenProject = gate.lease
      ? guardedProjectOf(req)
      : await loadProjectForSession(
          existingConversation.orgId.toString(),
          existingConversation.userId.toString(),
          existingConversation.projectId,
          ownerProjectOptions(req, deps, existingConversation._id),
        );
    applyProjectScope(aiPayload, regenProject);
    if (existingConversation.projectId) {
      void ProjectService.touchActivity(
        existingConversation.projectId.toString(),
      );
    }

    const regenEndpoint =
      regenGuest !== undefined
        ? `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(regenGuest)}/chat/stream`
        : regenIsAgentMode
          ? `${appConfig.aiBackend}/api/v1/agent/agentIdPlaceholder/chat/stream`
          : config.buildAIEndpoint(appConfig, agentKey);

    const aiCommandOptions: AICommandOptions = {
      uri: regenEndpoint,
      method: HttpMethod.POST,
      headers: {
        // The agent the request goes to: a guest that answers, else the session's own (none in agent mode).
        ...(await resolvedProxyHeaders(req, {
          agentKey: regenGuest ?? (regenIsAgentMode ? null : agentKey),
          conversationId: conversationId,
        })),
        'Content-Type': 'application/json',
      },
      body: aiPayload,
    };

    // Variables to collect complete response data
    let askUserQuestionPayload: unknown = null;
    let buffer = '';
    /** True when the AI backend already sent a terminal error we forwarded and saved */
    let upstreamAiErrorEventForwarded = false;

    const stream = await streamRun.open(() =>
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
    runStarted = true;
    upstreamAbort.bindStream(stream);
    keepRunAlive(
      stream,
      existingConversation._id,
      reservation?.runId,
      gate.lease,
    );
    const streamExtension = turns.streamFor(req, deps);
    live.answer = streamExtension.answer;
    // Others who read the chat see the new answer as it is written, as for any other turn.
    const liveAnswer = streamExtension.liveAnswer?.(
      turnRun,
      existingConversation,
    );
    if (liveAnswer) contentAccumulator.followNumberedCopies();
    const liveConversationId = existingConversation._id;
    if (liveAnswer)
      gate = gateTellingLive(gate, liveAnswer, liveConversationId);
    const watchdog = new RunWatchdog(
      streamExtension.runLimits(turnRun, existingConversation),
      (limit) => {
        logger.warn('Run reached its time limit; stopping the regeneration', {
          requestId,
          conversationId,
          limit,
        });
        void endByServer(TIMEOUT_FRAME, true);
      },
    );
    const untrack = trackRun((reason) => {
      if (reason === 'shutdown') return endByServer(SHUTDOWN_FRAME);
      // As the AI backend's own stop ends it: what streamed is saved as a stopped answer.
      upstreamAbort.abort();
      if (!finished && !upstreamAiErrorEventForwarded) {
        completeData ??= stoppedAnswer(contentAccumulator);
        streamRun.sawResult(completeData);
      }
      return finalizeRun();
    }, gate.lease?.runId);
    live.stopWatching = () => {
      untrack();
      watchdog.stop();
    };

    // Process SSE events, capture complete event, and forward non-complete events
    stream.on('data', (chunk: Buffer) => {
      streamRun.observe(chunk);
      watchdog.onChunk(chunk);
      if (finished) return;
      buffer = handleRegenerationStreamData(
        chunk,
        buffer,
        existingConversation,
        messageId || null,
        session,
        res,
        (data: IAIResponse) => {
          completeData = data;
          streamRun.sawResult(completeData);
        },
        contentAccumulator,
        (code: unknown) => {
          upstreamAiErrorEventForwarded = true;
          streamRun.sawRunError(code);
        },
        (payload: unknown) => {
          askUserQuestionPayload = payload;
        },
        turnRun,
        (write) => pendingWrites.push(write),
        streamExtension.answer,
      );
      if (liveAnswer) {
        tellLiveAnswer(() => {
          liveAnswer.text(contentAccumulator.getLiveAnswer());
        }, liveConversationId);
      }
    });

    const finalizeRun = async (): Promise<void> => {
      if (!claim()) return;
      let outcome: TurnOutcome = 'failed';
      let lastFrame = (): Promise<void> => Promise.resolve();
      try {
        await Promise.all(pendingWrites);
        // Save the AI response to the conversation, replacing the existing message
        // A failed save is handled once, by the catch below: one error frame, one saved reason.
        if (completeData && existingConversation) {
          // A run that ended in an error answer, or in none, did not answer the edited question: it
          // stays as it was, with the rows of its card turn, and only the answer is replaced.
          const answered: IAIResponse = completeData;
          const answeredEdit =
            !isClassifiedFailureAnswer(answered) &&
            (Boolean(answered.answer?.trim()) ||
              Boolean(askUserQuestionPayload) ||
              answered.status === 'waiting_input');
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
              frame,
              streamExtension.answer,
            );
          if (await dropReplacedTurn()) {
            const shown = responseConversation as {
              messages?: Array<{ _id?: unknown }>;
            };
            const gone = new Set(replacedTurnIds.map(String));
            shown.messages = shown.messages?.filter(
              (row) => !gone.has(String(row._id)),
            );
          }
          outcome = outcomeForStatus(
            (responseConversation as { status?: string }).status,
          );
          const shownConversation = await conversationForViewer(
            appConfig,
            viewerOf(userId, orgId),
            responseConversation as { messages?: unknown },
            {
              known: accessMemo,
              freshIds: [messageId],
              identity: callerIdentityOf(req),
            },
          );

          // Send final response event with the complete conversation data
          lastFrame = () => {
            sendSSECompleteEvent(
              res,
              shownConversation,
              savedCitations.length,
              requestId ?? '',
              startTime,
              protocol,
              searchDegradedMeta(completeData),
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
        streamRun.end({ kind: 'answered' });
      } catch (dbError: unknown) {
        if (dbError instanceof LeaseLostError) {
          streamRun.end(stoppedEnding());
          outcome = 'stopped';
          lastFrame = () => {
            res.write(STOPPED_FRAME);
            return Promise.resolve();
          };
        } else {
          streamRun.end({ kind: 'failed', error: dbError });
          const dbMessage =
            dbError instanceof Error ? dbError.message : String(dbError);
          await restoreOriginalQuestion();

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
    };
    live.finalizeAnswered = () => {
      if (!completeData && !upstreamAiErrorEventForwarded) return false;
      runGuarded(finalizeRun);
      return true;
    };
    stream.on('end', () => {
      runGuarded(finalizeRun);
    });

    stream.on('error', async (error: Error) => {
      if (upstreamAbort.isAborted()) return;
      if (!claim()) return;
      streamRun.end({ kind: 'failed', error, fromStream: true });
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
          frame,
          { appConfig, userId, orgId, identity: callerIdentityOf(req) },
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
    streamRun.end({ kind: 'failed', error });
    if (!claim()) return;

    if (!res.headersSent) {
      res.writeHead(500, {
        'Content-Type': 'text/event-stream; charset=utf-8',
      });
    }

    // A run that never started (refused by the AI service, or the AI service out of reach) wrote nothing, so it
    // replaces nothing: the stored answer stands for everyone, and only the asker hears why. A lost lease ends as before.
    const neverRan = !runStarted && outcomeForError(error) === 'failed';
    const answerToReplace = neverRan ? null : existingConversation;
    // Nothing was replaced, so the session goes back to idle rather than failed.
    const outcome: TurnOutcome = answerToReplace
      ? outcomeForError(error)
      : 'unstarted';
    try {
      await handleRegenerationError(
        res,
        error,
        answerToReplace,
        messageId || null,
        conversationId || '',
        session,
        requestId || '',
        'regeneration_error',
        protocol,
        run,
        () => gate.settle(outcome),
        frame,
        { appConfig, userId, orgId, identity: callerIdentityOf(req) },
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
      void session.endSession();
      session = null;
    }
  }
}

export const regenerateAnswers = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
) =>
  catchAsync<AuthenticatedUserRequest>(async (req, res) => {
    if (await refuseIfDraining(res)) return;
    await regenerateAnswersInternal(appConfig, deps, req, res, {
      isAgentSession: false,
      buildQueryFilter: (conversationId, orgId) => ({
        _id: conversationId,
        orgId,
        isDeleted: false,
        ...kindFilterOf(req, EXCLUDE_AGENT),
      }),
      buildAIEndpoint: (appConfig) =>
        `${appConfig.aiBackend}/api/v1/chat/stream`,
    });
  });

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
        ...kindFilterOf(req, EXCLUDE_AGENT),
      });
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
      // conversationId is forwarded (not just runId) so Python can reject a
      // runId registered under a different conversation of the same user.
      const aiResponse = await conversationExtensions().turns.cancelRun({
        req,
        appConfig,
        conversationId: conversationId as string,
        runId: runId as string,
      });
      if (aiResponse === null) {
        res.status(HTTP_STATUS.OK).json({ cancelled: false });
        return;
      }
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
      // Readers learn of a change by `rev`, a rename too.
      conv.$inc('rev', 1);
      await conv.save(txnSession ? { session: txnSession } : {});
      return conv;
    };

    let conversation;
    if (rsAvailable) {
      session = await mongoose.startSession();
      conversation = await session.withTransaction(() =>
        applyTitleUpdate(session),
      );
    } else {
      conversation = await applyTitleUpdate(null);
    }

    const response = {
      conversation: {
        ...conversationExtensions().views.viewRunFailures(
          withoutErrorStacks(conversation.toObject()),
          userId,
        ),
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
    // A room's notes are its people's conversation with each other, not the chat being forked.
    const rowFilter: Record<string, unknown> =
      conversationContextOf(req).extended === true
        ? { messageType: { $ne: 'note' } }
        : {};
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
      rowFilter,
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
        rowFilter,
      );

    let result: ForkChatSessionResult;
    if (rsAvailable) {
      session = await mongoose.startSession();
      result = await session.withTransaction(() => performFork(session));
    } else {
      result = await performFork(null);
    }
    const fork = {
      orgId,
      userId,
      sourceId: conversationId,
      messageId,
      forkId: String(forkId),
      messages: result.messageCount,
      artifacts: artifacts.copies.size,
    };
    // Not awaited: telling an extension of the fork must never hold up or fail it.
    void Promise.resolve()
      .then(() => conversationExtensions().events.conversationForked(req, fork))
      .catch((error: unknown) => {
        logger.error('Failed to report a fork', {
          requestId,
          forkId: fork.forkId,
          error: error instanceof Error ? error.message : String(error),
        });
      });

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
 * Who may fork is the route's `fork` guard: the owner only without collaborative
 * chats, since a recipient's access to the original's attachments and artifacts is
 * revoked on unshare. A room's private and agent threads are chats of their own, so
 * a fork of the room never copies them.
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
      findSource: (orgId) =>
        ChatSession.findOne({
          _id: conversationGrantOf(req).session._id,
          orgId,
          isDeleted: false,
          isArchived: { $ne: true },
          ...kindFilterOf(req, EXCLUDE_AGENT),
        }),
    });

/** Guarded as `forkConversation` is. */
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
      findSource: (orgId) =>
        ChatSession.findOne({
          _id: conversationGrantOf(req).session._id,
          agentKey,
          orgId,
          isDeleted: false,
          ...kindFilterOf(req, ONLY_AGENT),
        }),
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
  query: Record<string, any>;
  conversationId: string;
  messageId: string;
  userId: string;
  body: FeedbackBody;
  userAgent: string | undefined;
  session?: ClientSession | null;
  /** Whether the caller sees the row; when not it is answered as missing, before anything tells what kind it is. */
  shown: (message: object) => Promise<boolean>;
  runs?: FeedbackRuns | undefined;
}): Promise<{ updatedConversation: any; feedbackEntry: any }> {
  const { query, messageId, userId, body, userAgent, session, shown, runs } =
    params;

  const conversation = await ChatSession.findOne(
    query,
    null,
    session ? { session } : undefined,
  );
  if (!conversation) {
    throw new NotFoundError('Conversation not found');
  }

  const message = await ChatSessionMessage.findOne(
    {
      _id: messageId,
      sessionId: conversation._id,
      orgId: conversation.orgId,
      ...NOT_REMOVED,
    },
    null,
    session ? { session } : undefined,
  );
  if (
    !message ||
    !(await shown(
      typeof message.toObject === 'function' ? message.toObject() : message,
    ))
  ) {
    throw new NotFoundError('Message not found');
  }

  if (message.messageType !== 'bot_response') {
    throw new BadRequestError('Feedback is only allowed for bot responses');
  }

  const orgId = String(conversation.orgId);
  const sessionId = String(conversation._id);
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
      question: () =>
        questionBefore(
          { sessionId, orgId, seq: message.seq },
          new MongoTx(session ?? undefined),
        ),
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
    session,
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

      const feedbackArgs = {
        query,
        conversationId: conversationId!,
        messageId: messageId!,
        userId: userId!,
        body: req.body as FeedbackBody,
        userAgent: req.headers['user-agent'],
        runs,
        shown: (message: object) =>
          conversationExtensions().views.rowShownTo(req, message),
      };
      let updatedConversation, feedbackEntry;
      if (rsAvailable) {
        session = await mongoose.startSession();
        ({ updatedConversation, feedbackEntry } = await session.withTransaction(
          () => performFeedbackUpdate({ ...feedbackArgs, session }),
        ));
      } else {
        ({ updatedConversation, feedbackEntry } =
          await performFeedbackUpdate(feedbackArgs));
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
    const grantedId = conversationGrantOf(req).session._id;
    const archives = conversationExtensions().archives;
    const ctx = conversationContextOf(req);

    logger.debug('Attempting to archive conversation', {
      requestId,
      message: 'Attempting to archive conversation',
      conversationId: req.params.conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performArchiveConversation(
      session?: ClientSession | null,
    ): Promise<IChatSessionDocument> {
      // Get conversation with access control
      const conversation = await ChatSession.findOne(
        {
          _id: grantedId,
          orgId,
          isDeleted: false,
          ...EXCLUDE_AGENT,
        },
        archives.projection,
      );

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no archive permission',
        );
      }

      const state = archives.decide(ctx, conversation, `${userId}`, 'archive');
      if (state.archived) {
        throw new BadRequestError('Conversation already archived');
      }

      // Perform soft delete
      const updatedConversation: IChatSessionDocument | null =
        await ChatSession.findOneAndUpdate(
          { _id: conversationId, ...EXCLUDE_AGENT },
          state.update
            ? state.update
            : {
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
      const session = await mongoose.startSession();
      session.startTransaction();
      updatedConversation = await performArchiveConversation(session);
      await session.commitTransaction();
    } else {
      updatedConversation = await performArchiveConversation();
    }
    // A chat archived for one member only (`archivedFor`) keeps its branches.
    if (updatedConversation?.isArchived === true) {
      await discardCompareBranches(orgId, conversationId);
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
  try {
    const { conversationId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const grantedId = conversationGrantOf(req).session._id;
    const archives = conversationExtensions().archives;
    const ctx = conversationContextOf(req);

    logger.debug('Attempting to unarchive conversation', {
      requestId,
      message: 'Attempting to unarchive conversation',
      conversationId: req.params.conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performUnarchiveConversation(
      session?: ClientSession | null,
    ): Promise<IChatSessionDocument> {
      // Get conversation with access control
      const conversation = await ChatSession.findOne(
        {
          _id: grantedId,
          orgId,
          isDeleted: false,
          ...EXCLUDE_AGENT,
        },
        archives.projection,
      );

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no unarchive permission',
        );
      }

      const state = archives.decide(
        ctx,
        conversation,
        `${userId}`,
        'unarchive',
      );
      if (!state.archived) {
        throw new BadRequestError('Conversation is not archived');
      }

      // Perform soft delete
      const updatedConversation: IChatSessionDocument | null =
        await ChatSession.findOneAndUpdate(
          { _id: conversationId, ...EXCLUDE_AGENT },
          state.update
            ? state.update
            : {
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
      const session = await mongoose.startSession();
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
 * Pin is the caller's own ordering of their chat list: the creator's pin is the
 * chat's `isPinned`, and anyone else's is the extension's (`pins.setOwn`), so a
 * pin never re-sorts someone else's list. Re-pinning a pinned chat (or
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
      const userId = req.user?.userId as string | undefined;
      const orgId = req.user?.orgId as string | undefined;
      if (!userId || !orgId) {
        throw new BadRequestError('User ID and Organization ID are required');
      }

      // The guard proves the caller can see the chat; this pin is its creator's.
      const grant = conversationGrantOf(req);
      const updated = await ChatSession.findOneAndUpdate(
        {
          _id: grant.session._id,
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

      if (
        !updated &&
        !(await conversationExtensions().pins.setOwn(
          conversationContextOf(req),
          grant,
          pinned,
        ))
      ) {
        throw new NotFoundError(
          'Conversation not found or not owned by caller',
        );
      }

      res.status(HTTP_STATUS.OK).json({
        id: updated?._id ?? grant.session._id,
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
      const ctx = conversationContextOf(req);
      const { filter: sessionFilter, queryConstraints } = await buildListQuery(
        req,
        {
          orgId,
          listFilter: listFilterOf(req),
          stateFilter: {
            ...listViews().archivedStateFilter(ctx),
            // A Compare models branch is opened by its id, never listed.
            ...(conversationId
              ? { _id: new mongoose.Types.ObjectId(conversationId as string) }
              : { compareOf: { $exists: false } }),
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
          .select(archiveSelect(ctx))
          .lean()
          .exec(),
        ChatSession.countDocuments(sessionFilter),
      ]);

      // Batch-fetch every message for the page in one query, grouped in
      // memory by sessionId — never a per-row query.
      const pageIds = conversations.map((c) => c._id);
      const storedMessages = await ChatSessionMessage.find({
        sessionId: { $in: pageIds },
        orgId,
        ...NOT_REMOVED,
      })
        .sort({ seq: 1 })
        .lean()
        .exec();
      const ownerBySession = new Map(
        conversations.map((c) => [String(c._id), String(c.initiator)]),
      );
      const chatOwnerBySession = new Map(
        conversations.map((c) => [String(c._id), String(c.userId)]),
      );
      // Each archived chat's rows as its detail shows them to the caller, sources included.
      const allMessages = await conversationExtensions().views.viewSources(
        await redactForViewer(
          req,
          appConfig,
          storedMessages,
          (m) => rowOwnerOf(ownerBySession.get(String(m.sessionId)))(m),
          new Map(
            conversations.map((c) => [
              String(c._id),
              { userId: String(c.userId), initiator: String(c.initiator) },
            ]),
          ),
        ),
        viewerOf(userId, orgId),
        (m) => chatOwnerBySession.get(String(m.sessionId)) ?? '',
        storedMessages,
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

      const views = await listViews().accessViews(ctx, conversations);

      // Process results with computed fields and restructured citations
      const processedConversations = conversations.map((conversation: any) => {
        const sessionMessages =
          messagesBySession.get(conversation._id.toString()) || [];
        const conversationWithMessages = attachMessages(
          conversation,
          sessionMessages,
          userId,
          { asStored: true },
        );
        const conversationWithComputedFields = addComputedFields(
          listViews().redactRecipients(
            ctx,
            conversationWithMessages,
          ) as IConversation,
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

      const ctx = conversationContextOf(req);
      const orgOid = new mongoose.Types.ObjectId(`${orgId}`);
      const userOid = new mongoose.Types.ObjectId(`${userId}`);

      const stateFilter = {
        ...listViews().archivedStateFilter(ctx),
        // A Compare models branch is opened by its id, never listed.
        compareOf: { $exists: false },
      };
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
        listViews().ownArchivedClause(ctx),
      );
      const contentMatchIds = await findSessionIdsMatchingContent(
        orgId,
        escapedSearch,
        {
          ownerId: String(userId),
          sessionFilter: { $or: [assistantScope, agentScope] },
        },
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
        listViews().ownArchivedClause(ctx),
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
                $cond: [
                  { $eq: ['$sessionType', 'agent'] },
                  'agent',
                  'assistant',
                ],
              },
            },
          },
          { $sort: { lastActivityAt: -1 } },
          { $skip: skip },
          { $limit: limit },
          { $project: archiveProject(req, ['source']) },
        ]),
        ChatSession.countDocuments(assistantFilter),
        ChatSession.countDocuments(agentFilter),
      ]);

      const totalCount = assistantCount + agentCount;

      // ── Tag and apply computed fields (bounded to `limit` docs) ──────
      const views = await listViews().accessViews(ctx, aggregateResult);
      const paginatedResults = aggregateResult.map((c: any) => ({
        ...addComputedFields(
          listViews().redactRecipients(ctx, c) as IConversation,
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

/** Creates an agent. */
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
        throw (
          mapAgentHandleError(aiResponse) ??
          handleBackendError(aiResponse, 'Create Agent')
        );
      }
      const agent = aiResponse.data;
      conversationExtensions().agents.changed(
        callerIdentityOf(req),
        (agent as { agent?: { _key?: string } } | undefined)?.agent?._key,
      );
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
        throw (
          mapAgentHandleError(aiResponse) ??
          handleBackendError(aiResponse, 'Update Agent')
        );
      }
      const agent = aiResponse.data;
      conversationExtensions().agents.changed(callerIdentityOf(req), agentKey);
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
      conversationExtensions().agents.changed(callerIdentityOf(req), agentKey);
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

export const streamAgentConversation = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): ReturnType<typeof firstSendStream> =>
  firstSendStream(appConfig, deps, 'agent');

export const streamAgentConversationInternal = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  keyValueStoreService?: KeyValueStoreService,
): ReturnType<typeof asInternal> =>
  asInternal(
    streamAgentConversation(appConfig, deps),
    appConfig,
    keyValueStoreService,
  );

export const addMessageStreamToAgentConversation = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): ReturnType<typeof followUpStream> =>
  followUpStream(appConfig, deps, 'agent');

export const addMessageStreamToAgentConversationInternal = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  keyValueStoreService?: KeyValueStoreService,
): ReturnType<typeof asInternal> =>
  asInternal(
    addMessageStreamToAgentConversation(appConfig, deps),
    appConfig,
    keyValueStoreService,
  );

export const regenerateAgentAnswers = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
) =>
  catchAsync<AuthenticatedUserRequest>(async (req, res) => {
    if (await refuseIfDraining(res)) return;
    await regenerateAnswersInternal(appConfig, deps, req, res, {
      isAgentSession: true,
      buildQueryFilter: (_conversationId, orgId, _userId, agentKey) => ({
        _id: conversationGrantOf(req).session._id,
        agentKey,
        orgId,
        isDeleted: false,
        ...kindFilterOf(req, ONLY_AGENT),
      }),
      buildAIEndpoint: (appConfig, agentKey) =>
        `${appConfig.aiBackend}/api/v1/agent/${encodeURIComponent(agentKey as string)}/chat/stream`,
    });
  });

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
      const aiResponse = await conversationExtensions().turns.cancelRun({
        req,
        appConfig,
        conversationId: String(grantedId),
        runId: runId as string,
      });
      if (aiResponse === null) {
        res.status(HTTP_STATUS.OK).json({ cancelled: false });
        return;
      }
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
    const ctx = conversationContextOf(req);
    const { filter, queryConstraints } = await buildListQuery(req, {
      orgId,
      listFilter: listFilterOf(req),
      stateFilter: {
        // Sidebar / chat list: omit archived threads (archived view uses a dedicated route).
        isArchived: { $ne: true },
        // A Compare models branch is opened by its id, never listed.
        ...(conversationId
          ? { _id: new mongoose.Types.ObjectId(conversationId) }
          : { compareOf: { $exists: false } }),
      },
      formatSpecifiers: true,
    });
    const sortOptions = buildSortOptions(req);

    // sharedWith Me Conversation
    const sharedWithMeFilter = composeListFilter(sharedListFilterOf(req), {
      isArchived: { $ne: true },
      ...(typeof req.query.status === 'string' && {
        status: req.query.status,
      }),
    });

    // Execute query
    const [rows, totalCount, sharedWithMeConversations] = await Promise.all([
      ChatSession.find(filter)
        .sort(sortOptions as any)
        .skip(skip)
        .limit(limit + 1)
        .select(listViews().select(ctx))
        .lean()
        .exec(),
      ChatSession.countDocuments(filter, { limit: LIST_COUNT_CAP }),
      ChatSession.find(sharedWithMeFilter)
        .sort(sortOptions as any)
        .skip(skip)
        .limit(limit)
        .select(listViews().select(ctx))
        .lean()
        .exec(),
    ]);
    const conversations = rows.slice(0, limit);

    const views = await listViews().accessViews(ctx, [
      ...conversations,
      ...sharedWithMeConversations,
    ]);
    const decorate = await listViews().rowDecorator(ctx, [
      ...conversations,
      ...sharedWithMeConversations,
    ]);
    const processedConversations = conversations.map((conversation: any) =>
      decorate(
        addComputedFields(
          // A project member sees another user's project-visible chat here; who it was shared
          // with is the owner's business.
          listViews().redactRecipients(
            ctx,
            String(conversation.initiator) === String(userId)
              ? conversation
              : withoutSharedWith(conversation),
          ) as IAgentConversation,
          userId,
          views.get(String(conversation._id)),
        ),
      ),
    );
    const processedSharedWithMeConversations = await attachSharedBy(
      sharedWithMeConversations.map((sharedWithMeConversation: any) =>
        decorate(
          addComputedFields(
            withoutSharedWith(sharedWithMeConversation) as IAgentConversation,
            userId,
            views.get(String(sharedWithMeConversation._id)),
          ),
        ),
      ),
      orgId as string,
    );

    // Build response metadata
    const response = {
      conversations: processedConversations,
      sharedWithMeConversations: processedSharedWithMeConversations,
      pagination: {
        ...buildPaginationMetadata(totalCount, page, limit),
        hasNextPage: rows.length > limit,
      },
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

export const getAgentConversationById =
  (appConfig: AppConfig, checks: ViewerChecks = {}) =>
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
        ...kindFilterOf(req, ONLY_AGENT),
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
          // The newest saved failure tells whose run `failReason` is from; the response never carries it.
          conversationErrors: { $slice: -1 },
          // Who asked the run that failed, so `Failed` is said to them alone; never sent.
          failedRunBy: 1,
          modelInfo: 1,
          projectId: 1,
          projectVisibility: 1,
          // Where a fork came from, for `resolveForkOrigin` / `resolveForks`.
          metadata: 1,
          // Read before the rows, for the same reason as in getConversationById.
          rev: 1,
        })
        .lean()
        .exec();

      if (!session) {
        throw new NotFoundError('Conversation not found');
      }

      const sessionId = session._id;
      const harness = await harnessSessionView(
        String(sessionId),
        String(session.orgId),
      );

      const totalMessages = await ChatSessionMessage.countDocuments({
        sessionId,
        orgId,
        ...NOT_REMOVED,
      });

      const { skip, limit: effectiveLimit } = olderMessagesWindow(
        totalMessages,
        page,
        limit,
      );

      // A fork written before forks detached their copies names the original's runs (FORKFIX).
      const messages = await withForeignRunsDetached(
        { _id: String(sessionId), metadata: harness.metadata },
        String(orgId),
        await getMessages(sessionId, {
          orgId,
          skip,
          limit: effectiveLimit,
          populateCitations: true,
        }),
      );

      // A recipient may be on the conversation only through a share, so what the owner
      // created since sharing is granted as they read.
      const initiatorId = session.initiator?.toString() ?? '';
      await conversationExtensions().content.viewed({
        appConfig,
        conversationId: String(sessionId),
        ownerId: initiatorId,
        viewerId: String(userId),
        orgId: String(orgId),
        page,
        messages,
        requestId,
      });

      const conversationWithMessages = attachMessages(
        session,
        messages,
        userId,
        {
          asStored: true,
        },
      );

      // Build conversation response using existing helper
      // Whose read it is, and whether the harness's citation check could be made (set by withViewerChecks).
      const viewerRead = {
        orgId: String(orgId),
        sessionId: String(sessionId),
        checkFailed: false,
      };
      const baseResponse = await withViewerChecks(
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
          conversationWithMessages?.messages || [],
          accessViewOf(req),
        ),
        req,
        checks,
        viewerRead,
      );

      // The recipient list is the owner's business: a viewer is told who shared
      // the chat, never who else can read it.
      const sharedResponse = await attachSharedByIfRecipient(
        initiatorId === String(userId)
          ? baseResponse
          : { ...baseResponse, sharedWith: undefined },
        orgId as unknown as string,
      );
      const conversationResponse =
        await conversationExtensions().details.decorate(
          req,
          { ...sharedResponse, rev: session.rev ?? 0 },
          messages,
          {
            users: defaultUsers,
            ownerId: conversationGrantOf(req).session.userId.toString(),
          },
        );
      // Hidden in stored order, then sorted as the client asked: a turn's tool rows follow their
      // answer, and `sortBy` must not separate them first.
      const shown = await redactPageForViewer(
        req,
        appConfig,
        {
          _id: String(sessionId),
          orgId: String(session.orgId),
          initiator: initiatorId,
        },
        conversationResponse.messages,
        skip,
        totalMessages,
        rowOwnerOf(initiatorId),
        checks.citations?.checksRecords() === true,
        viewerRead.checkFailed,
      );
      const redacted = conversationExtensions().details.afterReaderCheck(
        conversationResponse.messages,
        shown,
      );
      conversationResponse.messages = sortMessages(
        redacted as unknown as IMessageDocument[],
        messageSortOptions as { field: keyof IMessage },
      ) as unknown as typeof redacted;

      // Build filters metadata using existing helper
      const filtersMetadata = buildFiltersMetadata(
        messageFilter,
        query,
        messageSortOptions,
      );

      const forkView = {
        _id: String(sessionId),
        orgId: String(session.orgId),
        metadata: session.metadata,
        agentKey: agentKey as string,
      };
      const [forkedFrom, forks] = await Promise.all([
        resolveForkOrigin(forkView, String(userId)),
        resolveForks(forkView, String(userId)),
      ]);

      // Prepare response using existing format
      const response = {
        conversation: {
          ...conversationResponse,
          ...(forkedFrom && { forkedFrom }),
          ...(forks.length > 0 && { forks }),
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

    const conversation = await deleteAgentConversation(
      conversationGrantOf(req).session._id,
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
      await conversationExtensions().events.conversationDeleted(
        req,
        conversationGrantOf(req),
      );
    }

    res.status(200).json({
      message: 'Conversation deleted successfully',
      conversation:
        conversation &&
        conversationExtensions().views.viewRunFailures(
          withoutErasureMarker(withoutErrorStacks(conversation.toJSON())),
          userId,
        ),
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
  let session: ClientSession | null = null;
  try {
    const { conversationId, agentKey } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const grantedId = conversationGrantOf(req).session._id;
    const archives = conversationExtensions().archives;
    const ctx = conversationContextOf(req);

    logger.debug('Attempting to archive agent conversation', {
      requestId,
      message: 'Attempting to archive agent conversation',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performArchive(
      s?: ClientSession | null,
    ): Promise<IChatSessionDocument> {
      const conversation = await ChatSession.findOne(
        {
          _id: grantedId,
          orgId,
          agentKey,
          isDeleted: false,
          ...ONLY_AGENT,
        },
        archives.projection,
        { session: s },
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      const state = archives.decide(ctx, conversation, `${userId}`, 'archive');
      if (state.archived) {
        throw new BadRequestError('Agent conversation already archived');
      }

      const updated = await ChatSession.findOneAndUpdate(
        { _id: conversationId, ...ONLY_AGENT },
        state.update
          ? state.update
          : {
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
    const archives = conversationExtensions().archives;
    const ctx = conversationContextOf(req);

    logger.debug('Attempting to unarchive agent conversation', {
      requestId,
      message: 'Attempting to unarchive agent conversation',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performUnarchive(
      s?: ClientSession | null,
    ): Promise<IChatSessionDocument> {
      const conversation = await ChatSession.findOne(
        {
          _id: grantedId,
          orgId,
          agentKey,
          isDeleted: false,
          ...ONLY_AGENT,
        },
        archives.projection,
        { session: s },
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      const state = archives.decide(
        ctx,
        conversation,
        `${userId}`,
        'unarchive',
      );
      if (!state.archived) {
        throw new BadRequestError('Agent conversation is not archived');
      }

      const updated = await ChatSession.findOneAndUpdate(
        { _id: conversationId, ...ONLY_AGENT },
        state.update
          ? state.update
          : {
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
      const ctx = conversationContextOf(req);
      const { filter, queryConstraints } = await buildListQuery(req, {
        orgId,
        listFilter: listFilterOf(req),
        stateFilter: {
          ...listViews().archivedStateFilter(ctx),
          // A Compare models branch is opened by its id, never listed.
          compareOf: { $exists: false },
        },
        formatSpecifiers: true,
      });
      const sortOptions = buildSortOptions(req);

      const [conversations, totalCount] = await Promise.all([
        ChatSession.find(filter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select(archiveSelect(ctx))
          .lean()
          .exec(),
        ChatSession.countDocuments(filter),
      ]);

      const views = await listViews().accessViews(ctx, conversations);
      // A shared agent chat lists here for its members too: who else is in it is its owner's to see, as on every list.
      const processedConversations = conversations.map((conversation: any) => ({
        ...addComputedFields(
          listViews().redactRecipients(ctx, conversation) as IAgentConversation,
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

    const title = req.body.title?.trim();
    if (!title) {
      throw new BadRequestError('Title is required and cannot be empty');
    }

    // One `$set`: a load-and-save would write back the defaults Mongoose filled in on load (`rev`, `aclVersion`)
    // over a concurrent bump.
    const conversation = await ChatSession.findOneAndUpdate(
      {
        _id: conversationGrantOf(req).session._id,
        orgId,
        agentKey,
        isDeleted: false,
        ...ONLY_AGENT,
      },
      // Readers learn of a change by `rev`, a rename too.
      { $set: { title }, $inc: { rev: 1 } },
      { returnDocument: 'after' },
    );

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
        ...conversationExtensions().views.viewRunFailures(
          withoutErrorStacks(conversation.toObject()),
          userId,
        ),
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
        ...kindFilterOf(req, ONLY_AGENT),
      };

      const feedbackArgs = {
        query,
        conversationId: conversationId!,
        messageId: messageId!,
        userId: userId!,
        body: req.body as FeedbackBody,
        userAgent: req.headers['user-agent'],
        runs,
        shown: (message: object) =>
          conversationExtensions().views.rowShownTo(req, message),
      };
      let updatedConversation, feedbackEntry;
      if (rsAvailable) {
        session = await mongoose.startSession();
        ({ updatedConversation, feedbackEntry } = await session.withTransaction(
          () => performFeedbackUpdate({ ...feedbackArgs, session }),
        ));
      } else {
        ({ updatedConversation, feedbackEntry } =
          await performFeedbackUpdate(feedbackArgs));
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

      // No agentKey on this route, which the shared access filter requires, so it stays owner-only.
      const ctx = conversationContextOf(req);
      const ownedArchives = {
        orgId: new mongoose.Types.ObjectId(`${orgId}`),
        userId: new mongoose.Types.ObjectId(`${userId}`),
        ...listViews().archivedStateFilter(ctx),
        isDeleted: false,
        ...ONLY_AGENT,
        ...(deletedAgentKeys !== null && {
          agentKey: { $nin: deletedAgentKeys },
        }),
      };
      const perUserArchives = listViews().ownArchivedClause(ctx);
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
          // Only a row's fields go into the groups (no embedded messages of an older chat, nothing `select: false`).
          { $project: archiveProject(req, [], { messages: 0 }) },
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

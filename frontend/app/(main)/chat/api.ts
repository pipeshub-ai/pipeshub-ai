import { apiClient, streamSSERequest, type SSEEvent } from '@/lib/api';
import { onSessionEnd } from '@/lib/api/session-cache';
import { OrgApi, type OrgResponse } from '@/app/(main)/workspace/general/api';
import { CHAT_STREAM_ERROR_MESSAGES, StreamError } from '@/lib/api/stream-errors';
import { CONVERSATION_MESSAGES_PAGE_SIZE } from './constants';
import { PROJECT_ID_UNASSIGNED } from './project-types';
import {
  Conversation,
  ConversationMessage,
  ConversationPagination,
  ForkChild,
  ForkOrigin,
  ConversationsListResponse,
  ConversationApiResponse,
  ConversationSource,
  ModelInfo,
  SharedWithEntry,
  StreamChatRequest,
  AgentStrategyApiSegment,
  SSEConnectedEvent,
  SSEStatusEvent,
  SSEAnswerChunkEvent,
  SSECompleteEvent,
  type AgentCapabilities,
  SSEArtifactEvent,
  SSEAskUserQuestionEvent,
  SSEApprovalRequestedEvent,
  SSEApprovalResolvedEvent,
  SSEInteractionWaitingEvent,
  SSEPlanEvent,
  ApprovalDecision,
  MessagePart,
  AvailableLlmModel,
  SearchRequest,
  SearchResponse,
  streamChatModeToAgentApiChatMode,
  AttachmentRef,
  DurableAgentRunHandle,
  type ChatKnowledgeFilters,
  CHAT_ATTACHMENT_UPLOAD_TIMEOUT_MS,
} from './types';
import { hasAnyFilter } from './utils/tree-selection';
import { getClientTimezone, getClientCurrentTime } from './utils/client-time';
import { createAGUIEventHandler, type AGUIEventHandler, type AGUIStreamTracking } from './agui-event-handler';
import { durableStreamLog, followDurableRun, isRootTerminalFrame } from './durable-run-stream';
import { DurableAdmissionError } from './harness-errors';
import { sendFailureOf, type SendFailure } from './card-send';
import { MAX_DENY_REASON_CHARS } from './approval-preview';
import { countedApprovals, type CountedApprovals } from './interactions';
import type { ActivityFollowup, ActivityStepDetail, ConversationUsage, DecisionAuthority, ResolvedRunView, RunCostView, RunTimelineView, SandboxCommandRequest, SessionSandboxView, WorkspaceDiff, WorkspaceFileList, WorkspaceFileRead, WorkspaceVersions } from './types';
import { toFollowups, toStepDetail } from './activity';
import type {
  AgentHarnessRun,
  AgentRunAdmission,
  AgentRunAdmissionInput,
  AgentRunControlReceipt,
} from '@/app/(main)/agents/types';

/** The key a model choice is matched by across the picker's list and the model access answer. */
export const modelChoiceKey = (modelKey: string, modelName: string): string => `${modelKey}::${modelName}`;

/** G-41: the model keys the org limits to groups the person is in none of; anything malformed counts as allowed. */
export function groupBlocked(choices: unknown): Set<string> {
  const blocked = new Set<string>();
  if (!Array.isArray(choices)) return blocked;
  for (const choice of choices) {
    if (typeof choice !== 'object' || choice === null) continue;
    const { modelKey, groupAllowed } = choice as Record<string, unknown>;
    if (typeof modelKey === 'string' && groupAllowed === false) blocked.add(modelKey);
  }
  return blocked;
}

/** The picker's unavailable models: by group (`modelKey`), and by the edition's reasons (translation key by `modelChoiceKey`). */
export interface UnavailableModels {
  group: Set<string>;
  reasons: ReadonlyMap<string, string>;
}

export interface FeedbackPayload {
  isHelpful: boolean;
  categories?: string[];
  comments?: {
    positive?: string;
    negative?: string;
  };
}

export interface ForkConversationResult {
  id: string;
  title: string;
  projectId?: string;
  forkedFrom: {
    conversationId: string;
    messageId: string;
  };
  messageCount: number;
}

export interface StreamMessageCallbacks {
  /** Called after durable admission so an explicit UI stop can cancel it. */
  onDurableRunAccepted?: (run: DurableAgentRunHandle) => void;
  /** A chat-route turn's first frame came from today's loop, not a durable run (LAT1). */
  onLegacyTurn?: () => void;
  onConnected?: (data: SSEConnectedEvent) => void;
  onStatus?: (data: SSEStatusEvent) => void;
  onChunk?: (data: SSEAnswerChunkEvent) => void;
  onComplete?: (data: SSECompleteEvent) => void;
  onArtifact?: (data: SSEArtifactEvent) => void;
  /** Backend is discarding partial output (citation verify / re-parse) — clear UI buffer */
  onRestreaming?: () => void;
  onAskUserQuestion?: (data: SSEAskUserQuestionEvent) => void;
  /** A durable run holds a tool call for the user's approval (AG-UI protocol only). */
  onApprovalRequested?: (data: SSEApprovalRequestedEvent) => void;
  onApprovalResolved?: (data: SSEApprovalResolvedEvent) => void;
  /** WP-100: one more approval counted on an ask that needs several and still waits (counts only, AG-UI only). */
  onApprovalCounted?: (data: { interactionId: string; approvals: CountedApprovals }) => void;
  /** WP-100 (C10.05): the run's spend as the ledger moved (`run_cost`, AG-UI only). */
  onRunCost?: (value: unknown) => void;
  /** How many of the run's approvals wait, and whether it is paused on them (UXINT-5, AG-UI only). */
  onInteractionWaiting?: (data: SSEInteractionWaitingEvent) => void;
  /** The root run's whole plan after it changed (UXR-B1, AG-UI protocol only). */
  onPlan?: (data: SSEPlanEvent) => void;
  /**
   * The answer is settled and only persistence remains (AG-UI protocol only —
   * `AnswerFinalizer`'s `snapshot.final` STATE_SNAPSHOT, which reaches us
   * ahead of the RUN_FINISHED Node withholds while it writes to Mongo).
   * Implementations use it to stop showing progress indicators.
   */
  onAnswerFinal?: () => void;
  /**
   * Live chain-of-thought delta (AG-UI protocol only — see `agui-event-handler.ts`).
   * `done: true` marks the end of one reasoning turn; no legacy equivalent exists,
   * so implementations are free to no-op this until a "thinking" panel is built.
   */
  onReasoning?: (data: { delta: string; done: boolean }) => void;
  /**
   * Live agent-activity transcript snapshot (AG-UI protocol only — see
   * `agui-event-handler.ts`). Called with the FULL parts array (not a
   * delta) every time it changes, so implementations can just assign it
   * straight into slot state. No legacy equivalent; safe to no-op.
   */
  onParts?: (parts: MessagePart[]) => void;
  /** Compare mode: the question is saved and its id known, so the branch for the second model can start. */
  onTurnStarted?: (data: { conversationId?: string; userMessageId?: string }) => void;
  onError?: (error: Error) => void;
  signal?: AbortSignal;
}

/** The durable run a chat route took this turn to, from its first frame (LAT1); null on today's loop. */
function durableHandle(event: SSEEvent, agentId: string | undefined): DurableAgentRunHandle | null {
  const data = event.data as { type?: unknown; name?: unknown; value?: { conversationId?: unknown; runId?: unknown } };
  if ((data?.type ?? event.event) !== 'CUSTOM' || data?.name !== 'conversation_created') return null;
  const { conversationId, runId } = data.value ?? {};
  if (typeof conversationId !== 'string' || typeof runId !== 'string') return null;
  return { ...(agentId ? { agentId } : {}), sessionId: conversationId, runId };
}

/** A denial's reason as the decision routes take it: trimmed, only with a denial, never blank. */
function denialReason(decision: string, reason: string | undefined): { reason?: string } {
  const text = reason?.trim().slice(0, MAX_DENY_REASON_CHARS);
  return decision === 'denied' && text ? { reason: text } : {};
}

/** A refusal of the turn, in the reader's language when the harness named it (UX3's texts). */
function turnError(error: Error): Error {
  return error instanceof StreamError && error.code?.startsWith('HTTP_AGENT_HARNESS_')
    ? new DurableAdmissionError(error.code, error.reasonCode ?? null, error.status)
    : error;
}

/**
 * One chat turn on the existing chat route, in one request (LAT1): the server
 * admits and streams it on the durable harness, or answers it on today's
 * loop, on this same response. A durable turn whose connection drops before
 * the run ends is followed with `GET …/runs/:id/stream` from the last frame,
 * by the same handler. The body's `runId` names the turn, so the server never
 * admits it twice.
 */
async function runTurnStream(
  endpoint: string,
  payload: Record<string, unknown>,
  agentId: string | undefined,
  callbacks: StreamMessageCallbacks,
): Promise<void> {
  const tracking: AGUIStreamTracking = { receivedComplete: false };
  let handler: AGUIEventHandler | null = null;
  let target: DurableAgentRunHandle | null = null;
  let lastEventId: string | null = null;
  let finished = false;
  let failure: Error | null = null;
  await streamSSERequest(endpoint, { ...payload, protocol: 'agui' }, {
    onEvent: (event) => {
      if (handler === null) {
        target = durableHandle(event, agentId);
        handler = createAGUIEventHandler(callbacks, tracking, target ? { replaceRestartedMessages: true } : {});
        if (target) {
          durableStreamLog.info('transport.one_request', { sessionId: target.sessionId, runId: target.runId });
          callbacks.onDurableRunAccepted?.(target);
        } else {
          handler(event);
          callbacks.onLegacyTurn?.();
          return;
        }
      }
      handler(event);
      if (event.id) lastEventId = event.id;
      if (isRootTerminalFrame(event)) finished = true;
    },
    onError: (error) => {
      failure = error;
    },
    signal: callbacks.signal,
  });
  if (callbacks.signal?.aborted) return;
  const run = target as DurableAgentRunHandle | null;
  if (run !== null && handler !== null && !finished) {
    await followDurableRun(run, callbacks, { handler, tracking, lastEventId });
    return;
  }
  const failed = failure as Error | null;
  if (failed !== null) {
    callbacks.onError?.(turnError(failed));
    return;
  }
  // A connection that closes cleanly without a terminal frame would otherwise
  // leave the turn "streaming" forever, with Stop showing and no answer.
  if (!tracking.receivedComplete && !tracking.receivedError) {
    callbacks.onError?.(new Error(CHAT_STREAM_ERROR_MESSAGES.interrupted));
  }
}

function newIdempotencyKey(): string {
  const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  return cryptoApi && typeof cryptoApi.randomUUID === 'function'
    ? cryptoApi.randomUUID()
    : `agent-run-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

/**
 * The durable admission body for a turn, from the request its stream would
 * send: an agent's filters always carry both lists (none means "no knowledge
 * scope"), a plain chat's only when set, as the legacy bodies do.
 */
function durableAdmissionInput(
  request: StreamChatRequest,
): Omit<AgentRunAdmissionInput, 'idempotencyKey'> {
  const filters: Record<string, unknown> = request.agentId
    ? { ...buildAgentFiltersPayload(request.filters).filters }
    : ((buildFiltersPayload(request.filters).filters as Record<string, unknown> | undefined) ?? {});
  return {
    query: request.query,
    filters,
    ...(request.appliedFilters
      ? { appliedFilters: request.appliedFilters as unknown as Record<string, unknown> }
      : {}),
    ...(request.agentStreamTools !== undefined ? { tools: request.agentStreamTools } : {}),
    ...(request.agentCapabilities ? { agentCapabilities: request.agentCapabilities } : {}),
    ...(request.attachments?.length ? { attachments: request.attachments } : {}),
    modelKey: request.modelKey,
    modelName: request.modelName,
    modelFriendlyName: request.modelFriendlyName || request.modelName,
    ...(request.reasoningEffort ? { reasoningEffort: request.reasoningEffort } : {}),
    chatMode: request.agentId ? streamChatModeToAgentApiChatMode(request.chatMode) : request.chatMode,
    timezone: getClientTimezone(),
    currentTime: getClientCurrentTime(),
  };
}

/** Where a durable turn is admitted: a new session, or the session it continues. */
function durableAdmissionUrl(agentId: string | undefined, conversationId: string | undefined | null): string {
  const base = agentId ? `/api/v1/agents/${encodeURIComponent(agentId)}` : '/api/v1/conversations';
  if (!conversationId) return `${base}/runs`;
  return agentId
    ? `${base}/conversations/${encodeURIComponent(conversationId)}/runs`
    : `${base}/${encodeURIComponent(conversationId)}/runs`;
}

/** A durable session's routes: an agent's conversation or a built-in chat. */
export function durableSessionBase(agentId: string | undefined, sessionId: string): string {
  return agentId
    ? `/api/v1/agents/${encodeURIComponent(agentId)}/conversations/${encodeURIComponent(sessionId)}`
    : `/api/v1/conversations/${encodeURIComponent(sessionId)}`;
}

/** The harness's machine code of a refused request (`HTTP_AGENT_HARNESS_…`), if it has one. */
function errorCode(error: unknown): string | null {
  const original = (error as { originalError?: { response?: { data?: unknown } } } | null)?.originalError;
  const body = original?.response?.data as { error?: { code?: unknown } } | undefined;
  return typeof body?.error?.code === 'string' ? body.error.code : null;
}

/** What became of a message sent to a running task (steering, P1b T2). */
export type SteerOutcome =
  | { status: 'queued'; messageSeq: number }
  /** Someone else's task is running (a shared conversation): the message starts as its own run after it. */
  | { status: 'next_run'; runId: string; position: number }
  /** The task ended meanwhile: send the message as a new turn. */
  | { status: 'not_active' }
  | { status: 'refused'; code: string | null };

/** The HTTP status of a rejected request; undefined when no answer came (network, timeout). */
export function statusOf(error: unknown): number | undefined {
  const processed = error as { statusCode?: unknown; originalError?: { response?: { status?: unknown } } } | null;
  const status = processed?.statusCode ?? processed?.originalError?.response?.status;
  return typeof status === 'number' ? status : undefined;
}

export type { CountedApprovals };

interface DecisionResponse {
  interaction?: { interactionId: string; decision: string; approvals?: CountedApprovals };
  interactions?: Array<{ interactionId: string; decision: string; approvals?: CountedApprovals }>;
}

/** A decision the server refused or that got no answer: its code, and whether the card retries it (CARDFAIL). */
export interface DecisionRefusal {
  refused: string;
  failure?: SendFailure;
}

/**
 * Posts a decision once. The card's send (`sendCard`) retries a transient failure; the idempotency key makes a
 * retry replay the first decision rather than decide twice. Resolves to the refusal, or the server's answer.
 */
async function postDecision(
  url: string,
  body: Record<string, unknown>,
  log: Record<string, unknown>,
): Promise<DecisionRefusal | { data: DecisionResponse }> {
  try {
    // The card says why a decision was refused; no second, generic toast.
    const { data } = await apiClient.post<DecisionResponse>(url, body, { suppressErrorToast: true });
    return { data: data ?? {} };
  } catch (error) {
    const code = errorCode(error);
    const failure = sendFailureOf(statusOf(error), code);
    durableStreamLog.warn('approval.refused', { ...log, code, status: statusOf(error) ?? null, retry: failure.retry });
    return { refused: code ?? 'HTTP_AGENT_HARNESS_DECISION_FAILED', failure };
  }
}

/** Map GET /conversations (or agent conversations) row → sidebar `Conversation` */
export function mapApiConversationToConversation(conv: ConversationApiResponse): Conversation {
  return {
    id: conv._id,
    title: conv.title,
    createdAt: conv.createdAt,
    updatedAt: conv.updatedAt,
    isShared: conv.isShared,
    sharedWith: conv.sharedWith ?? [],
    lastActivityAt: conv.lastActivityAt,
    status: conv.status,
    modelInfo: conv.modelInfo,
    isOwner: conv.isOwner,
    ...(conv.isPinned === true ? { isPinned: true } : {}),
    // Dropping these made every row look project-less: Move to project
    // pre-selected "No project" and "Remove from project" never showed.
    ...(conv.projectId ? { projectId: conv.projectId } : {}),
    ...(conv.projectVisibility ? { projectVisibility: conv.projectVisibility } : {}),
    sharedBy: conv.sharedBy,
    agentKey: conv.agentKey,
    ...(conv.harnessState ? { harnessState: conv.harnessState } : {}),
  };
}

const transformConversation = mapApiConversationToConversation;

export interface FetchConversationsOptions {
  /** 'owned' fetches the user's own chats; 'shared' fetches chats shared with them. */
  source: ConversationSource;
  /** Passed to GET /api/v1/conversations?search= — server matches title and message content */
  search?: string;
  /** Leave out chats that belong to a project; they are listed under their project instead. */
  excludeProjectChats?: boolean;
  signal?: AbortSignal;
  /** For callers that show the failure themselves. */
  suppressErrorToast?: boolean;
}

/**
 * Sanitise an array of filter IDs: keep only non-blank strings.
 */
function sanitizeFilterIds(ids: unknown[] | null | undefined): string[] {
  return (ids ?? []).filter(
    (id): id is string => typeof id === 'string' && id.trim().length > 0,
  );
}

/**
 * The `filters` object as sent: `apps` and `kb` always, and the keys that
 * select below app level (record groups, folders, records) when not empty.
 */
function sanitizeFilters(filters: Partial<ChatKnowledgeFilters> | null | undefined): ChatKnowledgeFilters {
  const recordGroups = sanitizeFilterIds(filters?.recordGroups);
  const records = sanitizeFilterIds(filters?.records);
  const recordsExact = sanitizeFilterIds(filters?.recordsExact);
  return {
    apps: sanitizeFilterIds(filters?.apps),
    kb: sanitizeFilterIds(filters?.kb),
    ...(recordGroups.length > 0 ? { recordGroups } : {}),
    ...(records.length > 0 ? { records } : {}),
    ...(recordsExact.length > 0 ? { recordsExact } : {}),
  };
}

/**
 * Normalise the filters and return a `{ filters }` spread fragment for
 * **non-agent** (assistant) streams.
 *
 * Only emits the key when at least one valid ID remains — so the backend
 * treats the absence of `filters` as "search everything" (correct for the
 * assistant endpoint where no scope is intentional).
 */
function buildFiltersPayload(
  filters: Partial<ChatKnowledgeFilters> | null | undefined,
): Record<string, unknown> {
  const valid = sanitizeFilters(filters);
  return hasAnyFilter(valid) ? { filters: valid } : {};
}

/**
 * Build `{ filters }` for **agent** streams.
 *
 * Always emits the key — even when both arrays are empty — because the agent
 * endpoint interprets a missing `filters` key as "all knowledge," whereas
 * `{ apps: [], kb: [] }` means "this agent has no knowledge scope."
 */
function buildAgentFiltersPayload(
  filters: Partial<ChatKnowledgeFilters> | null | undefined,
): { filters: ChatKnowledgeFilters } {
  return { filters: sanitizeFilters(filters) };
}

// Chat API endpoints
/**
 * With no model resolved the store hands back empty strings. The API rejects
 * an empty model field but picks the workspace default when it is missing, so
 * only non-empty values are sent.
 */
function buildModelPayload(model: {
  modelKey?: string;
  modelName?: string;
  modelFriendlyName?: string;
}): Record<string, string> {
  const modelKey = model.modelKey?.trim();
  const modelName = model.modelName?.trim();
  const modelFriendlyName = model.modelFriendlyName?.trim() || modelName;
  return {
    ...(modelKey ? { modelKey } : {}),
    ...(modelName ? { modelName } : {}),
    ...(modelFriendlyName ? { modelFriendlyName } : {}),
  };
}

export const ChatApi = {
  /**
   * Sends `request`'s message to the session's running task (steering):
   * the server adds it to that run, which reads it at its next step. It
   * never starts a run (`steer: true`); `idempotencyKey` makes a retry of
   * the same send the same message.
   */
  async steerDurableRun(
    request: StreamChatRequest,
    run: DurableAgentRunHandle,
    idempotencyKey: string = newIdempotencyKey(),
  ): Promise<SteerOutcome> {
    const body: AgentRunAdmissionInput = {
      ...durableAdmissionInput(request),
      idempotencyKey,
      steer: true,
    };
    try {
      const { data } = await apiClient.post<{ admission: AgentRunAdmission | null }>(
        durableAdmissionUrl(run.agentId, run.sessionId),
        body,
        // The caller says what happened to the message; no generic error toast.
        { suppressErrorToast: true },
      );
      const nextRun = data.admission?.nextRun;
      if (nextRun !== undefined) {
        durableStreamLog.info('steer.next_run', { sessionId: run.sessionId, runId: run.runId, position: nextRun.position });
        return { status: 'next_run', runId: nextRun.runId, position: nextRun.position };
      }
      const queued = data.admission?.queued;
      if (queued === undefined) {
        // The rollout sent the session's turns back to the legacy stream.
        return { status: 'refused', code: null };
      }
      durableStreamLog.info('steer.queued', { sessionId: run.sessionId, runId: run.runId, messageSeq: queued.messageSeq });
      return { status: 'queued', messageSeq: queued.messageSeq };
    } catch (error) {
      const code = errorCode(error);
      durableStreamLog.warn('steer.refused', { sessionId: run.sessionId, runId: run.runId, code });
      return code === 'HTTP_AGENT_HARNESS_RUN_NOT_ACTIVE'
        ? { status: 'not_active' }
        : { status: 'refused', code };
    }
  },

  /** Reattach a fresh browser projection to a run that continued server-side. */
  async resumeDurableAgentRun(
    agentId: string | undefined,
    sessionId: string,
    runId: string,
    callbacks: StreamMessageCallbacks,
  ): Promise<void> {
    const target: DurableAgentRunHandle = { ...(agentId ? { agentId } : {}), sessionId, runId };
    callbacks.onDurableRunAccepted?.(target);
    await followDurableRun(target, callbacks);
  },

  /**
   * Approves or denies a call a durable run holds. Resolves to the refusal's
   * machine code (`HTTP_AGENT_HARNESS_…`) when the server refuses it, else null.
   */
  async decideDurableInteraction(
    run: DurableAgentRunHandle,
    interactionId: string,
    decision: ApprovalDecision,
    idempotencyKey: string,
    scope?: 'conversation' | 'tool',
    /** AP-05: why the person denied it; the run is told. Sent only with a denial. */
    reason?: string,
    /** AP-06: the arguments the person approves instead of the call's own. Sent only with an approval of this call. */
    edits?: Record<string, unknown>,
  ): Promise<string | null> {
    const sent = await ChatApi.decideDurableInteractionOutcome(
      run, interactionId, decision, idempotencyKey, scope, reason, edits,
    );
    return 'refused' in sent ? sent.refused : null;
  },

  /**
   * `decideDurableInteraction`, also saying when the approval was counted on an ask that needs several approvals
   * and still waits for the rest: the call is not approved yet.
   */
  async decideDurableInteractionOutcome(
    run: DurableAgentRunHandle,
    interactionId: string,
    decision: ApprovalDecision,
    idempotencyKey: string,
    scope?: 'conversation' | 'tool',
    reason?: string,
    edits?: Record<string, unknown>,
  ): Promise<DecisionRefusal | { awaiting?: CountedApprovals }> {
    const base = durableSessionBase(run.agentId, run.sessionId);
    const sent = await postDecision(
      `${base}/runs/${encodeURIComponent(run.runId)}/interactions/${encodeURIComponent(interactionId)}/decision`,
      // `tool` is main's "Don't ask again" (BUGS-B): the decision with `remember`.
      {
        decision,
        idempotencyKey,
        ...(scope === 'conversation' ? { scope } : scope === 'tool' ? { remember: true } : {}),
        ...denialReason(decision, reason),
        ...(edits !== undefined && decision === 'approved' && scope === undefined ? { edits } : {}),
      },
      { runId: run.runId, interactionId },
    );
    if ('refused' in sent) return sent;
    const awaiting = countedApprovals(sent.data.interaction?.approvals);
    return awaiting ? { awaiting } : {};
  },

  /**
   * "Allow all N" / "Deny all" (DECIDE409): one request for the whole batch, so the run wakes once and runs every
   * approved call. Resolves to each interaction's decision as it now stands (an earlier one stands), or the refusal.
   */
  async decideDurableInteractions(
    run: DurableAgentRunHandle,
    interactionIds: readonly string[],
    decision: Extract<ApprovalDecision, 'approved' | 'denied'>,
    idempotencyKey: string,
    scope?: 'conversation',
    reason?: string,
  ): Promise<DecisionRefusal | { decided: Record<string, string> }> {
    const base = durableSessionBase(run.agentId, run.sessionId);
    const sent = await postDecision(
      `${base}/runs/${encodeURIComponent(run.runId)}/interactions/decisions`,
      { interactionIds, decision, idempotencyKey, ...(scope ? { scope } : {}), ...denialReason(decision, reason) },
      { runId: run.runId, interactions: interactionIds.length },
    );
    if ('refused' in sent) return sent;
    const decided: Record<string, string> = {};
    for (const item of sent.data.interactions ?? []) decided[item.interactionId] = item.decision;
    return { decided };
  },

  /**
   * A durable run's (or a child's) timeline, for the session's owner (U3). Always a background read: every caller
   * shows its own state when it fails, and a run deleted or past retention is no error for the person to dismiss.
   */
  async getRunTimeline(run: DurableAgentRunHandle): Promise<RunTimelineView> {
    const { data } = await apiClient.get<RunTimelineView>(
      `${durableSessionBase(run.agentId, run.sessionId)}/runs/${encodeURIComponent(run.runId)}/timeline`,
      { suppressErrorToast: true },
    );
    return data;
  },

  /** C10.05: what a durable run has spent so far and its status, for the live cost beside its elapsed time. */
  async getRunCost(run: DurableAgentRunHandle): Promise<RunCostView> {
    const { data } = await apiClient.get<RunCostView>(
      `${durableSessionBase(run.agentId, run.sessionId)}/runs/${encodeURIComponent(run.runId)}/cost`,
      { suppressErrorToast: true },
    );
    return data;
  },

  /** One step's arguments and result for the Activity panel's opened step (ACTRESULTS); null when malformed. */
  async getRunStep(run: DurableAgentRunHandle, toolCallId: string): Promise<ActivityStepDetail | null> {
    const { data } = await apiClient.get<{ step?: unknown }>(
      `${durableSessionBase(run.agentId, run.sessionId)}/runs/${encodeURIComponent(run.runId)}/steps/${encodeURIComponent(toolCallId)}`,
      { suppressErrorToast: true },
    );
    return toStepDetail(data?.step);
  },

  /** A durable run's pin as admitted (CPD-5): model, effort, tools, approvals. `/status` reads it. */
  async getRunResolved(run: DurableAgentRunHandle): Promise<ResolvedRunView> {
    const { data } = await apiClient.get<ResolvedRunView>(
      `${durableSessionBase(run.agentId, run.sessionId)}/runs/${encodeURIComponent(run.runId)}/resolved`,
      { suppressErrorToast: true },
    );
    return data;
  },

  /** What the caller's own replies in this conversation spent, from the budget ledger (SLASH2 C1, `/usage`). */
  async getConversationUsage(session: { agentId?: string; sessionId: string }): Promise<ConversationUsage> {
    const { data } = await apiClient.get<ConversationUsage>(
      `${durableSessionBase(session.agentId, session.sessionId)}/usage`,
      { suppressErrorToast: true },
    );
    return data;
  },

  /**
   * `/compact [focus]` (SLASH2 C2): saved on the conversation and done at the start of its next run, on any
   * executor. Resolves to the refusal's machine code when the server refuses it (nothing to compact yet).
   */
  async requestCompact(
    session: { agentId?: string; sessionId: string },
    focus: string,
  ): Promise<{ requestedAt: number } | { refused: string }> {
    try {
      const { data } = await apiClient.post<{ requestedAt: number }>(
        `${durableSessionBase(session.agentId, session.sessionId)}/compact`,
        focus ? { focus } : {},
        { suppressErrorToast: true },
      );
      return { requestedAt: data.requestedAt };
    } catch (error) {
      return { refused: errorCode(error) ?? 'HTTP_AGENT_HARNESS_COMPACT_FAILED' };
    }
  },

  /**
   * A completed run's suggested next questions (UX5). The worker stores them
   * after the run ended, so the server holds the request up to `waitMs` for them.
   */
  async getRunFollowups(run: DurableAgentRunHandle, waitMs: number, signal?: AbortSignal): Promise<ActivityFollowup[]> {
    const { data } = await apiClient.get<unknown>(
      `${durableSessionBase(run.agentId, run.sessionId)}/runs/${encodeURIComponent(run.runId)}/followups`,
      { params: { waitMs }, signal, suppressErrorToast: true },
    );
    return toFollowups(typeof data === 'object' && data !== null ? (data as { followups?: unknown }).followups : undefined);
  },

  /** One command's full redacted log (UX5), as plain text. */
  async getCommandLog(run: DurableAgentRunHandle, commandId: string): Promise<string> {
    const { data } = await apiClient.get<string>(
      `${durableSessionBase(run.agentId, run.sessionId)}/runs/${encodeURIComponent(run.runId)}/commands/${encodeURIComponent(commandId)}/log`,
      { responseType: 'text', transformResponse: (body: unknown) => body, suppressErrorToast: true },
    );
    return typeof data === 'string' ? data : '';
  },

  /** What the viewer may decide on the run's approvals; on any failure, the card keeps both buttons and the server decides. */
  async getDecisionAuthority(run: DurableAgentRunHandle): Promise<DecisionAuthority> {
    try {
      const { data } = await apiClient.get<DecisionAuthority>(
        `${durableSessionBase(run.agentId, run.sessionId)}/runs/${encodeURIComponent(run.runId)}/interactions/authority`,
      );
      return { approve: data.approve === true, deny: data.deny === true, ...(data.edit === true ? { edit: true } : {}) };
    } catch {
      return { approve: true, deny: true };
    }
  },

  async listWorkspaceVersions(
    session: { agentId?: string; sessionId: string },
    page: { limit?: number; before?: number } = {},
  ): Promise<WorkspaceVersions> {
    const { data } = await apiClient.get<WorkspaceVersions>(
      `${durableSessionBase(session.agentId, session.sessionId)}/workspace/versions`,
      // Owner only: a shared chat's participant gets 404 on open, which every caller treats as "no versions";
      // the global toast showed them "Not Found: Agent run not found".
      { params: page, suppressErrorToast: true },
    );
    return data;
  },

  /** The files this chat's workspace changed since it began, or in its last writing turn (SLASH2 `/diff`, `/undo`). */
  async getWorkspaceDiff(
    session: { agentId?: string; sessionId: string },
    scope: 'chat' | 'turn' = 'chat',
  ): Promise<WorkspaceDiff> {
    const { data } = await apiClient.get<WorkspaceDiff>(
      `${durableSessionBase(session.agentId, session.sessionId)}/workspace/diff`,
      { params: { scope } },
    );
    return data;
  },

  /**
   * `/run <command>` (SLASH3): admits a command run on the chat, with the chat's mode and model as its next message
   * would send them. Resolves to the run, or to the refusal's code (or HTTP status) and its wait.
   */
  async runSandboxCommand(
    session: { agentId?: string; sessionId: string },
    body: SandboxCommandRequest,
  ): Promise<{ runId: string } | { refused: string; retryAfterS?: number }> {
    try {
      const { data } = await apiClient.post<{ admission: { run: { runId: string } } }>(
        `${durableSessionBase(session.agentId, session.sessionId)}/commands/run`,
        body,
        { suppressErrorToast: true },
      );
      return { runId: data.admission.run.runId };
    } catch (error) {
      const retry = Number(
        (error as { originalError?: { response?: { headers?: Record<string, unknown> } } } | null)?.originalError?.response
          ?.headers?.['retry-after'],
      );
      return {
        refused: errorCode(error) ?? `HTTP_${String(statusOf(error) ?? 0)}`,
        ...(Number.isFinite(retry) && retry > 0 ? { retryAfterS: retry } : {}),
      };
    }
  },

  /**
   * `/upload` (SLASH3): the files as one workspace revision under `folder` on top of `baseRevision`. Resolves to
   * the revision, or to the refusal's code (or HTTP status).
   */
  async uploadWorkspaceFiles(
    session: { agentId?: string; sessionId: string },
    upload: { files: File[]; folder?: string; baseRevision: number; idempotencyKey: string; replace: boolean },
  ): Promise<{ revision: number; paths: string[]; replaced: string[] } | { refused: string }> {
    const body = new FormData();
    body.set('baseRevision', String(upload.baseRevision));
    body.set('idempotencyKey', upload.idempotencyKey);
    if (upload.folder) body.set('folder', upload.folder);
    body.set('replace', String(upload.replace));
    body.set('paths', JSON.stringify(upload.files.map((file) => file.webkitRelativePath || file.name)));
    for (const file of upload.files) body.append('files', file, file.name);
    try {
      const { data } = await apiClient.post<{ revision: number; paths: string[]; replaced: string[] }>(
        `${durableSessionBase(session.agentId, session.sessionId)}/workspace/files`,
        body,
        // The client's default application/json makes axios send the FormData as JSON (files as {}): the server
        // refused every /upload with "Invalid upload fields". Unset, axios sends multipart with its boundary.
        { headers: { 'Content-Type': undefined }, suppressErrorToast: true },
      );
      return { revision: data.revision, paths: data.paths, replaced: data.replaced };
    } catch (error) {
      return { refused: errorCode(error) ?? `HTTP_${String(statusOf(error) ?? 0)}` };
    }
  },

  /** `/engine` (SLASH3): this chat's coding engine from its next message; the refusal's code when refused. */
  async setSessionCodingEngine(
    session: { agentId?: string; sessionId: string },
    engine: string | null,
  ): Promise<{ engine: string | null } | { refused: string }> {
    try {
      const { data } = await apiClient.put<{ engine: string | null }>(
        `${durableSessionBase(session.agentId, session.sessionId)}/coding-engine`,
        { engine },
        { suppressErrorToast: true },
      );
      return { engine: data.engine };
    } catch (error) {
      return { refused: errorCode(error) ?? `HTTP_${String(statusOf(error) ?? 0)}` };
    }
  },

  /** What runs this chat's code (SLASH3 `/sandbox`); starts nothing. */
  async getSessionSandbox(session: { agentId?: string; sessionId: string }): Promise<SessionSandboxView> {
    const { data } = await apiClient.get<SessionSandboxView>(
      `${durableSessionBase(session.agentId, session.sessionId)}/sandbox`,
      { suppressErrorToast: true },
    );
    return data;
  },

  /** "End now": the chat's kept sign-in sandbox ends; the next message signs in again. */
  async endKeptSandbox(session: { agentId?: string; sessionId: string }): Promise<void> {
    await apiClient.delete(`${durableSessionBase(session.agentId, session.sessionId)}/sandbox/kept`, {
      suppressErrorToast: true,
    });
  },

  /** A workspace revision's files under `prefix` (SLASH3 `/files`); the head when no revision is named. */
  async listWorkspaceFiles(
    session: { agentId?: string; sessionId: string },
    prefix?: string,
  ): Promise<WorkspaceFileList> {
    const { data } = await apiClient.get<WorkspaceFileList>(
      `${durableSessionBase(session.agentId, session.sessionId)}/workspace/files`,
      { params: prefix ? { prefix } : {}, suppressErrorToast: true },
    );
    return data;
  },

  /**
   * One workspace file's bytes (SLASH3 `/open`, `/download`): at most `maxBytes` for a preview, or the whole file
   * as a download (audited). Resolves to the HTTP status when it is refused.
   */
  async readWorkspaceFile(
    session: { agentId?: string; sessionId: string },
    path: string,
    options: { maxBytes?: number; download?: boolean },
  ): Promise<WorkspaceFileRead | { refused: number }> {
    try {
      const { data, headers } = await apiClient.get<Blob>(
        `${durableSessionBase(session.agentId, session.sessionId)}/workspace/file`,
        {
          params: { path, ...(options.download ? { download: '1' } : { maxBytes: options.maxBytes }) },
          responseType: 'blob',
          suppressErrorToast: true,
        },
      );
      const header = (name: string): string => String((headers as Record<string, unknown>)[name] ?? '');
      return {
        blob: data,
        contentType: header('content-type'),
        sizeBytes: Number(header('x-workspace-file-size')) || data.size,
        truncated: header('x-workspace-truncated') === '1',
        revision: Number(header('x-workspace-revision')) || 0,
      };
    } catch (error) {
      return { refused: statusOf(error) ?? 0 };
    }
  },

  /** A workspace folder (all of it by default) as a zip (SLASH3 `/download`); the HTTP status when refused. */
  async downloadWorkspaceArchive(
    session: { agentId?: string; sessionId: string },
    prefix?: string,
  ): Promise<{ blob: Blob; revision: number } | { refused: number }> {
    try {
      const { data, headers } = await apiClient.get<Blob>(
        `${durableSessionBase(session.agentId, session.sessionId)}/workspace/archive`,
        { params: prefix ? { prefix } : {}, responseType: 'blob', suppressErrorToast: true },
      );
      return { blob: data, revision: Number((headers as Record<string, unknown>)['x-workspace-revision']) || 0 };
    } catch (error) {
      return { refused: statusOf(error) ?? 0 };
    }
  },

  /**
   * Restores `revision` as a new version on top of `baseRevision`. The key
   * names the restore, so a retried click answers the version it wrote.
   * Resolves to the refusal's machine code when the server refuses it.
   */
  async restoreWorkspaceVersion(
    session: { agentId?: string; sessionId: string },
    revision: number,
    baseRevision: number,
  ): Promise<{ revision: number } | { refused: string }> {
    try {
      const { data } = await apiClient.post<{ revision: number }>(
        `${durableSessionBase(session.agentId, session.sessionId)}/workspace/versions/${revision}/restore`,
        { baseRevision, idempotencyKey: `restore-${revision}-on-${baseRevision}` },
      );
      return { revision: data.revision };
    } catch (error) {
      return { refused: errorCode(error) ?? 'HTTP_AGENT_HARNESS_RESTORE_FAILED' };
    }
  },

  async getDurableRun(
    agentId: string | undefined,
    sessionId: string,
    runId: string,
  ): Promise<AgentHarnessRun> {
    const base = durableSessionBase(agentId, sessionId);
    const { data } = await apiClient.get<{ run: AgentHarnessRun }>(
      `${base}/runs/${encodeURIComponent(runId)}`,
    );
    return data.run;
  },

  /**
   * Stop: one request, no revision read first (UX3 ST-2). The key names the
   * run, so a repeated Stop is the same command. A run already over answers
   * null.
   */
  async cancelCurrentDurableRun(
    agentId: string | undefined,
    sessionId: string,
    runId: string,
  ): Promise<AgentRunControlReceipt | null> {
    const base = durableSessionBase(agentId, sessionId);
    try {
      const { data } = await apiClient.post<{ receipt: AgentRunControlReceipt }>(
        `${base}/runs/${encodeURIComponent(runId)}/cancel`,
        { idempotencyKey: `stop-${runId}` },
        { suppressErrorToast: true },
      );
      return data.receipt;
    } catch (error) {
      if (statusOf(error) === 409) return null;
      throw error;
    }
  },

  // Fetch one page of conversations for a single source (owned or shared).
  // Call once per tab if you need both lists.
  async fetchConversations(
    page: number = 1,
    limit: number = 20,
    options: FetchConversationsOptions
  ): Promise<{
    conversations: Conversation[];
    source: ConversationSource;
    pagination: ConversationsListResponse['pagination'];
  }> {
    const search = options.search?.trim();
    const params: Record<string, string | number> = {
      page,
      limit,
      source: options.source,
    };
    if (search) {
      params.search = search;
    }
    if (options.excludeProjectChats) {
      params.projectId = PROJECT_ID_UNASSIGNED;
    }

    const { data } = await apiClient.get<ConversationsListResponse>(
      `/api/v1/conversations`,
      { params, signal: options.signal, suppressErrorToast: options.suppressErrorToast }
    );

    return {
      conversations: data.conversations.map(transformConversation),
      source: data.source,
      pagination: data.pagination,
    };
  },

  /**
   * Fetch a specific conversation with paginated messages.
   * page=1 returns the most recent batch; higher pages return older batches.
   * Used when loading conversation history from sidebar click and for
   * "load older messages" infinite-scroll in the message list.
   *
   * Response is wrapped: { conversation: {...}, filters: {...}, meta: {...} }
   */
  async fetchConversation(
    conversationId: string,
    page: number = 1,
    limit: number = CONVERSATION_MESSAGES_PAGE_SIZE,
  ): Promise<{
    conversation: {
      id: string;
      title: string;
      initiator: string;
      createdAt?: string;
      messages: ConversationMessage[];
      status: string;
      modelInfo: ModelInfo;
      isShared: boolean;
      sharedWith: SharedWithEntry[];
      forkedFrom?: ForkOrigin;
      forks?: ForkChild[];
      access: {
        isOwner: boolean;
        accessLevel: string;
      };
      /** Durable sessions only: the run still in flight, or `null`. */
      activeRunId?: string | null;
      /** The project the conversation belongs to; its turns are project-scoped (UX3 PS-2). */
      projectId?: string | null;
    };
    messages: ConversationMessage[];
    pagination: ConversationPagination;
  }> {
    const { data } = await apiClient.get<{
      conversation: {
        id: string;
        title: string;
        initiator: string;
        createdAt: string;
        isShared: boolean;
        sharedWith: SharedWithEntry[];
        status: string;
        messages: ConversationMessage[];
        modelInfo: ModelInfo;
        forkedFrom?: ForkOrigin;
        forks?: ForkChild[];
        /** Optional: older API versions may not include this field. */
        pagination?: ConversationPagination;
        access: {
          isOwner: boolean;
          accessLevel: string;
        };
        activeRunId?: string | null;
        projectId?: string | null;
      };
      filters: Record<string, unknown>;
      meta: Record<string, unknown>;
    }>(`/api/v1/conversations/${conversationId}/`, {
      params: { page, limit },
    });

    const pagination = data.conversation.pagination ?? {
      page,
      limit,
      totalCount: 0,
      totalPages: 1,
      hasNextPage: false,
      hasPrevPage: page > 1,
    };

    return {
      conversation: data.conversation,
      messages: data.conversation.messages || [],
      pagination,
    };
  },

  /**
   * Stream a chat message with SSE
   * Handles new conversation creation and existing conversation messages
   */
  async streamMessage(
    request: StreamChatRequest,
    callbacks: StreamMessageCallbacks
  ): Promise<void> {
    let endpoint: string;
    let payload: Record<string, unknown>;

    if (request.agentId) {
      const agentChatMode = streamChatModeToAgentApiChatMode(request.chatMode);
      endpoint = request.conversationId
        ? `/api/v1/agents/${request.agentId}/conversations/${request.conversationId}/messages/stream`
        : `/api/v1/agents/${request.agentId}/conversations/stream`;
      const f = request.filters;
      payload = {
        query: request.query,
        ...buildModelPayload(request),
        chatMode: agentChatMode,
        timezone: getClientTimezone(),
        currentTime: getClientCurrentTime(),
        ...(request.runId ? { runId: request.runId } : {}),
        ...(request.reasoningEffort ? { reasoningEffort: request.reasoningEffort } : {}),
        // `undefined` (runtime.ts omits the field entirely when every tool
        // is selected) must NOT become `[]` here — an empty array means
        // "no tools" to the backend (agent.py treats `None`/missing as
        // "use every configured toolset", `[]` as an explicit empty
        // filter), the opposite of what an unfiltered selection means.
        ...(request.agentStreamTools !== undefined ? { tools: request.agentStreamTools } : {}),
        ...buildAgentFiltersPayload(f),
        ...(request.appliedFilters ? { appliedFilters: request.appliedFilters } : {}),
        ...(request.agentCapabilities ? { agentCapabilities: request.agentCapabilities } : {}),
        ...(request.attachments?.length ? { attachments: request.attachments } : {}),
        ...(request.projectId ? { projectId: request.projectId } : {}),
        ...(request.harness ? { harness: request.harness } : {}),
        ...(request.codingEngine ? { codingEngine: request.codingEngine } : {}),
        ...(request.repository ? { repository: request.repository } : {}),
        ...(request.gates ? { gates: request.gates } : {}),
        ...(request.toolApproval ? { toolApproval: request.toolApproval } : {}),
      };
    } else {
      endpoint = request.conversationId
        ? `/api/v1/conversations/${request.conversationId}/messages/stream`
        : `/api/v1/conversations/stream`;
      // Rename `agentStreamTools` → `tools` (Node.js controller reads `req.body.tools`
      // uniformly for both agent and non-agent paths) and validate filters.
      // `rest` already carries `runId` — spread through as-is; it names the turn.
      const {
        agentStreamTools,
        filters: reqFilters,
        attachments: reqAttachments,
        modelKey: _modelKey,
        modelName: _modelName,
        modelFriendlyName: _modelFriendlyName,
        ...rest
      } = request;
      payload = {
        ...rest,
        ...buildModelPayload(request),
        timezone: getClientTimezone(),
        currentTime: getClientCurrentTime(),
        ...(agentStreamTools !== undefined ? { tools: agentStreamTools } : {}),
        ...buildFiltersPayload(reqFilters),
        ...(reqAttachments?.length ? { attachments: reqAttachments } : {}),
      };
    }

    await runTurnStream(endpoint, payload, request.agentId, callbacks);
  },

  /**
   * Regenerate the last bot response SSE stream.
   * Endpoint: POST /api/v1/conversations/:conversationId/message/:messageId/regenerate
   * Response: SSE stream with the same events as streamMessage, in one
   * request: the server streams a durable regenerate or today's on it (LAT2).
   *
   * Pure transport: the caller is responsible for resolving the model and
   * mode/filters (typically from the slot being regenerated, not global UI
   * state) and passing them in, matching the pattern used by streamMessage
   * and streamAgentRegenerate.
   */
  async streamRegenerate(
    conversationId: string,
    messageId: string,
    callbacks: StreamMessageCallbacks,
    request: {
      modelKey: string;
      modelName: string;
      modelFriendlyName: string;
      chatMode: StreamChatRequest['chatMode'];
      filters: StreamChatRequest['filters'];
      /** Universal agent mode: explicit tool subset (null = all, [] = none). */
      agentStreamTools?: string[];
      agentCapabilities?: StreamChatRequest['agentCapabilities'];
      reasoningEffort?: StreamChatRequest['reasoningEffort'];
      harness?: StreamChatRequest['harness'];
      runId?: string;
      /** The edited question; omitted to regenerate the stored one unchanged. */
      query?: string;
      /** The edited question's files, replacing the stored ones; omitted keeps them. */
      attachments?: AttachmentRef[];
      /** Changed answers to the question card the reply followed (`User selections: ...`). */
      answers?: string;
    }
  ): Promise<void> {
    const endpoint = `/api/v1/conversations/${conversationId}/message/${messageId}/regenerate`;

    const body: Record<string, unknown> = {
      ...buildModelPayload(request),
      chatMode: request.chatMode,
      ...buildFiltersPayload(request.filters),
      timezone: getClientTimezone(),
      currentTime: getClientCurrentTime(),
    };
    if (request.agentStreamTools !== undefined) {
      body.tools = request.agentStreamTools;
    }
    if (request.agentCapabilities !== undefined) {
      body.agentCapabilities = request.agentCapabilities;
    }
    if (request.reasoningEffort) {
      body.reasoningEffort = request.reasoningEffort;
    }
    if (request.harness) {
      body.harness = request.harness;
    }
    if (request.runId) {
      body.runId = request.runId;
    }
    if (request.query) {
      body.query = request.query;
    }
    if (request.attachments !== undefined) {
      body.attachments = request.attachments;
    }
    if (request.answers) {
      body.answers = request.answers;
    }

    await runTurnStream(endpoint, body, undefined, callbacks);
  },

  /**
   * Regenerate a message in an agent conversation (SSE).
   * POST /api/v1/agents/:agentId/conversations/:conversationId/message/:messageId/regenerate
   */
  async streamAgentRegenerate(
    agentId: string,
    conversationId: string,
    messageId: string,
    callbacks: StreamMessageCallbacks,
    model: {
      modelKey: string;
      modelName: string;
      modelFriendlyName?: string;
      chatMode: AgentStrategyApiSegment;
      /** Explicit tool subset for this agent context (all tools when omitted). */
      tools?: string[];
      filters: ChatKnowledgeFilters;
      agentCapabilities?: AgentCapabilities;
      reasoningEffort?: StreamChatRequest['reasoningEffort'];
      harness?: StreamChatRequest['harness'];
      runId?: string;
      /** The edited question; omitted to regenerate the stored one unchanged. */
      query?: string;
      /** The edited question's files, replacing the stored ones; omitted keeps them. */
      attachments?: AttachmentRef[];
      /** Changed answers to the question card the reply followed (`User selections: ...`). */
      answers?: string;
    }
  ): Promise<void> {
    const endpoint = `/api/v1/agents/${agentId}/conversations/${conversationId}/message/${messageId}/regenerate`;

    const agentRegenBody: Record<string, unknown> = {
      ...buildModelPayload(model),
      chatMode: model.chatMode,
      timezone: getClientTimezone(),
      currentTime: getClientCurrentTime(),
      ...buildAgentFiltersPayload(model.filters),
    };
    if (model.tools !== undefined) {
      agentRegenBody.tools = model.tools;
    }
    if (model.agentCapabilities !== undefined) {
      agentRegenBody.agentCapabilities = model.agentCapabilities;
    }
    if (model.reasoningEffort) {
      agentRegenBody.reasoningEffort = model.reasoningEffort;
    }
    if (model.harness) {
      agentRegenBody.harness = model.harness;
    }
    if (model.runId) {
      agentRegenBody.runId = model.runId;
    }
    if (model.query) {
      agentRegenBody.query = model.query;
    }
    if (model.attachments !== undefined) {
      agentRegenBody.attachments = model.attachments;
    }
    if (model.answers) {
      agentRegenBody.answers = model.answers;
    }

    await runTurnStream(endpoint, agentRegenBody, agentId, callbacks);
  },

  /**
   * Cooperatively stop an in-flight stream by `runId`.
   * Endpoint: POST /api/v1/conversations/:conversationId/cancel (assistant)
   *        or POST /api/v1/agents/:agentId/conversations/:conversationId/cancel (agent)
   *
   * Never rejects on "already finished" — the backend returns
   * `{ cancelled: false }` (200) for an unknown/already-settled `runId`
   * rather than a 4xx; see `cancelConversationStream` (Node) /
   * `cancel_chat_stream` (Python). Callers that only care whether the
   * stream is now stopped can ignore the return value entirely and rely on
   * the grace-timeout abort fallback (`stopStreamForSlot`) instead.
   */
  async cancelStream(
    conversationId: string,
    runId: string,
    agentId?: string | null,
  ): Promise<{ cancelled: boolean }> {
    const endpoint = agentId
      ? `/api/v1/agents/${agentId}/conversations/${conversationId}/cancel`
      : `/api/v1/conversations/${conversationId}/cancel`;
    const { data } = await apiClient.post<{ cancelled: boolean }>(
      endpoint,
      { runId },
      // The composer already shows its own "Stopping…" affordance; a
      // generic error toast on top (e.g. if the run already ended) would
      // be confusing noise — `stopStreamForSlot`'s grace-timeout fallback
      // covers the case where this call fails outright.
      { suppressErrorToast: true },
    );
    return data;
  },

  /**
   * Search across knowledge bases and connectors.
   * Endpoint: POST /api/v1/search
   */
  async search(request: SearchRequest, signal?: AbortSignal): Promise<SearchResponse> {
    const { data } = await apiClient.post<SearchResponse>(
      '/api/v1/search',
      request,
      {
        signal,
        // Search errors are shown inline on the chat page; suppress avoids duplicate
        // toasts and odd UX when a request is superseded by a newer search (abort).
        suppressErrorToast: true,
      }
    );
    return data;
  },

  /**
   * Submit feedback (thumbs up / thumbs down) for a bot response message.
   * Endpoint: POST /api/v1/conversations/:conversationId/message/:messageId/feedback
   */
  async submitFeedback(
    conversationId: string,
    messageId: string,
    feedback: FeedbackPayload
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/conversations/${conversationId}/message/${messageId}/feedback`,
      feedback
    );
  },

  /**
   * Submit feedback for a bot response in an agent conversation.
   * Endpoint: POST /api/v1/agents/:agentId/conversations/:conversationId/message/:messageId/feedback
   */
  async submitAgentFeedback(
    agentId: string,
    conversationId: string,
    messageId: string,
    feedback: FeedbackPayload
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/agents/${agentId}/conversations/${conversationId}/message/${messageId}/feedback`,
      feedback
    );
  },

  // Rename a conversation
  async renameConversation(conversationId: string, title: string): Promise<void> {
    await apiClient.patch(`/api/v1/conversations/${conversationId}/title`, { title });
  },

  /**
   * Copy a conversation into a new chat, including every message up to `messageId`.
   * Endpoint: POST /api/v1/conversations/:conversationId/message/:messageId/fork
   */
  async forkConversation(
    conversationId: string,
    messageId: string,
  ): Promise<ForkConversationResult> {
    const { data } = await apiClient.post<ForkConversationResult>(
      `/api/v1/conversations/${conversationId}/message/${messageId}/fork`,
    );
    return data;
  },

  // Delete a conversation
  async deleteConversation(conversationId: string): Promise<void> {
    await apiClient.delete(`/api/v1/conversations/${conversationId}`);
  },

  // Archive a conversation
  async archiveConversation(conversationId: string): Promise<void> {
    await apiClient.patch(`/api/v1/conversations/${conversationId}/archive`);
  },

  /**
   * Fork a conversation (a shared one's participant, or its owner): a copy the caller owns. One copy
   * per `idempotencyKey`, so a retry after a lost answer opens the same one.
   * Endpoint: POST /api/v1/conversations/:conversationId/fork
   * (agent: POST /api/v1/agents/:agentKey/conversations/:conversationId/fork)
   */
  async continuePrivately(
    conversationId: string,
    idempotencyKey: string,
    agentKey?: string
  ): Promise<{ conversationId: string; replayed: boolean }> {
    const base = agentKey ? `/api/v1/agents/${encodeURIComponent(agentKey)}/conversations` : '/api/v1/conversations';
    const { data } = await apiClient.post<{ conversationId: string; replayed: boolean }>(
      `${base}/${encodeURIComponent(conversationId)}/fork`,
      { idempotencyKey }
    );
    return data;
  },

  // Restore (unarchive) a conversation
  async restoreConversation(conversationId: string): Promise<void> {
    await apiClient.patch(`/api/v1/conversations/${conversationId}/unarchive`);
  },

  // Pin or unpin one of the caller's own conversations (idempotent; owner only)
  async setConversationPinned(conversationId: string, pinned: boolean): Promise<void> {
    await apiClient.patch(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/${pinned ? 'pin' : 'unpin'}`,
    );
  },

  /**
   * Fetch archived conversations.
   * Endpoint: GET /api/v1/conversations/show/archives
   *
   * The response embeds messages inside each conversation object so we
   * extract them into a messagesMap keyed by conversation ID. This lets
   * the archived-chats page load message detail without a second API call.
   */
  async fetchArchivedConversations(params?: { page?: number; limit?: number; conversationId?: string }): Promise<{
    conversations: Conversation[];
    messagesMap: Record<string, ConversationMessage[]>;
    pagination: ConversationsListResponse['pagination'];
  }> {
    const query: Record<string, string | number> = {};
    if (params?.page != null) query.page = params.page;
    if (params?.limit != null) query.limit = params.limit;
    if (params?.conversationId) query.conversationId = params.conversationId;

    const { data } = await apiClient.get<{
      conversations: (ConversationApiResponse & { messages?: ConversationMessage[] })[];
      pagination: ConversationsListResponse['pagination'];
    }>('/api/v1/conversations/show/archives', { params: query });

    const raw = data.conversations ?? [];
    const messagesMap: Record<string, ConversationMessage[]> = {};
    raw.forEach((c) => {
      if (c.messages) messagesMap[c._id] = c.messages;
    });

    return {
      conversations: raw.map(transformConversation),
      messagesMap,
      pagination: data.pagination,
    };
  },

  /**
   * Search across all archived conversations (both assistant and agent).
   * Endpoint: GET /api/v1/conversations/show/archives/search
   *
   * Returns a unified, paginated list sorted by lastActivityAt desc.
   * Each result includes a `source` field ('assistant' | 'agent') and
   * optionally `agentKey` for agent conversations.
   */
  async searchArchivedConversations(params: {
    search: string;
    page?: number;
    limit?: number;
  }): Promise<{
    conversations: (Conversation & { source: 'assistant' | 'agent'; agentKey?: string })[];
    pagination: ConversationsListResponse['pagination'];
    summary: {
      totalMatches: number;
      assistantMatches: number;
      agentMatches: number;
      searchQuery: string;
    };
  }> {
    const query: Record<string, string | number> = { search: params.search };
    if (params.page != null) query.page = params.page;
    if (params.limit != null) query.limit = params.limit;

    const { data } = await apiClient.get<{
      conversations: (ConversationApiResponse & {
        source: 'assistant' | 'agent';
        agentKey?: string;
      })[];
      pagination: ConversationsListResponse['pagination'];
      summary: {
        totalMatches: number;
        assistantMatches: number;
        agentMatches: number;
        searchQuery: string;
      };
    }>('/api/v1/conversations/show/archives/search', { params: query });

    return {
      conversations: (data.conversations ?? []).map((c) => ({
        ...transformConversation(c),
        source: c.source,
        agentKey: c.agentKey,
      })),
      pagination: data.pagination,
      summary: data.summary,
    };
  },

  /**
   * Upload chat attachments (PDF / JPEG / PNG) and return the server-assigned AttachmentRef list.
   * Uses the public JWT-authenticated endpoint — not the internal scoped-token endpoint.
   */
  async uploadAttachments(
    files: File[],
    opts: { agentId?: string | null; conversationId?: string | null; signal?: AbortSignal },
  ): Promise<AttachmentRef[]> {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f, f.name));
    if (opts.conversationId) fd.append('conversationId', opts.conversationId);
    const url = opts.agentId
      ? `/api/v1/agents/${encodeURIComponent(opts.agentId)}/conversations/attachments/upload`
      : `/api/v1/conversations/attachments/upload`;
    const { data } = await apiClient.post<{ attachments: AttachmentRef[] }>(url, fd, {
      signal: opts.signal,
      timeout: CHAT_ATTACHMENT_UPLOAD_TIMEOUT_MS,
      // Remove the default application/json Content-Type so axios can auto-set
      // multipart/form-data with the correct boundary for this FormData upload.
      headers: { 'Content-Type': undefined },
      // Suppress the global error toast — the caller (chat-input-wrapper) shows
      // a contextual toast with the actual server error message.
      suppressErrorToast: true,
    });
    return data.attachments ?? [];
  },

  /**
   * Delete a previously uploaded chat attachment. Called fire-and-forget when
   * the user removes a chip after the upload has completed. Errors are silently
   * swallowed by callers — a failed delete is acceptable (orphan record) but
   * must never block the UI or the query.
   */
  async deleteAttachment(
    recordId: string,
    opts: { agentId?: string | null },
  ): Promise<void> {
    const url = opts.agentId
      ? `/api/v1/agents/${encodeURIComponent(opts.agentId)}/conversations/attachments/${encodeURIComponent(recordId)}`
      : `/api/v1/conversations/attachments/${encodeURIComponent(recordId)}`;
    await apiClient.delete(url, { suppressErrorToast: true });
  },

  /**
   * Fetch hub root nodes for the chat collection picker.
   *
   * Endpoint: GET /api/v1/knowledgeBase/knowledge-hub/nodes
   *
   * Returns hub root items (KB connector, other connectors, COLLECTION roots) for one page.
   * The chat UI paginates with `page` / `limit` and merges `serverPagination` when present.
   * Chevron rules live in `collections-tab` (`showExpandChevron`).
   */
  async listCollectionsForChat(params?: {
    cursor?: string | null;
    limit?: number;
    origins?: 'COLLECTION' | 'CONNECTOR';
  }): Promise<ListCollectionsForChatResult> {
    const limit = params?.limit ?? 100;
    const { data } = await apiClient.get<KnowledgeHubNodesResponse>(
      '/api/v1/knowledgeBase/knowledge-hub/nodes',
      {
        params: {
          // Only a cursor the server issued is ever sent back; one it did not
          // issue is a 400.
          ...(params?.cursor ? { cursor: params.cursor } : {}),
          // Root apps of one origin only; flattened=false keeps it a root listing, not a tree search.
          ...(params?.origins ? { nodeTypes: 'app', origins: params.origins, flattened: false } : {}),
          limit,
          sortBy: 'updatedAt',
          sortOrder: 'desc',
        },
      }
    );

    const knowledgeBases: KnowledgeBaseForChat[] = data.items.map((item) => ({
      id: item.id,
      name: item.name,
      nodeType: item.nodeType,
      hasChildren: item.hasChildren,
      origin: item.origin,
      connector: item.connector,
      subType: item.subType,
      createdAtTimestamp: item.createdAt ?? 0,
      updatedAtTimestamp: item.updatedAt ?? 0,
      createdBy: '',
      userRole: '',
      folders: [],
    }));
    return {
      knowledgeBases,
      requestedLimit: limit,
      nextCursor: data.pagination?.nextCursor ?? null,
      serverPagination: data.pagination ?? null,
    };
  },

  /**
   * Fetch available LLM models for the org, with the whole response for any
   * other org settings it carries.
   * Endpoint: GET /api/v1/configurationManager/ai-models/available/llm
   */
  async fetchOrgLlmCatalog(): Promise<{ models: AvailableLlmModel[]; response: unknown }> {
    const { data } = await apiClient.get<{ status: string; models: AvailableLlmModel[]; message: string }>(
      '/api/v1/configurationManager/ai-models/available/llm'
    );
    // `data` itself can be null/undefined (e.g. an empty response body), and
    // `?? []` alone wouldn't guard a malformed non-array `models` value — both
    // would otherwise reach callers that call `.find`/`.map` on the result.
    return { models: Array.isArray(data?.models) ? data.models : [], response: data };
  },

  /**
   * The org's LLM models from the session cache that chat uses.
   * @deprecated Kept for edition code; call `fetchModelsForContext(ASSISTANT_CTX)`.
   */
  async fetchAvailableLlms(): Promise<AvailableLlmModel[]> {
    // Imported on use: that module imports this one.
    const [{ fetchModelsForContext }, { ASSISTANT_CTX }] = await Promise.all([
      import('./utils/fetch-models-for-context'),
      import('./store'),
    ]);
    return fetchModelsForContext(ASSISTANT_CTX);
  },

  /**
   * The configured models the picker shows as unavailable, with why: limited to groups the person is
   * in none of (G-41, by `modelKey`), or for a reason `reasonsOf` (the `modelUnavailableReasons`
   * extension point) reads from the answer's choices. Empty where the harness is not served or the
   * answer cannot be read: the turn is still refused where its model is decided.
   */
  async fetchUnavailableModels(
    reasonsOf: (choices: unknown) => ReadonlyMap<string, string> = () => new Map(),
  ): Promise<UnavailableModels> {
    try {
      const { data } = await apiClient.get<{ modelChoices?: unknown }>(
        '/api/v1/agent-harness/model-accounts/access',
        { suppressErrorToast: true },
      );
      return { group: groupBlocked(data?.modelChoices), reasons: reasonsOf(data?.modelChoices) };
    } catch {
      return { group: new Set(), reasons: new Map() };
    }
  },

  // ── Deprecated: knowledge-hub/nodes approach (commented out) ──
  // The knowledge-hub/nodes endpoint supports richer data (per-item counts,
  // nodeTypes filtering) but was returning incorrect results. Kept for
  // reference in case we switch back when the endpoint is fixed.
  //
  // async listCollectionsForChat_knowledgeHub(params?: { q?: string; page?: number; limit?: number }) {
  //   const { data } = await apiClient.get<CollectionsForChatResponse_KnowledgeHub>(
  //     '/api/v1/knowledgeBase/knowledge-hub/nodes',
  //     {
  //       params: {
  //         nodeTypes: 'kb',
  //         include: 'counts',
  //         sortBy: 'updatedAt',
  //         sortOrder: 'desc',
  //         page: 1,
  //         limit: 100,
  //         ...params,
  //       },
  //     }
  //   );
  //   return data;
  // },
};

// ── Types for listCollectionsForChat ──

/**
 * Minimal KB node shape from GET /api/v1/knowledgeBase/knowledge-hub/nodes
 */
interface KbNodeFromHubApi {
  id: string;
  name: string;
  nodeType: string;
  hasChildren?: boolean;
  origin?: string;
  connector?: string;
  subType?: string;
  updatedAt?: number;
  createdAt?: number;
}

interface KnowledgeHubNodesResponse {
  success: boolean;
  items: KbNodeFromHubApi[];
  error?: string | null;
  pagination?: {
    page?: number;
    limit?: number;
    totalItems?: number;
    totalPages?: number;
    startIndex?: number;
    endIndex?: number;
    hasNext?: boolean;
    hasPrev?: boolean;
    nextCursor?: string | null;
    prevCursor?: string | null;
  } | null;
}

/** One page from {@link ChatApi.listCollectionsForChat}. */
export interface ListCollectionsForChatResult {
  knowledgeBases: KnowledgeBaseForChat[];
  requestedLimit: number;
  /** The cursor for the next page, or null at the end of the result. */
  nextCursor: string | null;
  serverPagination: KnowledgeHubNodesResponse['pagination'];
}

/**
 * Normalized knowledge base shape used internally in the chat collection picker.
 */
export interface KnowledgeBaseForChat {
  id: string;
  name: string;
  nodeType: string;
  hasChildren?: boolean;
  origin?: string;
  connector?: string;
  subType?: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  createdBy: string;
  userRole: string;
  folders: string[];
}

// ============================================
// Organisation
// ============================================

type OrgWithLogo = { org: OrgResponse | null; logoUrl: string | null };

let _orgWithLogoPromise: Promise<OrgWithLogo> | null = null;

/** @deprecated Kept for edition code; React code reads `useOrg` / `useOrgLogo`, which logo and org writes update. */
export function clearOrgWithLogoCache(): void {
  _orgWithLogoPromise = null;
}

onSessionEnd(clearOrgWithLogoCache);

/** @deprecated Kept for edition code; React code reads `useOrg` / `useOrgLogo`. */
export function fetchOrgWithLogo(): Promise<OrgWithLogo> {
  if (!_orgWithLogoPromise) {
    const promise: Promise<OrgWithLogo> = Promise.allSettled([OrgApi.getOrg(), OrgApi.getLogoUrl()]).then(
      ([orgRes, logoRes]) => {
        // A failed org read is asked again next time instead of staying blank for the session.
        if (orgRes.status === 'rejected' && _orgWithLogoPromise === promise) _orgWithLogoPromise = null;
        return {
          org: orgRes.status === 'fulfilled' ? orgRes.value : null,
          logoUrl: logoRes.status === 'fulfilled' ? logoRes.value : null,
        };
      }
    );
    _orgWithLogoPromise = promise;
  }
  return _orgWithLogoPromise;
}

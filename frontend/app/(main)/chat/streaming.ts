/**
 * Slot-scoped SSE streaming logic.
 *
 * Extracted from the old ChatModelAdapter — this module is purely
 * imperative (no React hooks) so SSE streams can write to any slot
 * in Zustand regardless of which slot is currently active.
 *
 * Key design:
 * - `streamMessageForSlot()` handles new + existing conversations.
 * - `streamRegenerateForSlot()` handles message regeneration.
 * - rAF batching collapses high-frequency SSE chunks into one Zustand
 *   write per animation frame. Background (inactive) slot writes happen
 *   silently — no React component subscribes to those fields.
 */

import { ChatApi, type StreamMessageCallbacks } from './api';
import { pushRunCost } from './live-run-cost';
import { approvalStreamCallbacks, seedPendingApprovals } from './approvals';
import { pendingConnects } from './connect-cards';
import { withArtifactCard } from './code-tasks';
import { planStreamCallbacks } from './plan-stream';
import { hasSavedDurableStream } from './durable-run-stream';
import { AgentsApi } from '@/app/(main)/agents/api';
import { confirmNewChatEngine } from './new-chat-engine';
import { useChatStore, ctxKeyFromAgent, getEffectiveModel, getRequestReasoningEffort, projectAgentCapabilities } from './store';
import { fetchModelsForContext } from './utils/fetch-models-for-context';
import { buildChatArtifact } from './utils/build-chat-artifact';
import { appendResumeParts } from './utils/tool-display';
import { stripAnswerMarkers } from './utils/parse-download-markers';
import { debugLog } from './debug-logger';
import {
  loadHistoricalMessages,
  getThreadMessagePlainText,
  isUsableFollowUpText,
  prependOlderRows,
  reconcilePendingAfterPrepend,
  threadMatchesRows,
} from './runtime';
import {
  hasUnansweredQuestions,
  mergeAskUserQuestionPayloads,
} from './components/message-area/ask-user-question-card';
import { i18n } from '@/lib/i18n';
import { toast } from '@/lib/store/toast-store';
import { showNoModelToast } from './utils/no-model-toast';
import {
  useFeatureFlagsStore,
  selectAgentHarnessEnabled,
  selectAgentHarnessResumeEnabled,
} from '@/lib/store/feature-flags-store';
import { notifyAnswerFinishedInBackground } from './utils/background-finish';
import { hasAnyFilter } from './utils/tree-selection';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { chatHarnessField, turnHarnessChoice } from './assistant-harness';
import {
  buildAssistantApiFilters,
  buildStreamRequestModeFields,
  streamChatModeToAgentApiChatMode,
  type StreamChatRequest,
  type StatusMessage,
  type ModelOverride,
  type SSEConnectedEvent,
  type ChatArtifact,
  type SSEArtifactEvent,
  type SSEAskUserQuestionEvent,
  type PendingAskUserQuestion,
  type MessagePart,
  type ChatSlot,
  type DurableAgentRunHandle,
  type ChatKnowledgeFilters,
  type AttachmentRef,
  type ConversationMessage,
} from './types';
import {
  buildCitationMapsFromStreaming,
} from './components/message-area/response-tabs/citations';
import type { CitationMaps } from './components/message-area/response-tabs/citations/types';
import { pickModelInfoFromConversationBundle } from './utils/apply-conversation-model-info';
import { CONVERSATION_MESSAGES_PAGE_SIZE } from './constants';
import { ANSWER_FAILED, ChatStreamError, streamErrorInfo, type ChatStreamErrorInfo } from './stream-error';
import { DurableAdmissionError, refusedTurnText } from './harness-errors';
import { cancelCardSend, questionSendKey, SEND_ABORTED, sendFailureOf, type SendAttempt, type SendFailure } from './card-send';
import { StreamError } from '@/lib/api/stream-errors';
import { useRepositoryStore } from './repository-store';
import { useRunGatesStore } from './run-gates';
import { readAccessNotices, type AccessNotice } from '@/app/components/feedback/access-notice';
import { discardCompare, onPrimaryTurnStarted } from './compare/compare-controller';

/** Stable id for the in-flight assistant placeholder (works on HTTP where randomUUID is missing). */
function createPendingAssistantId(): string {
  const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }
  return `asst-pending-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

/**
 * Client-generated run identifier sent with the stream request so a later
 * Stop can cooperatively cancel this exact run (`ChatApi.cancelStream`).
 * The backend validates it as a UUID (`cancelRunBodySchema`), so there is no
 * `Math.random` fallback: `randomUUID` needs a secure context, but
 * `getRandomValues` does not, so plain-HTTP deployments still get a real v4
 * UUID. `undefined` (no Web Crypto at all) means this run cannot be cancelled.
 */
function generateRunId(): string | undefined {
  const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (!cryptoApi) return undefined;
  if (typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID();
  if (typeof cryptoApi.getRandomValues !== 'function') return undefined;
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
}

/** True while this slot's in-flight `runId` is still the one `streamRunId` started. */
function slotIsOnRun(slotId: string, streamRunId: string | null): boolean {
  const slot = useChatStore.getState().slots[slotId];
  return Boolean(slot && slot.runId === streamRunId);
}

/**
 * Grace period between posting a cooperative cancel and hard-aborting the
 * connection if the backend hasn't finished the run by then. Long enough
 * for `RunCancellationRegistry` fan-out + the in-flight LLM chunk boundary
 * check (`LangChainTransport.stream()`) to land under normal conditions;
 * short enough that a stuck backend doesn't leave Stop looking unresponsive.
 */
const STOP_GRACE_MS = 5000;

/**
 * A durable run stops its model call or tool as soon as the cancel lands and
 * saves the text it streamed (UX3, R1 F5), so the chat waits for that saved
 * answer and shows exactly it; the local text is only the fallback for a
 * server that never answers.
 */
const DURABLE_STOP_GRACE_MS = 20_000;

/**
 * Closing the stream no longer stops a run (it finishes server-side), so the
 * cancel POST is the only Stop that works and must actually land. It can race
 * the backend registering the run (`cancelled: false` for an unknown id) or hit
 * a network blip, so it is retried with backoff until acknowledged.
 */
const STOP_CANCEL_RETRY_WINDOW_MS = 20_000;
const STOP_CANCEL_RETRY_BASE_MS = 500;
const STOP_CANCEL_RETRY_MAX_MS = 3000;
/** A cancel POST that never settles counts as failed so the next attempt still goes out. */
const STOP_CANCEL_ATTEMPT_TIMEOUT_MS = 5000;
/** Poll interval while waiting for `conversation_created` to give a new chat its id. */
const STOP_CANCEL_CONV_ID_POLL_MS = 250;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('request timed out')), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err: unknown) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

/**
 * Stops that were pressed before the run's conversation id was known, by runId.
 * `onConnected` fills the id in even after the slot has moved on, so the cancel
 * can still be addressed once `conversation_created` arrives.
 */
const pendingStopConvIds = new Map<string, string | null>();

interface CancelHandle {
  isAcknowledged(): boolean;
  /** The conversation id the cancel was (last) addressed to, once known. */
  convId(): string | null;
  /** Resolves once the cancel was acknowledged or the retry window closed. */
  readonly done: Promise<void>;
}

/**
 * Posts the cancel until the backend reports `cancelled: true` or the retry
 * window closes. Deliberately independent of the slot's lifecycle: it keeps
 * going after the grace timer settles the UI, and after a Stop-then-send has
 * moved the slot on to a new run. `knownConvId` is null on the very first
 * turn of a new chat; the id then comes from `pendingStopConvIds`.
 */
function postCancelUntilAcknowledged(
  slotId: string,
  runId: string,
  agentId: string | null,
  knownConvId: string | null,
): CancelHandle {
  const deadline = Date.now() + STOP_CANCEL_RETRY_WINDOW_MS;
  let delay = STOP_CANCEL_RETRY_BASE_MS;
  let acknowledged = false;
  let addressedConvId = knownConvId;
  if (!knownConvId) pendingStopConvIds.set(runId, null);

  const done = new Promise<void>((resolve) => {
    const finish = (): void => {
      pendingStopConvIds.delete(runId);
      resolve();
    };
    const attempt = async (): Promise<void> => {
      const convId = knownConvId ?? pendingStopConvIds.get(runId) ?? null;
      if (convId) {
        addressedConvId = convId;
        try {
          const result = await withTimeout(
            ChatApi.cancelStream(convId, runId, agentId ?? undefined),
            STOP_CANCEL_ATTEMPT_TIMEOUT_MS,
          );
          if (result?.cancelled) {
            acknowledged = true;
            finish();
            return;
          }
        } catch (err) {
          console.warn('[streaming] cancelStream request failed for slot', slotId, err);
        }
      }
      if (Date.now() >= deadline) {
        finish();
        return;
      }
      const wait = convId ? delay : STOP_CANCEL_CONV_ID_POLL_MS;
      if (convId) delay = Math.min(delay * 2, STOP_CANCEL_RETRY_MAX_MS);
      setTimeout(() => void attempt(), wait);
    };
    void attempt();
  });

  return { isAcknowledged: () => acknowledged, convId: () => addressedConvId, done };
}

/**
 * Points a slot whose Stop was not acknowledged at the conversation the run
 * belongs to, and flags it so the remote-run poll reloads it once the server
 * settles. `isTemp` has to go: `invalidateConversation` skips temp slots, so the
 * poll would finish without ever replacing the local "stopped" bubble.
 * Leaves the slot alone if it has moved on to another run or conversation.
 */
function followRemoteRun(slotId: string, convId: string): void {
  const slot = useChatStore.getState().slots[slotId];
  if (!slot || slot.isStreaming || slot.stopping) return;
  if (slot.convId && slot.convId !== convId) return;
  useChatStore.getState().updateSlot(slotId, { convId, isTemp: false, remoteRunInProgress: true });
}

/** The assistant row a finished turn ends on, or null when the turn kept no reply (the history ends on the question). */
function turnReply(messages: ThreadMessageLike[]): ThreadMessageLike | null {
  const last = messages[messages.length - 1];
  return last?.role === 'assistant' ? last : null;
}

type AnswerCustom = { messageId?: string; searchDegraded?: boolean; accessNotices?: AccessNotice[] };
const answerCustom = (m: ThreadMessageLike | undefined) => m?.metadata?.custom as AnswerCustom | undefined;

/** Whether `row` is the assistant answer `target` names: by row id, or by the backend message id when the row id differs from it. */
function isAnswerRow(row: ThreadMessageLike, target: string | null | undefined, backendId?: string | null): boolean {
  if (row.role !== 'assistant') return false;
  if (target && row.id === target) return true;
  const id = backendId ?? target;
  return Boolean(id) && (row.id === id || answerCustom(row)?.messageId === id);
}

/**
 * The partial-search note belongs to an answer, so it is kept on the message (`metadata.custom.searchDegraded`) and shown from there: an
 * old answer that stays on screen keeps its note, and a new answer brings its own. A list reloaded from the server carries no flags,
 * so the notes of the answers already on screen are carried over by row id or backend message id. `mark` is the answer whose final
 * event said its search was partial; `fresh` is an answer that was just replaced, so its old note is not carried.
 */
function withSearchDegraded(
  next: ThreadMessageLike[],
  previous: ThreadMessageLike[],
  opts: { mark?: ThreadMessageLike | null; fresh?: string | null; degraded?: boolean; notices?: AccessNotice[] } = {},
): ThreadMessageLike[] {
  // The access notices (a source syncing or updating permissions) are kept on the answer the same way as the partial-search note.
  const flaggedBefore = previous.filter((m) => m.role === 'assistant' && (answerCustom(m)?.searchDegraded === true || answerCustom(m)?.accessNotices?.length));
  return next.map((row) => {
    if (row.role !== 'assistant') return row;
    const marked = Boolean(opts.mark) && row.id === opts.mark!.id;
    const before = !isAnswerRow(row, opts.fresh ?? null)
      ? flaggedBefore.find((p) => isAnswerRow(row, typeof p.id === 'string' ? p.id : null, answerCustom(p)?.messageId))
      : undefined;
    const degraded = (marked && opts.degraded !== false) || answerCustom(before)?.searchDegraded === true;
    const notices = marked && opts.notices?.length ? opts.notices : answerCustom(before)?.accessNotices;
    if (!degraded && !notices?.length) return row;
    return {
      ...row,
      metadata: { ...row.metadata, custom: { ...row.metadata?.custom, ...(degraded ? { searchDegraded: true } : {}), ...(notices?.length ? { accessNotices: notices } : {}) } },
    };
  });
}

/**
 * Builds the post-stop message list for the offline fallback (grace-timeout
 * abort) — commits whatever text streamed so far as a locally-synthesized
 * `status: 'stopped'` message, since the hard abort means we'll never see
 * the backend's own persisted version.
 *
 * - Regenerate: replaces the target message in place (leaves it untouched,
 *   i.e. the pre-regenerate answer, if nothing new streamed yet).
 * - New message: replaces the trailing placeholder assistant row, or drops
 *   it entirely if the stream was stopped before any tokens arrived (e.g.
 *   during "Thinking") — an empty "stopped" bubble is just noise.
 *
 * The committed text is model output, not a backend-saved answer, so its
 * markers are stripped: once committed it renders as `persisted` and any
 * marker left in it would become a card.
 */
function buildStoppedMessages(
  messages: ThreadMessageLike[],
  regenerateMessageId: string | null,
  rawStreamingContent: string,
  pendingAskUserQuestion?: PendingAskUserQuestion | null,
  streamingAssistantId?: string | null,
): ThreadMessageLike[] {
  const streamingContent = stripAnswerMarkers(rawStreamingContent);
  if (regenerateMessageId) {
    if (!streamingContent) return messages;
    return messages.map((m) =>
      isAnswerRow(m, regenerateMessageId)
        ? {
            ...m,
            content: [{ type: 'text' as const, text: streamingContent }],
            // The streamed text replaces the old answer, so the old answer's note goes with it.
            metadata: { ...m.metadata, custom: { ...m.metadata?.custom, status: 'stopped' as const, searchDegraded: undefined } },
          }
        : m
    );
  }
  const last = messages[messages.length - 1];
  const targetId = streamingAssistantId
    ?? (last?.role === 'assistant' && typeof last.id === 'string' ? last.id : null);
  if (!targetId) return messages;
  const targetIdx = messages.findIndex((m) => m.role === 'assistant' && m.id === targetId);
  if (targetIdx < 0) return messages;

  if (streamingContent) {
    return messages.map((m) =>
      m.id === targetId
        ? {
            ...m,
            content: [{ type: 'text' as const, text: streamingContent }],
            metadata: { ...m.metadata, custom: { ...m.metadata?.custom, status: 'stopped' as const } },
          }
        : m
    );
  }

  const targetIsLast = targetIdx === messages.length - 1;
  const targetIsCard = Boolean(
    pendingAskUserQuestion && targetId === pendingAskUserQuestion.assistantMessageId,
  );
  if (targetIsLast && !targetIsCard) return messages.slice(0, -1);
  return messages;
}

/**
 * `_id` of the stopped reply that ends the latest turn of a raw history page,
 * which `loadHistoricalMessages` drops when it was saved empty.
 */
export function trailingStoppedReplyId(raw: ConversationMessage[] | null | undefined): string | null {
  for (let i = (raw?.length ?? 0) - 1; i >= 0; i--) {
    const msg = raw![i];
    if (msg.messageType === 'user_query') return null;
    if (msg.messageType === 'bot_response') return msg.status === 'stopped' ? String(msg._id) : null;
  }
  return null;
}

/**
 * A stopped run can come back from the server with an empty answer even
 * though text was on screen (older query services saved "" on cancel). Fills
 * the stopped row from what streamed so Stop never blanks the answer.
 * `appendMessageId` is the persisted reply's `_id`; feedback, fork and
 * regenerate need it on an appended row.
 */
export function keepStreamedTextOnStoppedAnswer(
  messages: ThreadMessageLike[],
  targetId: string | null | undefined,
  rawStreamedText: string,
  citationMaps: CitationMaps | null | undefined,
  appendId?: string | null,
  appendMessageId?: string | null,
): ThreadMessageLike[] {
  const text = stripAnswerMarkers(rawStreamedText ?? '').trim();
  if (!text) return messages;
  const last = messages[messages.length - 1];
  // History drops an empty stopped reply with no activity, so the turn ends on
  // the question; the partial then needs a row of its own.
  if (!targetId && appendId && last?.role === 'user') {
    return [
      ...messages,
      {
        id: appendId,
        role: 'assistant' as const,
        content: [{ type: 'text' as const, text }],
        metadata: {
          custom: {
            status: 'stopped' as const,
            ...(appendMessageId ? { messageId: appendMessageId } : {}),
            ...(citationMaps ? { citationMaps } : {}),
          },
        },
      },
    ];
  }
  const target = targetId
    ? messages.find((m) => m.role === 'assistant' && m.id === targetId)
    : (last?.role === 'assistant' ? last : undefined);
  if (!target) return messages;
  const custom = (target.metadata?.custom ?? {}) as Record<string, unknown>;
  if (custom.status !== 'stopped' || getThreadMessagePlainText(target).trim()) return messages;
  const savedMaps = custom.citationMaps as CitationMaps | undefined;
  const hasSavedCitations = Boolean(savedMaps && Object.keys(savedMaps.citations ?? {}).length);
  return messages.map((m) =>
    m === target
      ? {
          ...m,
          content: [{ type: 'text' as const, text }],
          metadata: {
            ...m.metadata,
            custom: {
              ...custom,
              ...(!hasSavedCitations && citationMaps ? { citationMaps } : {}),
            },
          },
        }
      : m,
  );
}

/** `repliedInChat`: the message being sent answers this open card, so it shows as closed. */
function stampPendingCardOnMessages(
  messages: ThreadMessageLike[],
  pending: PendingAskUserQuestion | null | undefined,
  repliedInChat = false,
): ThreadMessageLike[] {
  if (!pending?.payload || !pending.assistantMessageId) return messages;
  const idx = messages.findIndex(
    (row) => row.role === 'assistant' && row.id === pending.assistantMessageId,
  );
  if (idx < 0) return messages;
  const row = messages[idx];
  const prevCustom = (row.metadata?.custom ?? {}) as Record<string, unknown>;
  const stamped = prevCustom.persistedAskUserQuestion as PendingAskUserQuestion['payload'] | undefined;
  if (stamped && !repliedInChat) return messages;
  const payload = stamped ?? pending.payload;
  const next = messages.slice();
  next[idx] = {
    ...row,
    metadata: {
      ...row.metadata,
      custom: {
        ...prevCustom,
        persistedAskUserQuestion: repliedInChat ? { ...payload, repliedInChat: true } : payload,
      },
    },
  };
  return next;
}

function exactAssistantRowIdForBackendMessage(
  messages: ThreadMessageLike[],
  backendMessageId: string,
): string | null {
  const row = messages.find((m) => {
    if (m.role !== 'assistant') return false;
    if (m.id === backendMessageId) return true;
    const custom = m.metadata?.custom as { messageId?: string } | undefined;
    return custom?.messageId === backendMessageId;
  });
  return row && typeof row.id === 'string' ? row.id : null;
}

function assistantRowIdForBackendMessage(
  messages: ThreadMessageLike[],
  backendMessageId: string,
): string {
  const exact = exactAssistantRowIdForBackendMessage(messages, backendMessageId);
  if (exact) return exact;
  const last = [...messages].reverse().find((m) => m.role === 'assistant');
  return last && typeof last.id === 'string' ? last.id : backendMessageId;
}

/**
 * The user row before an assistant row, with its text swapped for an edited question and, when
 * the edit replaced them, its files (an empty list clears them).
 */
export function withEditedQuestion(
  messages: ThreadMessageLike[],
  backendMessageId: string,
  text: string,
  attachments?: AttachmentRef[],
): ThreadMessageLike[] {
  const idx = messages.findIndex((m) => {
    if (m.role !== 'assistant') return false;
    const custom = m.metadata?.custom as { messageId?: string } | undefined;
    return m.id === backendMessageId || custom?.messageId === backendMessageId;
  });
  if (idx <= 0 || messages[idx - 1].role !== 'user') return messages;
  return messages.map((m, i) => {
    if (i !== idx - 1) return m;
    const custom = (m.metadata?.custom ?? {}) as Record<string, unknown>;
    return {
      ...m,
      content: [{ type: 'text' as const, text }],
      ...(attachments === undefined
        ? {}
        : {
            metadata: {
              ...m.metadata,
              custom: { ...custom, attachments: attachments.length > 0 ? attachments : undefined },
            },
          }),
    };
  });
}

/**
 * Whether a failure says the server did not take an edit: its own error (a run that failed) or a
 * request it refused before streaming. A connection that dropped says nothing, the run goes on there
 * and may save the edit, so what it did is read back from the server instead of guessed.
 */
export function editWasNotTaken(error: unknown): boolean {
  return (
    error instanceof ChatStreamError ||
    (error instanceof StreamError && error.status !== undefined)
  );
}

/**
 * Whether the answer row shows a question card the server asks again on a regenerate: one stored on
 * it, or one waiting on the slot. A card closed by a typed reply is not one: that regenerate only
 * redoes the reply to the typed message.
 */
function rowHasQuestionCard(
  messages: ThreadMessageLike[],
  backendMessageId: string,
  pending: PendingAskUserQuestion | null | undefined,
): boolean {
  const row = messages.find((m) => {
    if (m.role !== 'assistant') return false;
    const custom = m.metadata?.custom as { messageId?: string } | undefined;
    return m.id === backendMessageId || custom?.messageId === backendMessageId;
  });
  if (!row) return false;
  const custom = row.metadata?.custom as
    | { persistedAskUserQuestion?: { repliedInChat?: boolean } }
    | undefined;
  const stored = custom?.persistedAskUserQuestion;
  if (stored) return !stored.repliedInChat;
  return pending?.assistantMessageId === row.id;
}

/** The answer row without the question card it asked, for a turn that is being asked again. */
export function withoutQuestionCard(
  messages: ThreadMessageLike[],
  backendMessageId: string,
): ThreadMessageLike[] {
  return messages.map((m) => {
    if (m.role !== 'assistant') return m;
    const custom = m.metadata?.custom as { messageId?: string } | undefined;
    if (m.id !== backendMessageId && custom?.messageId !== backendMessageId) return m;
    const {
      persistedAskUserQuestion: _card,
      persistedAskUserQuestionAnswers: _answers,
      ...rest
    } = (m.metadata?.custom ?? {}) as Record<string, unknown>;
    return { ...m, metadata: { ...m.metadata, custom: rest } };
  });
}

function pendingFromUnansweredPersistedCard(
  messages: ThreadMessageLike[],
  assistantId: string,
): PendingAskUserQuestion | null {
  const row = messages.find((m) => m.role === 'assistant' && m.id === assistantId);
  const custom = row?.metadata?.custom as {
    persistedAskUserQuestion?: PendingAskUserQuestion['payload'];
    persistedAskUserQuestionAnswers?: PendingAskUserQuestion['answers'];
  } | undefined;
  const payload = custom?.persistedAskUserQuestion;
  const answers = custom?.persistedAskUserQuestionAnswers ?? {};
  if (!payload || Object.keys(answers).length > 0) return null;
  return {
    assistantMessageId: assistantId,
    payload,
    answers: {},
    status: 'pending',
  };
}

/** A submitted card's answers reached the model; a failed resume's did not. */
function sentAnswersAfterAsk(
  previous: PendingAskUserQuestion,
): Pick<PendingAskUserQuestion, 'sentAnswers'> {
  const sent = previous.status === 'submitted' ? previous.answers : previous.sentAnswers;
  return sent && Object.keys(sent).length > 0 ? { sentAnswers: sent } : {};
}

/** A card event that has questions to show. Anything else (e.g. a refused call's error text) must not
 * cost the user the answer that is streaming in. */
export function isShowableAskUserQuestion(data: SSEAskUserQuestionEvent | undefined): boolean {
  const toolData = data?.toolData;
  return Boolean(
    toolData &&
      toolData.name === 'ask_user_question' &&
      Array.isArray(toolData.questions) &&
      toolData.questions.length > 0
  );
}

function applyAskUserQuestionSse(
  slotId: string,
  data: SSEAskUserQuestionEvent,
  assistantRowId: string
): void {
  if (!isShowableAskUserQuestion(data)) return;
  const toolData = data.toolData;
  const slot = useChatStore.getState().slots[slotId];
  const previous = slot?.pendingAskUserQuestion;
  const messages =
    previous && previous.assistantMessageId !== assistantRowId
      ? stampPendingCardOnMessages(slot?.messages ?? [], previous)
      : slot?.messages;
  const sameRow = previous?.assistantMessageId === assistantRowId;
  // A resume that asks what the user just answered adds nothing to answer;
  // reopening the card would send them through the same choice again.
  if (
    sameRow &&
    previous?.status === 'submitted' &&
    Object.keys(previous.answers).length > 0 &&
    !hasUnansweredQuestions(mergeAskUserQuestionPayloads(previous.payload, toolData), previous.answers)
  ) {
    return;
  }
  useChatStore.getState().updateSlot(slotId, {
    ...(messages && messages !== slot?.messages ? { messages } : {}),
    pendingAskUserQuestion: {
      assistantMessageId: assistantRowId,
      payload: sameRow && previous
        ? mergeAskUserQuestionPayloads(previous.payload, toolData)
        : toolData,
      answers: sameRow && previous ? previous.answers : {},
      ...(sameRow && previous
        ? sentAnswersAfterAsk(previous)
        : {}),
      status: 'pending',
    },
  });
}

/**
 * If the last message is the empty placeholder assistant for an in-flight stream,
 * replace it with the error text. Otherwise append a new assistant error row.
 * `streamError` (the server's error code and details) lets the thread offer a fix.
 */
/** What a turn's failure before its first frame is to a card's send (CARDFAIL): by its HTTP status and code. */
export function sendFailureOfError(error: unknown): SendFailure {
  if (error instanceof DurableAdmissionError) return sendFailureOf(error.status, error.code);
  if (error instanceof StreamError) return sendFailureOf(error.status, error.code ?? null);
  return sendFailureOf(undefined, null);
}

function pendingAfterStreamFailure(
  pending: PendingAskUserQuestion | null | undefined,
): PendingAskUserQuestion | null {
  if (!pending) return null;
  return { ...pending, status: 'pending' };
}

/** A failed resume keeps the card (with its selections) instead of an error
 *  row, and the pending card hides that row's text — so the only place left
 *  to tell the user the answers never reached the model is a toast. */
function notifyAskUserQuestionResumeFailed(detail: string): void {
  toast.error(i18n.t('chatStream.askQuestionResumeFailed'), {
    ...(detail ? { description: detail } : {}),
  });
}

function notifyToolApprovalFailed(detail: string): void {
  toast.error(i18n.t('chat.toolApproval.answerFailed'), {
    ...(detail ? { description: detail } : {}),
  });
}

function withStreamingErrorMessage(
  currentMessages: ThreadMessageLike[],
  errorText: string,
  streamError?: ChatStreamErrorInfo
): ThreadMessageLike[] {
  const withError = (metadata?: ThreadMessageLike['metadata']) =>
    streamError ? { metadata: { ...metadata, custom: { ...metadata?.custom, streamError } } } : {};
  const last = currentMessages[currentMessages.length - 1];
  if (last?.role === 'assistant' && getThreadMessagePlainText(last).trim() === '') {
    return [
      ...currentMessages.slice(0, -1),
      { ...last, content: [{ type: 'text' as const, text: errorText }], ...withError(last.metadata) },
    ];
  }
  return [
    ...currentMessages,
    { role: 'assistant' as const, content: [{ type: 'text' as const, text: errorText }], ...withError() },
  ];
}

function statusMessageFromConnectedEvent(data: SSEConnectedEvent): StatusMessage {
  const raw = typeof data?.message === 'string' ? data.message.trim() : '';
  const looksTechnical =
    raw.length === 0 ||
    /^sse\b/i.test(raw) ||
    /\bconnection\s+established\b/i.test(raw);
  return {
    id: 'status-connected',
    status: 'connected',
    message: looksTechnical ? 'Connected — working on your request…' : raw,
    timestamp: new Date().toISOString(),
  };
}

/** Clear partial stream output when the backend emits `restreaming` (citation verify / re-parse). */
function statusMessageRestreaming(): StatusMessage {
  return {
    id: `status-restreaming-${Date.now()}`,
    status: 'restreaming',
    message: i18n.t('chatStream.refiningResponse'),
    timestamp: new Date().toISOString(),
  };
}

interface StatusDwellScheduler {
  /** Force-apply a status immediately (bypasses dwell window). Used by restreaming. */
  applyStatus: (msg: StatusMessage | null) => void;
  /** Enqueue a status; coalesces bursts so each visible status dwells ≥ `minDwellMs`. */
  scheduleStatus: (msg: StatusMessage) => void;
  /** Drop any pending status and cancel the dwell + idle timers. */
  cancelPendingStatus: () => void;
  /** Start the quiet-stream watchdog (see `createStatusDwellScheduler`). */
  armIdleStatus: () => void;
  /** Retire the watchdog for the rest of the run — the answer is settled. */
  stopIdleStatus: () => void;
}

/** Placeholder the watchdog shows when a stream goes quiet with no status. */
function statusMessageIdleThinking(): StatusMessage {
  return {
    id: `status-idle-${Date.now()}`,
    status: 'calling_llm',
    message: i18n.t('chatStream.thinkingFallback'),
    timestamp: new Date().toISOString(),
  };
}

/**
 * Minimum-dwell scheduler for SSE status messages.
 *
 * Backend can emit bursts of status events (planning → executing → analyzing
 * → generating) within a few ms. Writing each one directly to the store
 * overwrites the previous before React paints, so users see statuses blink
 * past. This scheduler guarantees each visible status stays for at least
 * `minDwellMs`. Events arriving inside the window are coalesced — latest
 * wins — and flushed when the window elapses.
 *
 * It also owns the quiet-stream watchdog. Answer text clears the status line
 * (see `onChunk`), but the run is often far from over: the model can spend
 * many seconds composing tool-call arguments, during which the backend emits
 * nothing at all. Rather than depend on which event happens to re-arm the
 * status next — several are gated to root runs, and one lands only after the
 * silence — the watchdog re-shows "Thinking…" whenever the stream falls quiet
 * for `idleMs`. `stopIdleStatus` retires it once the answer is settled, so it
 * never appears beneath a finished reply.
 */
function createStatusDwellScheduler(
  slotId: string,
  isCurrentRun: () => boolean,
  minDwellMs = 400,
  idleMs = 900
): StatusDwellScheduler {
  let lastStatusAt = 0;
  let statusTimer: ReturnType<typeof setTimeout> | null = null;
  let pendingStatus: StatusMessage | null = null;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  let idleRetired = false;

  function clearIdleTimer(): void {
    if (idleTimer !== null) { clearTimeout(idleTimer); idleTimer = null; }
  }

  function applyStatus(msg: StatusMessage | null): void {
    if (!isCurrentRun()) return;
    lastStatusAt = Date.now();
    // A real status supersedes whatever the watchdog was about to show.
    clearIdleTimer();
    useChatStore.getState().updateSlot(slotId, { currentStatusMessage: msg });
  }

  function armIdleStatus(): void {
    if (idleRetired) return;
    clearIdleTimer();
    idleTimer = setTimeout(() => {
      idleTimer = null;
      if (idleRetired || !isCurrentRun()) return;
      // Re-read live state: the stream may have ended, or a real status may
      // have landed, between arming and firing.
      const slot = useChatStore.getState().slots[slotId];
      if (!slot?.isStreaming || slot.currentStatusMessage) return;
      applyStatus(statusMessageIdleThinking());
    }, idleMs);
  }

  function stopIdleStatus(): void {
    idleRetired = true;
    clearIdleTimer();
    // `TEXT_MESSAGE_END` unconditionally shows "Thinking…" the moment the
    // final answer's last token lands (it can't yet tell narration from the
    // final answer — see agui-event-handler.ts). Callers reach here once the
    // answer is actually settled, so clear that leftover status instead of
    // letting it sit under the finished reply until onComplete.
    applyStatus(null);
  }

  function scheduleStatus(msg: StatusMessage): void {
    const elapsed = Date.now() - lastStatusAt;
    if (elapsed >= minDwellMs) {
      if (statusTimer !== null) { clearTimeout(statusTimer); statusTimer = null; }
      pendingStatus = null;
      applyStatus(msg);
      return;
    }
    pendingStatus = msg;
    if (statusTimer !== null) return;
    statusTimer = setTimeout(() => {
      statusTimer = null;
      if (pendingStatus) {
        const m = pendingStatus;
        pendingStatus = null;
        applyStatus(m);
      }
    }, minDwellMs - elapsed);
  }

  function cancelPendingStatus(): void {
    if (statusTimer !== null) { clearTimeout(statusTimer); statusTimer = null; }
    pendingStatus = null;
    // Terminal handlers call this; leaving a timer armed would let it write to
    // a slot that has already started its next stream.
    clearIdleTimer();
  }

  return { applyStatus, scheduleStatus, cancelPendingStatus, armIdleStatus, stopIdleStatus };
}

/**
 * Stream a message for a specific slot.
 *
 * The function writes to `slots[slotId]` in Zustand — it does NOT
 * need the slot to be active. A background slot will accumulate
 * messages silently.
 *
 * @param slotId  — stable slot key in the store dictionary
 * @param query   — user's plain-text question
 * @param request — full StreamChatRequest (model, chatMode, filters, etc.). For **agent**
 *   streams, `ChatApi.streamMessage` always sends `filters: { apps, kb }` and `tools: [...]`
 *   — empty arrays mean no knowledge / no tools (same explicit contract).
 */
/** The row this run's card lives on, or undefined when the run had no card —
 *  a positional guess would hand back an unrelated turn and let the caller
 *  overwrite that turn's persisted transcript. */
function findAskUserQuestionRow(
  finalMessages: ThreadMessageLike[],
  pending: PendingAskUserQuestion | null | undefined,
): ThreadMessageLike | undefined {
  if (!pending) return undefined;
  const assistants = finalMessages.filter((m) => m.role === 'assistant');
  const byId = assistants.find((m) => m.id === pending.assistantMessageId);
  if (byId) return byId;
  // The live placeholder id does not survive the reload; the card this run just
  // asked is the newest persisted one, not the first.
  return [...assistants].reverse().find((m) => {
    const custom = m.metadata?.custom as { persistedAskUserQuestion?: unknown } | undefined;
    return Boolean(custom?.persistedAskUserQuestion);
  });
}

function textFromLiveParts(parts: MessagePart[]): string {
  for (let i = parts.length - 1; i >= 0; i -= 1) {
    const part = parts[i];
    if (part.type === 'text' && typeof part.content === 'string' && part.content.trim()) {
      return part.content;
    }
  }
  return '';
}

/** `live` plus the saved approval parts it lacks, keyed per run (UXINT-0: interaction ids repeat across runs). */
function withSavedApprovals(live: MessagePart[], saved: MessagePart[]): MessagePart[] {
  const key = (p: MessagePart) => `${p.runId ?? ''}:${p.interactionId ?? ''}`;
  const have = new Set(live.filter((p) => p.type === 'approval').map(key));
  const missing = saved.filter((p) => p.type === 'approval' && !have.has(key(p)));
  return missing.length ? [...live, ...missing] : live;
}

/** What the server records on an answer's final text part, which the live parts never carry. */
const FINAL_PART_FACTS = ['notFound', 'limits', 'runOutcome', 'truncatedBy', 'budgetScope'] as const;

/**
 * `live` with the saved answer's typed facts on its final text part: a card turn's live parts replace the ones the
 * server saved, so without this the reply would lose what only the saved final part holds (FU-2's not-found fact,
 * WP-89's outcome, a cut-short notice) until the conversation is opened again.
 */
export function withSavedFinalFacts(live: MessagePart[], saved: MessagePart[]): MessagePart[] {
  const savedFinal = [...saved].reverse().find((p) => p.type === 'text' && p.isFinal === true);
  const at = live.map((p) => p.type === 'text' && p.isFinal === true).lastIndexOf(true);
  if (savedFinal === undefined || at < 0) return live;
  const facts = Object.fromEntries(
    FINAL_PART_FACTS.filter((key) => savedFinal[key] !== undefined && live[at][key] === undefined).map((key) => [key, savedFinal[key]]),
  );
  if (Object.keys(facts).length === 0) return live;
  return live.map((part, i) => (i === at ? { ...part, ...facts } : part));
}

/** Live trailing text is the answer (`AnswerContent`). Mark it `isFinal` so
 *  the activity timeline does not also render it after we persist `streamingParts`. */
function withFinalAnswerMarked(parts: MessagePart[]): MessagePart[] {
  let lastText = -1;
  for (let i = parts.length - 1; i >= 0; i -= 1) {
    if (parts[i].type === 'text') {
      lastText = i;
      break;
    }
  }
  if (lastText < 0 || parts[lastText].isFinal) return parts;
  return parts.map((part, i) =>
    i === lastText && part.type === 'text' ? { ...part, isFinal: true } : part,
  );
}

export async function streamMessageForSlot(
  slotId: string,
  query: string,
  request: StreamChatRequest,
  options?: {
    resumeAskUserQuestion?: boolean;
    /** An answer to a tool approval card: it carries on this assistant row, with no user bubble. */
    reuseRowId?: string;
    /**
     * CARDFAIL: the turn's run id, the same on every retry of one answer, so the server admits it once and a resend
     * after a lost reply replays the first.
     */
    runId?: string;
    /**
     * CARDFAIL: told once whether the turn reached the server (its first frame) or failed before it did. With it,
     * a failure before the first frame leaves the card as it is and shows no toast: the card's send says it.
     */
    onSendOutcome?: (outcome: SendAttempt<true>) => void;
  }
): Promise<void> {
  const store = useChatStore.getState();
  const slot = store.slots[slotId];
  if (!slot) return;
  // A run left over from before a refresh is still writing this thread; a second
  // run would collide with it, so the UI entry points hold the send back.
  if (slot.remoteRunInProgress) return;

  // A Stop-then-send (or a second submit while the previous run is still
  // winding down) must kill the previous fetch so its callbacks cannot
  // clobber the new run. `cancelStreamForSlot`'s grace timer already no-ops
  // when `runId` changes; this abort is what actually drops the old SSE.
  slot.abortController?.abort();

  // Stop-then-send: commit the cancelled run's partial locally so the new
  // turn does not drop it. The grace timer will no-op once `runId` changes.
  const baseMessages = slot.stopping
    ? buildStoppedMessages(
      slot.messages,
      slot.regenerateMessageId,
      slot.streamingContent,
      slot.pendingAskUserQuestion,
      slot.streamingAssistantId,
    )
    : slot.messages;

  // Create an abort controller scoped to this stream
  const abortController = new AbortController();
  // Client-generated run identifier so a later Stop can target this exact
  // run (see `ChatApi.cancelStream` / `cancelStreamForSlot`).
  const runId = options?.runId ?? generateRunId();
  const streamRunId = runId ?? null;
  if (runId) request.runId = runId;
  let sendSettled = false;
  const settleSend = (outcome: SendAttempt<true>): boolean => {
    if (sendSettled) return false;
    sendSettled = true;
    options?.onSendOutcome?.(outcome);
    return options?.onSendOutcome !== undefined;
  };
  // A failure before the turn reached the server, with a card send to say it: the card is left as it was.
  const cardSaysFailure = (error: unknown): boolean =>
    !sendSettled && settleSend({ failed: sendFailureOfError(error) });

  // Ephemeral empty assistant so the in-progress turn has a dedicated "last
  // assistant" message. Pairs with MessageList: only the last assistant whose
  // preceding user text matches `streamingQuestion` receives live SSE props
  // (avoids `!content` false positives on older agent turns).
  //
  // Ask-user resume: keep the existing assistant row and do not append a
  // "User selections:" user bubble. MessageList still matches live SSE to
  // that row because `streamingQuestion` is the original user text.
  const resumeAskUserQuestion = options?.resumeAskUserQuestion === true;
  const reuseRowId = resumeAskUserQuestion ? undefined : options?.reuseRowId;
  const cardAssistantId = resumeAskUserQuestion
    ? slot.pendingAskUserQuestion?.assistantMessageId
    : reuseRowId;
  let lastUserText = '';
  let lastAssistantId: string | undefined;
  let lastAssistantParts: MessagePart[] = [];
  if (cardAssistantId) {
    const cardIdx = baseMessages.findIndex(
      (row) => row.role === 'assistant' && row.id === cardAssistantId,
    );
    if (cardIdx >= 0) {
      lastAssistantId = cardAssistantId;
      const persisted = baseMessages[cardIdx].metadata?.custom?.persistedParts;
      if (Array.isArray(persisted)) lastAssistantParts = persisted as MessagePart[];
      for (let i = cardIdx - 1; i >= 0; i -= 1) {
        if (baseMessages[i].role === 'user') {
          lastUserText = getThreadMessagePlainText(baseMessages[i]);
          break;
        }
      }
    }
  }
  // A card id the thread does not carry must not fall through to "newest
  // assistant row": reusing an unrelated turn overwrites the answer already
  // there. Start a fresh turn instead — misplaced at worst, not destructive.
  // An approval card is only answered on the latest reply, which is the newest row anyway.
  const cardRowMissing = resumeAskUserQuestion && Boolean(cardAssistantId) && !lastAssistantId;
  if (!lastAssistantId && !cardRowMissing) {
    for (let i = baseMessages.length - 1; i >= 0; i -= 1) {
      const row = baseMessages[i];
      if (!lastAssistantId && row.role === 'assistant' && typeof row.id === 'string') {
        lastAssistantId = row.id;
        const persisted = row.metadata?.custom?.persistedParts;
        if (Array.isArray(persisted)) lastAssistantParts = persisted as MessagePart[];
      }
      if (!lastUserText && row.role === 'user') {
        lastUserText = getThreadMessagePlainText(row);
      }
      if (lastAssistantId && lastUserText) break;
    }
  }
  const reuseExistingAssistant = (resumeAskUserQuestion || Boolean(reuseRowId)) && Boolean(lastAssistantId);
  // Typing while a card is open answers it, as the server reads it: the card closes.
  const repliedToOpenCard =
    !resumeAskUserQuestion && !reuseRowId && slot.pendingAskUserQuestion?.status === 'pending';
  // A typed reply answers the card instead: an answer of it still waiting to be resent is dropped.
  if (repliedToOpenCard && slot.pendingAskUserQuestion) cancelCardSend(questionSendKey(slot.pendingAskUserQuestion.assistantMessageId));
  const messagesWithPreviousCard = reuseExistingAssistant
    ? baseMessages
    : stampPendingCardOnMessages(baseMessages, slot.pendingAskUserQuestion, repliedToOpenCard);
  const pendingAssistantId = reuseExistingAssistant
    ? lastAssistantId!
    : createPendingAssistantId();
  const lastThreadUserText = (() => {
    for (let i = baseMessages.length - 1; i >= 0; i -= 1) {
      if (baseMessages[i].role === 'user') return getThreadMessagePlainText(baseMessages[i]);
    }
    return '';
  })();
  const streamingQuestion = reuseExistingAssistant && lastUserText
    ? lastUserText
    : resumeAskUserQuestion && lastThreadUserText
      ? lastThreadUserText
      : query;

  const newAssistantRow = {
    role: 'assistant' as const,
    id: pendingAssistantId,
    content: [{ type: 'text' as const, text: '' }],
  };
  const resumeMessages = reuseExistingAssistant
    ? baseMessages
    : resumeAskUserQuestion
      ? [...messagesWithPreviousCard, newAssistantRow]
      : [
        ...messagesWithPreviousCard,
        {
          role: 'user' as const,
          content: [{ type: 'text' as const, text: query }],
          ...(hasAnyFilter(request.filters)
            ? {
                metadata: {
                  custom: {
                    filters: request.filters,
                    createdAt: new Date().toISOString(),
                    ...(request.appliedFilters ? { appliedFilters: request.appliedFilters } : {}),
                    ...(request.attachments?.length ? { attachments: request.attachments } : {}),
                  },
                },
              }
            : {
                metadata: {
                  custom: {
                    createdAt: new Date().toISOString(),
                    ...(request.agentId && request.appliedFilters ? { appliedFilters: request.appliedFilters } : {}),
                    ...(request.attachments?.length ? { attachments: request.attachments } : {}),
                  },
                },
              }),
        },
        newAssistantRow,
      ];

  // Append user message + placeholder assistant + set streaming state atomically
  store.updateSlot(slotId, {
    isStreaming: true,
    streamingQuestion,
    streamingContent: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    streamingParts: reuseExistingAssistant ? lastAssistantParts : [],
    abortController,
    runId: streamRunId,
    stopping: false,
    durableRun: null,
    lastDurableRun: null,
    routePending: true,
    streamingAssistantId: pendingAssistantId,
    threadAgentId: request.agentId ?? slot.threadAgentId ?? null,
    // `request.agentStreamTools` is `undefined` when every tool is
    // selected (see `buildStreamChatRequestForSlot` in runtime.ts) — must
    // map to `null` here, NOT `[]`: on `ChatSlot.agentStreamTools`, `null`
    // means "all tools" and `[]` means "no tools" (see that field's
    // docstring), the opposite of what an unfiltered selection means.
    ...(request.agentId
      ? { agentStreamTools: request.agentStreamTools ?? null }
      : {}),
    ...(repliedToOpenCard ? { pendingAskUserQuestion: null } : {}),
    messages: resumeMessages,
  });

  // The engine `/engine` held for this new chat (CMDUI2) goes with this message; nothing is held after it.
  const newChatEngine = request.codingEngine ?? null;
  if (newChatEngine !== null) store.setPendingCodingEngine(null);
  if (request.repository) useRepositoryStore.getState().setPending(null);
  // A turn's gates are that message's alone: the next one starts with none.
  if (request.gates) useRunGatesStore.getState().clear();

  // For new conversations, push a pending sidebar entry keyed by slotId
  const isNewConversation = slot.isTemp;
  if (isNewConversation) {
    store.addPendingConversation(slotId);
  }

  debugLog.flush('stream-started', { slotId, convId: slot.convId, isNew: isNewConversation });

  // ── Time-throttled content + citation accumulator ──────────────────
  // Flushes streamingContent + streamingCitationMaps to Zustand at most
  // once per ~16 ms (≈60 fps).
  //
  // WHY NOT requestAnimationFrame:
  // rAF is a macrotask that only runs when the browser is idle. When the
  // server sends many SSE chunks in a rapid burst (all arrive as microtasks
  // in the same event-loop turn), rafPending stays `true` through the entire
  // burst and the single rAF fires at the very end — producing one giant
  // update instead of incremental ones. A time-based throttle avoids this:
  //   • First chunk → flush immediately (content appears right away).
  //   • Subsequent chunks within 16 ms → schedule a setTimeout for the
  //     remaining window (still fires between bursts, not just at the end).
  //   • Chunks arriving ≥16 ms apart → each flushes immediately.
  //
  // BACKGROUND THROTTLING: When this slot is NOT the active (visible) one,
  // no React component subscribes to its `streamingContent` — but each
  // `updateSlot()` still creates a new `slots` reference, causing ALL
  // subscriber selectors across the app to re-evaluate synchronously.
  // With N background streams at 60 fps each, that starves the main
  // thread and breaks the active chat's scroll tracking.  To avoid this,
  // background slots flush at a much lower cadence (200 ms).
  const BACKGROUND_FLUSH_MS = 200;
  // ACTIVE THROTTLING scales with answer size. 16ms (~60fps) is fine for a
  // short answer, but a large table/answer costs measurably more to
  // re-render per flush (block-splitting in `AnswerContent` keeps that cost
  // roughly proportional to the *tail* block, not the whole answer, but a
  // single tail block can itself still grow past a 16ms budget). Flushing
  // faster than the main thread can retire the resulting render only queues
  // up backlog — it doesn't make the UI feel faster.
  function activeFlushIntervalMs(contentLength: number): number {
    if (contentLength < 2000) return 16;
    if (contentLength < 8000) return 33;
    return 50;
  }
  let accumulatedContent = '';
  let pendingCitationMaps: ReturnType<typeof buildCitationMapsFromStreaming> | null = null;
  let lastCitationKey = ''; // JSON.stringify key for dedup
  let lastFlushTime = 0;
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  // When ask_user_question is received, stop accumulating answer_chunks so
  // only the question card is shown (not a partial streamed answer above it).
  let ignoreChunks = false;
  // Live agent-activity transcript (text/reasoning/tool_call/sub_agent),
  // built by `agui-event-handler.ts`'s `LivePartsBuilder` — piggybacks on
  // the same throttled flush as streamingContent so a burst of parts
  // updates doesn't cause its own separate wave of Zustand writes.
  let latestParts: MessagePart[] = [];

  // Minimum-dwell scheduler for SSE status messages (see
  // createStatusDwellScheduler for the rationale).
  const { applyStatus, scheduleStatus, cancelPendingStatus, armIdleStatus, stopIdleStatus } =
    createStatusDwellScheduler(slotId, () => slotIsOnRun(slotId, streamRunId));

  function flushContentToStore() {
    if (!slotIsOnRun(slotId, streamRunId)) return;
    debugLog.rafFlush();
    const citationMaps = pendingCitationMaps;
    if (citationMaps) {
      pendingCitationMaps = null;
    }
    useChatStore.getState().updateSlot(slotId, {
      streamingContent: accumulatedContent,
      streamingParts: reuseExistingAssistant && lastAssistantParts.length
        ? appendResumeParts(lastAssistantParts, latestParts)
        : latestParts,
      ...(citationMaps ? { streamingCitationMaps: citationMaps } : {}),
    });
  }

  /** Land the last throttled chunk so the grace timer commits all of it. */
  function flushPendingForStop() {
    if (flushTimer !== null) {
      clearTimeout(flushTimer);
      flushTimer = null;
      flushContentToStore();
    }
    cancelPendingStatus();
  }

  function scheduleFlush() {
    const now = Date.now();
    // Check activity on every call — adapts immediately when user switches.
    const isActive = useChatStore.getState().activeSlotId === slotId;
    const interval = isActive
      ? activeFlushIntervalMs(accumulatedContent.length)
      : BACKGROUND_FLUSH_MS;
    if (now - lastFlushTime >= interval) {
      // Enough time has passed — flush immediately.
      if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
      lastFlushTime = now;
      flushContentToStore();
    } else if (flushTimer === null) {
      // Within the throttle window — schedule a deferred flush.
      flushTimer = setTimeout(() => {
        flushTimer = null;
        lastFlushTime = Date.now();
        flushContentToStore();
      }, interval - (now - lastFlushTime));
    }
  }

  try {
    const route = turnRouteCallbacks(slotId, streamRunId, newChatEngine);
    await ChatApi.streamMessage(request, {
      onDurableRunAccepted: (durableRun) => {
        settleSend({ sent: true });
        route.onDurableRunAccepted?.(durableRun);
      },
      onLegacyTurn: () => {
        settleSend({ sent: true });
        route.onLegacyTurn?.();
      },
      onConnected: (data) => {
        // Before the slot guard: a Stop already settled the slot but still needs this id to cancel.
        if (streamRunId && pendingStopConvIds.has(streamRunId)) {
          const stopRaw = (data as SSEConnectedEvent | undefined)?.conversationId;
          const stopId = typeof stopRaw === 'string' ? stopRaw.trim() : '';
          if (stopId) pendingStopConvIds.set(streamRunId, stopId);
        }
        if (!slotIsOnRun(slotId, streamRunId)) return;
        if (isNewConversation) {
          const raw = (data as SSEConnectedEvent | undefined)?.conversationId;
          const earlyId = typeof raw === 'string' ? raw.trim() : '';
          if (earlyId) {
            // Sidebar title comes from the SSE `connected` payload (same value persisted
            // on the conversation row). No extra GET — avoids loading full message history.
            const rawConnectedTitle = (data as SSEConnectedEvent | undefined)?.title;
            const connectedTitle =
              typeof rawConnectedTitle === 'string' ? rawConnectedTitle.trim() : '';
            useChatStore.getState().connectNewConversation(slotId, earlyId, connectedTitle || null);
            debugLog.flush('connected-conv-id', { slotId, convId: earlyId });
          }
        }
        scheduleStatus(statusMessageFromConnectedEvent(data));
      },

      onRestreaming: () => {
        if (!slotIsOnRun(slotId, streamRunId)) return;
        if (flushTimer !== null) {
          clearTimeout(flushTimer);
          flushTimer = null;
        }
        cancelPendingStatus();
        accumulatedContent = '';
        lastCitationKey = '';
        pendingCitationMaps = null;
        latestParts = [];
        useChatStore.getState().updateSlot(slotId, {
          streamingContent: '',
          streamingCitationMaps: null,
          streamingParts: [],
        });
        applyStatus(statusMessageRestreaming());
      },

      onStatus: (data) => {
        if (!slotIsOnRun(slotId, streamRunId)) return;
        const statusMessage: StatusMessage = {
          id: `status-${Date.now()}`,
          status: data.status,
          message: data.message,
          timestamp: new Date().toISOString(),
        };
        if (data.status === 'calling_llm') {
          // A tool's last progress still waiting out its dwell must not come back after this.
          cancelPendingStatus();
          applyStatus(statusMessage);
        } else {
          scheduleStatus(statusMessage);
        }
      },

      onParts: (parts) => {
        if (!slotIsOnRun(slotId, streamRunId)) return;
        latestParts = parts;
        scheduleFlush();
      },

      onChunk: (data) => {
        if (!slotIsOnRun(slotId, streamRunId)) return;
        if (ignoreChunks) return;
        // A stopped run's final frame can carry an empty `accumulated`; taking
        // it would wipe the partial answer the user chose to keep.
        if (!data.accumulated && useChatStore.getState().slots[slotId]?.stopping) return;
        debugLog.chunk();
        accumulatedContent = data.accumulated;
        // Any answer text flowing right now supersedes a stale "Using X…"
        // status from an earlier tool call — clear it every time (not just
        // once per stream), otherwise it lingers above later chunks whenever
        // a status arrives *between* two text bursts (text → tool → text).
        if (data.accumulated.length > 0) {
          cancelPendingStatus();
          useChatStore.getState().updateSlot(slotId, { currentStatusMessage: null });
          armIdleStatus();
        }
        // Deduplicate citation maps: only stage a new maps object when
        // the serialized key changes (citations grow monotonically).
        if (data.citations && data.citations.length > 0) {
          const key = JSON.stringify(data.citations);
          if (key !== lastCitationKey) {
            lastCitationKey = key;
            pendingCitationMaps = buildCitationMapsFromStreaming(data.citations);
          }
        }
        scheduleFlush();
      },

      onArtifact: (data: SSEArtifactEvent) => {
        if (!data || !slotIsOnRun(slotId, streamRunId)) return;
        // Defensive guard: Python already suppresses STAGING artifacts before
        // emitting SSE events, so this branch should never fire in production.
        // It guards against accidental backend bypasses or future protocol changes.
        if (data.visibility === 'STAGING') return;
        const artifact: ChatArtifact = buildChatArtifact({
          id: data.artifactId,
          fileName: data.fileName,
          mimeType: data.mimeType,
          sizeBytes: data.sizeBytes,
          downloadUrl: data.downloadUrl,
          artifactType: data.artifactType,
          recordId: data.recordId,
          version: data.version,
          derivedFromCodeArtifactId: data.derivedFromCodeArtifactId,
          visibility: data.visibility,
          unavailable: data.unavailable,
        });
        const currentSlot = useChatStore.getState().slots[slotId];
        if (currentSlot) {
          useChatStore.getState().updateSlot(slotId, {
            artifacts: withArtifactCard(currentSlot.artifacts, artifact, data.lifecycle),
          });
        }
      },

      ...approvalStreamCallbacks(slotId),

      // WP-100 (C10.05): the run's spend as its stream pushes it.
      onRunCost: pushRunCost,
      ...planStreamCallbacks(slotId, () => slotIsOnRun(slotId, streamRunId)),

      onTurnStarted: (data) => {
        if (request.compareTurn && slotIsOnRun(slotId, streamRunId)) onPrimaryTurnStarted(slotId, data);
      },
      onAskUserQuestion: (data: SSEAskUserQuestionEvent) => {
        if (!slotIsOnRun(slotId, streamRunId) || !isShowableAskUserQuestion(data)) return;
        // Stop accumulating answer_chunks so no partial answer is shown
        // above the question card.
        ignoreChunks = true;
        if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
        accumulatedContent = '';
        pendingCitationMaps = null;
        lastCitationKey = '';
        useChatStore.getState().updateSlot(slotId, {
          streamingContent: '',
          streamingCitationMaps: null,
          currentStatusMessage: null,
        });
        // The run is parked on the user, not working — no progress indicator.
        stopIdleStatus();
        const slotSnap = useChatStore.getState().slots[slotId];
        const rowId = slotSnap?.regenerateMessageId ?? pendingAssistantId;
        applyAskUserQuestionSse(slotId, data, rowId);
      },

      onAnswerFinal: () => {
        if (!slotIsOnRun(slotId, streamRunId)) return;
        stopIdleStatus();
      },

      onComplete: (data) => {
        if (!slotIsOnRun(slotId, streamRunId)) return;
        if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
        latestParts = [];
        cancelPendingStatus();
        if (!data?.conversation) {
          // Finished without the saved conversation: settle the turn, then reload it by id if known.
          const known = useChatStore.getState().slots[slotId];
          const reloadId = known?.convId;
          // Keep the streamed text on its row so a failed reload does not blank the answer.
          const streamedAnswer = !reuseExistingAssistant && accumulatedContent.trim() && known
            ? known.messages.map((m) => (m.id === pendingAssistantId
              ? { ...m, content: [{ type: 'text' as const, text: accumulatedContent }] }
              : m))
            : null;
          useChatStore.getState().updateSlot(slotId, {
            ...(streamedAnswer ? { messages: streamedAnswer } : {}),
            isStreaming: false,
            streamingAssistantId: null,
            streamingContent: '',
            currentStatusMessage: null,
            streamingCitationMaps: null,
            streamingParts: [],
            abortController: null,
            runId: null,
            stopping: false,
          });
          if (reloadId) {
            const agent = known?.threadAgentId;
            // The id is known from `connected`, so the next send must continue this chat.
            if (isNewConversation) useChatStore.getState().resolveSlotConvId(slotId, reloadId);
            // A newer turn adds rows to `messages`; this older snapshot must not replace them.
            const settledMessages = useChatStore.getState().slots[slotId]?.messages;
            void (agent ? AgentsApi.fetchAgentConversation(agent, reloadId) : ChatApi.fetchConversation(reloadId))
              .then((detail) => {
                const store = useChatStore.getState();
                if (isNewConversation && store.pendingConversations[slotId]) {
                  const createdAt = detail.conversation.createdAt ?? new Date().toISOString();
                  store.resolvePendingConversation(
                    slotId,
                    {
                      id: reloadId,
                      title: detail.conversation.title,
                      createdAt,
                      updatedAt: createdAt,
                      isShared: detail.conversation.isShared,
                      status: detail.conversation.status,
                      modelInfo: detail.conversation.modelInfo,
                      isOwner: true,
                      sharedWith: [],
                      projectId: slot.projectId ?? undefined,
                    },
                    { isAgentStream: Boolean(request.agentId) },
                  );
                }
                const current = useChatStore.getState().slots[slotId];
                if (current?.convId !== reloadId || current.messages !== settledMessages) return;
                // History returns its latest page only, so pagination restarts from it.
                useChatStore.getState().updateSlot(slotId, {
                  messages: withSearchDegraded(loadHistoricalMessages(detail.messages).messages, current.messages),
                  rawMessages: detail.messages,
                  messagePagination: {
                    currentPage: detail.pagination.page,
                    hasOlderMessages: detail.pagination.hasNextPage,
                    isLoadingOlder: false,
                  },
                });
              })
              .catch((err) => {
                console.error('[streaming] Failed to reload after finish:', err);
                if (isNewConversation) useChatStore.getState().clearPendingConversation(slotId);
              });
          } else if (isNewConversation) {
            useChatStore.getState().clearPendingConversation(slotId);
          }
          return;
        }
        const conv = data.conversation as { _id?: string; id?: string };
        const newConvId = conv._id || conv.id || '';

        // Build finalized messages from API response
        const historical = loadHistoricalMessages(data.conversation.messages);
        const { unansweredAskUserQuestion } = historical;

        // SSE placeholder assistant id → persisted Mongo message id after complete.
        const slotBeforeComplete = useChatStore.getState().slots[slotId];
        // Settling clears `stopping`, so a user-stopped run is only knowable here.
        const wasStopped = Boolean(slotBeforeComplete?.stopping);
        const finalMessages = wasStopped && !resumeAskUserQuestion
          ? keepStreamedTextOnStoppedAnswer(
              historical.messages,
              null,
              accumulatedContent,
              pendingCitationMaps ?? slotBeforeComplete?.streamingCitationMaps,
              pendingAssistantId,
              trailingStoppedReplyId(data.conversation.messages),
            )
          : historical.messages;
        const liveParts = slotBeforeComplete?.streamingParts ?? [];
        const liveFollowUp = resumeAskUserQuestion
          ? [
              slotBeforeComplete?.streamingContent,
              textFromLiveParts(liveParts),
            ].find((text) => isUsableFollowUpText(text))?.trim() ?? ''
          : '';
        // `liveParts` is seeded from the reused card row (`reuseExistingAssistant`
        // above), so it holds an earlier resume's text too. `accumulatedContent`
        // belongs to this run alone and `onAskUserQuestion` clears it, so it is
        // the only thing that can say whether THIS resume produced an answer.
        const resumeStreamedAnswer = resumeAskUserQuestion
          && isUsableFollowUpText(accumulatedContent);
        const pendingBefore = slotBeforeComplete?.pendingAskUserQuestion;
        let remappedPending: PendingAskUserQuestion | undefined;
        const lastAsst = [...finalMessages].reverse().find((m) => m.role === 'assistant');
        const cardRow = findAskUserQuestionRow(finalMessages, pendingBefore);
        if (
          resumeAskUserQuestion &&
          pendingBefore &&
          (pendingBefore.status === 'pending' || pendingBefore.status === 'submitted')
        ) {
          remappedPending = {
            ...pendingBefore,
            assistantMessageId:
              (cardRow && typeof cardRow.id === 'string'
                ? cardRow.id
                : pendingBefore.assistantMessageId),
          };
        }
        // A card turn's live parts replace the ones the server merged into the
        // card row, so the row keeps what the stream showed. Approval frames never
        // become live parts (they go to `pendingApprovals`, dropped at run end), so
        // the server's saved approvals are kept or their receipts would vanish.
        const partsTarget = reuseExistingAssistant
          ? (cardRow ?? (resumeAskUserQuestion ? lastAsst : undefined))
          : (resumeAskUserQuestion ? lastAsst : undefined);
        if (partsTarget && liveParts.length) {
          const prevCustom = (partsTarget.metadata?.custom ?? {}) as Record<string, unknown>;
          const savedParts = Array.isArray(prevCustom.persistedParts) ? (prevCustom.persistedParts as MessagePart[]) : [];
          const persistedParts = withSavedApprovals(
            withSavedFinalFacts(withFinalAnswerMarked(liveParts), savedParts),
            savedParts,
          );
          if (persistedParts.length) {
            Object.assign(partsTarget, {
              metadata: {
                ...partsTarget.metadata,
                custom: { ...prevCustom, persistedParts },
              },
            });
          }
        }
        if (
          reuseExistingAssistant &&
          cardRow &&
          liveFollowUp &&
          !isUsableFollowUpText(getThreadMessagePlainText(cardRow))
        ) {
          Object.assign(cardRow, {
            content: [{ type: 'text' as const, text: liveFollowUp }],
          });
        }
        const rowHasFollowUp = Boolean(
          cardRow && isUsableFollowUpText(getThreadMessagePlainText(cardRow)),
        );
        // The resume can itself end on another ask_user_question, merged into
        // this same card by `applyAskUserQuestionSse` — settling it as answered
        // would leave that new question unanswerable.
        const resumeAskedMore = Boolean(
          pendingBefore &&
          hasUnansweredQuestions(pendingBefore.payload, pendingBefore.answers),
        );
        if (cardRow && pendingBefore?.status === 'submitted') {
          const prevCustom = (cardRow.metadata?.custom ?? {}) as Record<string, unknown>;
          Object.assign(cardRow, {
            metadata: {
              ...cardRow.metadata,
              custom: {
                ...prevCustom,
                persistedAskUserQuestion: pendingBefore.payload,
                persistedAskUserQuestionAnswers: pendingBefore.answers,
              },
            },
          });
        }

        // Determine pagination for the "load older messages" feature.
        // We don't get pagination metadata from the SSE event, so we preserve
        // whatever was set by the initial fetchConversation (via page.tsx).
        // If no previous state exists (brand-new conversation) we leave
        // messagePagination null — a fresh load will set it correctly when
        // the user next opens the conversation or reloads the page.
        const prevPagination = useChatStore.getState().slots[slotId]?.messagePagination;
        // The SSE event does not carry pagination metadata, so we cannot derive
        // hasOlderMessages from it directly. Two sources of truth are combined:
        //   1. prevPagination.hasOlderMessages — already confirmed by the initial
        //      fetchConversation (stays true once set).
        //   2. finalMessages.length >= 20 — heuristic: if the SSE response filled
        //      a full page, the conversation likely has older messages. This handles
        //      the case where the conversation crossed the page boundary in-session
        //      (e.g. user sent the 21st message). It is safe to re-enable because
        //      loadOlderMessagesForSlot now deduplicates before prepending, so the
        //      duplicate-ID crash that originally motivated removing this heuristic
        //      cannot occur again.
        const newMsgPagination = prevPagination
          ? {
              currentPage: 1,
              hasOlderMessages: prevPagination.hasOlderMessages || finalMessages.length >= CONVERSATION_MESSAGES_PAGE_SIZE,
              isLoadingOlder: false,
            }
          : null;

        // Apply the settled run in one write so `isStreaming` clears in the
        // same tick as the final messages — wrapping this in startTransition
        // left the composer blocked on Stop for a follow-up Enter.
        useChatStore.getState().updateSlot(slotId, {
            isStreaming: false,
            streamingContent: '',
            streamingQuestion: '',
            currentStatusMessage: null,
            streamingCitationMaps: null,
            streamingParts: [],
            pendingCollections: [],
            artifacts: [],
            messages: withSearchDegraded(finalMessages, slotBeforeComplete?.messages ?? [], {
              // This turn's own reply is the row the history ends on. A turn stopped before any text has none (history drops the
              // empty reply and ends on the question), and then the note belongs to no row: an earlier answer must not get it.
              degraded: data.meta?.searchDegraded === true,
              notices: readAccessNotices(data.meta?.accessNotices),
              mark: data.meta?.searchDegraded === true || readAccessNotices(data.meta?.accessNotices).length > 0
                ? (reuseExistingAssistant
                    ? (cardRow ?? turnReply(finalMessages))
                    : turnReply(finalMessages))
                : null,
            }),
            hasLoaded: true,
            abortController: null,
            runId: null,
            stopping: false,
            durableRun: null,
            routePending: false,
            conversationModelInfo: data.conversation.modelInfo,
            rawMessages: data.conversation.messages,
            ...(newMsgPagination !== null ? { messagePagination: newMsgPagination } : {}),
            ...(isNewConversation ? { isOwner: true } : {}),
            pendingAskUserQuestion: (
              resumeAskUserQuestion && rowHasFollowUp && pendingBefore
                ? {
                    ...pendingBefore,
                    // `rowHasFollowUp` is also true for text an EARLIER resume
                    // left on the card, so it cannot settle this one. This
                    // resume answered only if it streamed text or history saw
                    // one (`hasFollowUpAfterResume`); otherwise it came back
                    // empty and has to stay retryable.
                    status: resumeAskedMore || (!resumeStreamedAnswer && unansweredAskUserQuestion)
                      ? ('pending' as const)
                      : ('submitted' as const),
                    assistantMessageId:
                      (cardRow && typeof cardRow.id === 'string' ? cardRow.id : pendingBefore.assistantMessageId),
                  }
                : (unansweredAskUserQuestion
                  ?? (pendingBefore?.status === 'submitted'
                    ? {
                        ...pendingBefore,
                        assistantMessageId:
                          (cardRow && typeof cardRow.id === 'string' ? cardRow.id : pendingBefore.assistantMessageId),
                      }
                    : (remappedPending ?? pendingBefore ?? null)))
            ),
          });

        // Resolve temp → real convId
        const currentStore = useChatStore.getState();
        if (isNewConversation && newConvId) {
          currentStore.resolveSlotConvId(slotId, newConvId);
          currentStore.resolvePendingConversation(
            slotId,
            {
              id: newConvId,
              title: data.conversation.title,
              createdAt: data.conversation.createdAt,
              updatedAt: data.conversation.updatedAt,
              isShared: data.conversation.isShared,
              lastActivityAt: data.conversation.lastActivityAt,
              status: data.conversation.status,
              modelInfo: data.conversation.modelInfo,
              isOwner: true,
              sharedWith: [],
              projectId: data.conversation.projectId ?? slot.projectId ?? undefined,
            },
            { isAgentStream: Boolean(request.agentId) }
          );
        } else {
          const existingConvId = newConvId || slot.convId;
          if (existingConvId) {
            currentStore.moveConversationToTop(existingConvId);
            const listModelInfo = data.conversation.modelInfo;
            if (listModelInfo) {
              currentStore.updateConversationModelInfoInLists(
                existingConvId,
                listModelInfo
              );
            }
          }
        }

        notifyAnswerFinishedInBackground(slotId, wasStopped);
        debugLog.flush('stream-completed', { slotId, convId: newConvId || slot.convId });
      },

      onError: (error) => {
        if (!slotIsOnRun(slotId, streamRunId)) return;
        // After Stop, `cancelStreamForSlot`'s grace timer commits the partial
        // answer as stopped; settling here would erase it first.
        if (useChatStore.getState().slots[slotId]?.stopping) {
          flushPendingForStop();
          return;
        }
        if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
        cancelPendingStatus();
        console.error('[streaming] Stream error for slot', slotId, error);
        const cardSays = cardSaysFailure(error);
        const slotNow = useChatStore.getState().slots[slotId];
        const pendingNow = slotNow?.pendingAskUserQuestion;
        const currentMessages = slotNow?.messages ?? [];
        const err = refusedTurnText(error as { code?: string }) ?? (error.message || 'An error occurred. Please try again.');
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          streamingContent: '',
          streamingQuestion: '',
          currentStatusMessage: null,
          streamingCitationMaps: null,
          streamingParts: [],
          pendingCollections: [],
          abortController: null,
          runId: null,
          stopping: false,
          durableRun: null,
          routePending: false,
          pendingAskUserQuestion: cardSays ? (pendingNow ?? null) : pendingAfterStreamFailure(pendingNow),
          pendingApprovals: [],
          // A failed answer to a card leaves the card as it was, to answer again.
          messages: resumeAskUserQuestion || reuseRowId
            ? currentMessages
            : withStreamingErrorMessage(currentMessages, err, streamErrorInfo(error) ?? { code: ANSWER_FAILED }),
        });
        if (cardSays) {
          // The card's own notice says it, and retries.
        } else if (resumeAskUserQuestion) {
          notifyAskUserQuestionResumeFailed(err);
        } else if (reuseRowId) {
          notifyToolApprovalFailed(err);
        }
        if (isNewConversation) {
          useChatStore.getState().clearPendingConversation(slotId);
        }
        debugLog.flush('stream-error', { slotId });
      },

      signal: abortController.signal,
    });
  } catch (error) {
    if (flushTimer !== null) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
    cancelPendingStatus();

    const aborted =
      (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError') ||
      (error instanceof Error &&
        (error.name === 'AbortError' || error.name === 'CanceledError'));

    if (aborted) {
      settleSend({ failed: { retry: false, code: SEND_ABORTED } });
      if (!slotIsOnRun(slotId, streamRunId)) return;
      const cur = useChatStore.getState().slots[slotId];
      if (cur?.isStreaming) {
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          streamingContent: '',
          streamingQuestion: '',
          currentStatusMessage: null,
          streamingCitationMaps: null,
          streamingParts: [],
          pendingCollections: [],
          abortController: null,
          runId: null,
          stopping: false,
          durableRun: null,
          routePending: false,
        });
      }
      if (isNewConversation) {
        useChatStore.getState().clearPendingConversation(slotId);
      }
      debugLog.flush('stream-aborted', { slotId });
      return;
    }

    if (!slotIsOnRun(slotId, streamRunId)) return;

    console.error('[streaming] Fatal error for slot', slotId, error);
    const cardSays = cardSaysFailure(error);
    const slotNow = useChatStore.getState().slots[slotId];
    const pendingNow = slotNow?.pendingAskUserQuestion;
    const currentMessages = slotNow?.messages ?? [];
    const errorMessage = error instanceof Error
      ? error.message
      : i18n.t('chatStream.errorFallback');
    useChatStore.getState().updateSlot(slotId, {
      isStreaming: false,
      streamingContent: '',
      streamingQuestion: '',
      currentStatusMessage: null,
      streamingCitationMaps: null,
      streamingParts: [],
      pendingCollections: [],
      abortController: null,
      runId: null,
      stopping: false,
      durableRun: null,
      routePending: false,
      pendingAskUserQuestion: cardSays ? (pendingNow ?? null) : pendingAfterStreamFailure(pendingNow),
      pendingApprovals: [],
      messages: resumeAskUserQuestion || reuseRowId
        ? currentMessages
        : withStreamingErrorMessage(currentMessages, errorMessage, { code: ANSWER_FAILED }),
    });
    if (cardSays) {
      // The card's own notice says it, and retries.
    } else if (resumeAskUserQuestion) {
      notifyAskUserQuestionResumeFailed(errorMessage);
    } else if (reuseRowId) {
      notifyToolApprovalFailed(errorMessage);
    }
    if (isNewConversation) {
      useChatStore.getState().clearPendingConversation(slotId);
    }
    debugLog.flush('stream-fatal-error', { slotId });
  }
}

/**
 * Regenerate a bot response for a specific slot.
 *
 * Similar to `streamMessageForSlot` but uses the regenerate endpoint
 * and replaces the last assistant message rather than appending.
 *
 * @param slotId    — stable slot key
 * @param messageId — backend _id of the bot_response to regenerate
 */
export async function streamRegenerateForSlot(
  slotId: string,
  messageId: string,
  modelOverride?: ModelOverride,
  originalFilters?: ChatKnowledgeFilters,
  /**
   * Set when the user edited the question: its text, and its files when they changed, replace
   * the stored ones and the answer is rebuilt for them.
   */
  edit?: { query: string; attachments?: AttachmentRef[] },
  /** Changed answers to the question card the reply followed: the turn is re-run with them. */
  answers?: string,
): Promise<void> {
  const store = useChatStore.getState();
  const slot = store.slots[slotId];
  if (!slot || !slot.convId) return;
  if (slot.remoteRunInProgress) return;

  // Rebuilding the primary answer leaves nothing to compare against.
  discardCompare(slotId);
  const editConvId = slot.convId;
  const preEditMessages = slot.messages;
  const preEditPending = slot.pendingAskUserQuestion;
  // The server asks a card turn again from its first question (edited or not), so its old card
  // and answers go: the new run asks afresh, and a kept card would swallow the new questions.
  // Changed answers keep the card: the reply is re-run for them, and the card shows them meanwhile.
  const turnIsAskedAgain =
    !answers && (Boolean(edit) || rowHasQuestionCard(slot.messages, messageId, slot.pendingAskUserQuestion));
  const editedMessages = turnIsAskedAgain
    ? withoutQuestionCard(
        edit ? withEditedQuestion(slot.messages, messageId, edit.query, edit.attachments) : slot.messages,
        messageId,
      )
    : slot.messages;
  /** A run that fails keeps the stored question and its card, so the screen must too. */
  const undoEditedQuestion = (): Partial<{
    messages: ThreadMessageLike[];
    pendingAskUserQuestion: PendingAskUserQuestion | null;
  }> =>
    turnIsAskedAgain
      ? { messages: preEditMessages, pendingAskUserQuestion: preEditPending }
      : { pendingAskUserQuestion: preEditPending };

  slot.abortController?.abort();

  // Derive the effective agent id early — the slot may not have it yet when
  // the URL carries the agentId (first message in a fresh tab).  All context-
  // scoped lookups (model, reasoning effort) must use the same resolved id.
  const rawAgentIdFromUrl =
    typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('agentId') : null;
  const agentIdFromUrl = rawAgentIdFromUrl?.trim() ? rawAgentIdFromUrl : null;
  const slotAgentId = slot.threadAgentId?.trim() || null;
  const threadAgentId = slotAgentId ?? agentIdFromUrl;

  const regenCtxKey = ctxKeyFromAgent(threadAgentId ?? null);
  let resolvedModel: ModelOverride | null =
    modelOverride ?? getEffectiveModel(regenCtxKey);
  if (!resolvedModel) {
    try {
      await fetchModelsForContext(regenCtxKey);
      resolvedModel = getEffectiveModel(regenCtxKey);
    } catch (error) {
      console.warn('[streaming] Failed to fetch models for context, proceeding with defaults:', error);
    }
  }
  if (!resolvedModel) {
    showNoModelToast();
    resolvedModel = { modelKey: '', modelName: '', modelFriendlyName: '' };
  }

  const abortController = new AbortController();
  const runId = generateRunId();
  const streamRunId = runId ?? null;
  store.updateSlot(slotId, {
    // An answered card is not this run's: left in the slot it would be opened again when the run ends,
    // and the next click on it would add an answer message after the reply.
    ...(slot.pendingAskUserQuestion && slot.pendingAskUserQuestion.status !== 'pending'
      ? { pendingAskUserQuestion: null }
      : {}),
    isStreaming: true,
    regenerateMessageId: messageId,
    streamingAssistantId: messageId,
    streamingContent: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    streamingParts: [],
    abortController,
    runId: streamRunId,
    stopping: false,
    durableRun: null,
    routePending: true,
    lastDurableRun: null,
    // The old card belongs to the old question; keeping it would stamp it on the new answer.
    ...(turnIsAskedAgain ? { messages: editedMessages, pendingAskUserQuestion: null } : {}),
  });

  debugLog.flush('regenerate-started', { slotId, messageId });

  // ── Time-throttled content + citation accumulator (same as streamMessageForSlot) ──
  const ACTIVE_FLUSH_MS = 16;
  const BACKGROUND_FLUSH_MS = 200;
  let accumulatedContent = '';
  let pendingCitationMaps: ReturnType<typeof buildCitationMapsFromStreaming> | null = null;
  let lastCitationKey = '';
  let lastFlushTime = 0;
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  let ignoreChunks = false;
  let latestParts: MessagePart[] = [];

  // Minimum-dwell scheduler for SSE status messages (see
  // createStatusDwellScheduler for the rationale).
  const { applyStatus, scheduleStatus, cancelPendingStatus, armIdleStatus, stopIdleStatus } =
    createStatusDwellScheduler(slotId, () => slotIsOnRun(slotId, streamRunId));

  function flushContentToStore() {
    if (!slotIsOnRun(slotId, streamRunId)) return;
    debugLog.rafFlush();
    const citationMaps = pendingCitationMaps;
    if (citationMaps) {
      pendingCitationMaps = null;
    }
    useChatStore.getState().updateSlot(slotId, {
      streamingContent: accumulatedContent,
      streamingParts: latestParts,
      ...(citationMaps ? { streamingCitationMaps: citationMaps } : {}),
    });
  }

  /** Land the last throttled chunk so the grace timer commits all of it. */
  function flushPendingForStop() {
    if (flushTimer !== null) {
      clearTimeout(flushTimer);
      flushTimer = null;
      flushContentToStore();
    }
    cancelPendingStatus();
  }

  function scheduleFlush() {
    const now = Date.now();
    const isActive = useChatStore.getState().activeSlotId === slotId;
    const interval = isActive ? ACTIVE_FLUSH_MS : BACKGROUND_FLUSH_MS;
    if (now - lastFlushTime >= interval) {
      if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
      lastFlushTime = now;
      flushContentToStore();
    } else if (flushTimer === null) {
      flushTimer = setTimeout(() => {
        flushTimer = null;
        lastFlushTime = Date.now();
        flushContentToStore();
      }, interval - (now - lastFlushTime));
    }
  }

  /** Which API we use for reload — frozen at regen start (URL may change before `complete`) */
  const reloadViaAgentId = threadAgentId;

  const regenerateCallbacks: StreamMessageCallbacks = {
    ...turnRouteCallbacks(slotId, streamRunId),
    onConnected: (data) => {
      if (!slotIsOnRun(slotId, streamRunId)) return;
      scheduleStatus(statusMessageFromConnectedEvent(data));
    },

    onRestreaming: () => {
      if (!slotIsOnRun(slotId, streamRunId)) return;
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      cancelPendingStatus();
      accumulatedContent = '';
      lastCitationKey = '';
      pendingCitationMaps = null;
      latestParts = [];
      useChatStore.getState().updateSlot(slotId, {
        streamingContent: '',
        streamingCitationMaps: null,
        streamingParts: [],
      });
      applyStatus(statusMessageRestreaming());
    },

    onStatus: (data) => {
      if (!slotIsOnRun(slotId, streamRunId)) return;
      const statusMessage: StatusMessage = {
        id: `status-${Date.now()}`,
        status: data.status,
        message: data.message,
        timestamp: new Date().toISOString(),
      };
      if (data.status === 'calling_llm') {
        // A tool's last progress still waiting out its dwell must not come back after this.
        cancelPendingStatus();
        applyStatus(statusMessage);
      } else {
        scheduleStatus(statusMessage);
      }
    },

    onParts: (parts) => {
      if (!slotIsOnRun(slotId, streamRunId)) return;
      latestParts = parts;
      scheduleFlush();
    },

    onChunk: (data) => {
      if (!slotIsOnRun(slotId, streamRunId)) return;
      if (ignoreChunks) return;
      if (!data.accumulated && useChatStore.getState().slots[slotId]?.stopping) return;
      debugLog.chunk();
      accumulatedContent = data.accumulated;
      // See streamMessageForSlot's onChunk — clear on every chunk, not just
      // the first, so a status from a later tool call doesn't outlive it.
      if (data.accumulated.length > 0) {
        cancelPendingStatus();
        useChatStore.getState().updateSlot(slotId, { currentStatusMessage: null });
        armIdleStatus();
      }
      if (data.citations && data.citations.length > 0) {
        const key = JSON.stringify(data.citations);
        if (key !== lastCitationKey) {
          lastCitationKey = key;
          pendingCitationMaps = buildCitationMapsFromStreaming(data.citations);
        }
      }
      scheduleFlush();
    },

    ...approvalStreamCallbacks(slotId),

    // WP-100 (C10.05): the run's spend as its stream pushes it.

    onRunCost: pushRunCost,
    ...planStreamCallbacks(slotId, () => slotIsOnRun(slotId, streamRunId)),
    onAskUserQuestion: (data: SSEAskUserQuestionEvent) => {
      if (!slotIsOnRun(slotId, streamRunId) || !isShowableAskUserQuestion(data)) return;
      ignoreChunks = true;
      if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
      accumulatedContent = '';
      pendingCitationMaps = null;
      lastCitationKey = '';
      useChatStore.getState().updateSlot(slotId, {
        streamingContent: '',
        streamingCitationMaps: null,
        currentStatusMessage: null,
      });
      // The run is parked on the user, not working — no progress indicator.
      stopIdleStatus();
      const liveMessages = useChatStore.getState().slots[slotId]?.messages ?? [];
      applyAskUserQuestionSse(
        slotId,
        data,
        assistantRowIdForBackendMessage(liveMessages, messageId),
      );
    },

    onAnswerFinal: () => {
      if (!slotIsOnRun(slotId, streamRunId)) return;
      stopIdleStatus();
    },

    onComplete: async (data) => {
      if (!slotIsOnRun(slotId, streamRunId)) return;
      const wasStopped = Boolean(useChatStore.getState().slots[slotId]?.stopping);
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      cancelPendingStatus();
      latestParts = [];
      try {
        const detail = reloadViaAgentId
          ? await AgentsApi.fetchAgentConversation(reloadViaAgentId, slot.convId!)
          : await ChatApi.fetchConversation(slot.convId!);
        if (!slotIsOnRun(slotId, streamRunId)) return;
        const pendingBefore = useChatStore.getState().slots[slotId]?.pendingAskUserQuestion;
        const historical = loadHistoricalMessages(detail.messages);
        const { unansweredAskUserQuestion } = historical;
        const exactRegenRowId = exactAssistantRowIdForBackendMessage(historical.messages, messageId);
        const stoppedMaps = pendingCitationMaps ?? useChatStore.getState().slots[slotId]?.streamingCitationMaps;
        // History drops a regenerated reply that was stopped before any text was saved. The
        // nearest-row fallback would then point at an earlier answer, so the partial gets a row
        // of its own under the regenerated message's id instead.
        const stoppedRegenRowMissing = wasStopped && !exactRegenRowId;
        const regenRowId = exactRegenRowId
          ?? (stoppedRegenRowMissing
            ? messageId
            : assistantRowIdForBackendMessage(historical.messages, messageId));
        let loadedMessages = historical.messages;
        if (wasStopped && exactRegenRowId) {
          loadedMessages = keepStreamedTextOnStoppedAnswer(
            historical.messages, exactRegenRowId, accumulatedContent, stoppedMaps,
          );
        } else if (stoppedRegenRowMissing && historical.messages[historical.messages.length - 1]?.role === 'user') {
          loadedMessages = keepStreamedTextOnStoppedAnswer(
            historical.messages,
            null,
            accumulatedContent,
            stoppedMaps,
            messageId,
            trailingStoppedReplyId(detail.messages) ?? messageId,
          );
        }
        const finalMessages = pendingBefore?.payload
          ? stampPendingCardOnMessages(loadedMessages, {
              ...pendingBefore,
              assistantMessageId: regenRowId,
            })
          : loadedMessages;
        const postRegenModelInfo = pickModelInfoFromConversationBundle({
          modelInfo: detail.conversation.modelInfo,
          messages: detail.messages,
        });
        const regenPagination = detail.pagination
          ? {
              currentPage: detail.pagination.page,
              hasOlderMessages: detail.pagination.hasNextPage,
              isLoadingOlder: false,
            }
          : undefined;

        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          regenerateMessageId: null,
          durableRun: null,
          routePending: false,
          streamingAssistantId: null,
          streamingContent: '',
          currentStatusMessage: null,
          streamingCitationMaps: null,
          streamingParts: [],
          messages: withSearchDegraded(
            finalMessages,
            useChatStore.getState().slots[slotId]?.messages ?? [],
            {
              fresh: regenRowId,
              degraded: data?.meta?.searchDegraded === true,
              notices: readAccessNotices(data?.meta?.accessNotices),
              mark: data?.meta?.searchDegraded === true || readAccessNotices(data?.meta?.accessNotices).length > 0
                ? (finalMessages.find((m) => m.id === regenRowId) ?? null)
                : null,
            },
          ),
          rawMessages: detail.messages,
          abortController: null,
          runId: null,
          stopping: false,
          pendingAskUserQuestion: unansweredAskUserQuestion
            ?? (pendingBefore
              ? {
                  ...pendingBefore,
                  status: 'pending' as const,
                  assistantMessageId: regenRowId,
                }
              : pendingFromUnansweredPersistedCard(finalMessages, regenRowId)),
          ...(regenPagination ? { messagePagination: regenPagination } : {}),
          ...(postRegenModelInfo ? { conversationModelInfo: postRegenModelInfo } : {}),
        });
        // Editing the first question moves the chat's title; keep the sidebar in step.
        const reloadedTitle = detail.conversation.title;
        if (
          reloadedTitle &&
          useChatStore.getState().conversations.some((c) => c.id === slot.convId && c.title !== reloadedTitle)
        ) {
          useChatStore.getState().renameConversation(slot.convId!, reloadedTitle);
        }
        notifyAnswerFinishedInBackground(slotId, wasStopped);
        debugLog.flush('regenerate-completed', { slotId, messageId });
      } catch (err) {
        if (!slotIsOnRun(slotId, streamRunId)) return;
        console.error('[streaming] Failed to reload after regenerate:', err);
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          regenerateMessageId: null,
          durableRun: null,
          routePending: false,
          streamingAssistantId: null,
          streamingContent: '',
          currentStatusMessage: null,
          streamingCitationMaps: null,
          streamingParts: [],
          abortController: null,
          runId: null,
          stopping: false,
        });
        debugLog.flush('regenerate-reload-error', { slotId });
      }
    },

    onError: (error: Error) => {
      if (!slotIsOnRun(slotId, streamRunId)) return;
      // See streamMessageForSlot's onError: the grace timer owns a stopped run.
      if (useChatStore.getState().slots[slotId]?.stopping) {
        flushPendingForStop();
        return;
      }
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      cancelPendingStatus();
      console.error('[streaming] Regenerate error for slot', slotId, error);
      const dropped = turnIsAskedAgain && !editWasNotTaken(error);
      useChatStore.getState().updateSlot(slotId, {
        ...(dropped ? {} : undoEditedQuestion()),
        isStreaming: false,
        regenerateMessageId: null,
        durableRun: null,
        routePending: false,
        streamingAssistantId: null,
        streamingContent: '',
        currentStatusMessage: null,
        streamingCitationMaps: null,
        streamingParts: [],
        abortController: null,
        runId: null,
        stopping: false,
      });
      if (dropped) followRemoteRun(slotId, editConvId);
      debugLog.flush('regenerate-error', { slotId });
    },

    signal: abortController.signal,
  };

  try {
    if (threadAgentId && slotAgentId !== threadAgentId) {
      useChatStore.getState().updateSlot(slotId, { threadAgentId });
    }
    /** Strip `instanceId:` prefix added for UI multi-instance isolation. */
    const stripInstancePrefix = (key: string) => {
      const colon = key.indexOf(':');
      return colon >= 0 ? key.slice(colon + 1) : key;
    };

    if (threadAgentId) {
      const { chatMode } = buildStreamRequestModeFields(store.settings, true);
      const agentApiChatMode = streamChatModeToAgentApiChatMode(chatMode);
      // Read agent tools from the store at regen time so the correct tool set
      // is used even when the user changed the selection between turns.
      const agentToolsSel = useChatStore.getState().agentStreamTools;
      // `null` → everything selected: omit `tools` entirely (`undefined`)
      // rather than exploding the full catalog — an exploded list both
      // defeats the backend's "no filter = every configured toolset"
      // handling (agent.py) and needlessly re-approaches the request-size
      // cap on agents with many multi-action toolsets.
      const regenTools = agentToolsSel === null
        ? undefined
        : [...new Set(agentToolsSel.map(stripInstancePrefix))];
      const scopedCaps = useChatStore.getState().scopedAgentCapabilities[threadAgentId]
        ?? { internalSearch: true, webSearch: true };
      const agentRegenReasoningEffort = getRequestReasoningEffort(regenCtxKey, resolvedModel);
      await ChatApi.streamAgentRegenerate(
        threadAgentId,
        slot.convId,
        messageId,
        regenerateCallbacks,
        {
          modelKey: resolvedModel.modelKey.trim(),
          modelName: resolvedModel.modelName || resolvedModel.modelKey,
          modelFriendlyName: resolvedModel.modelFriendlyName || resolvedModel.modelName || resolvedModel.modelKey,
          chatMode: agentApiChatMode,
          tools: regenTools,
          filters: originalFilters ?? buildAssistantApiFilters(store.settings.filters),
          agentCapabilities: scopedCaps,
          ...(agentRegenReasoningEffort ? { reasoningEffort: agentRegenReasoningEffort } : {}),
          ...chatHarnessField(turnHarnessChoice(useChatStore.getState().slots[slotId], store.settings.queryMode, true)),
          runId,
          ...(edit ? { query: edit.query } : {}),
          ...(edit?.attachments !== undefined ? { attachments: edit.attachments } : {}),
          ...(answers ? { answers } : {}),
        }
      );
    } else {
      const { chatMode } = buildStreamRequestModeFields(store.settings, false);
      // Universal agent mode: read current tool selection at regen time
      const isUniversalAgent = store.settings.queryMode === 'agent';
      const universalToolsSel = useChatStore.getState().universalAgentStreamTools;
      // null → "all tools": left out, as a normal send does. A list without a web_search entry turns
      // web search off, and the catalog has none. Array → explicit subset.
      // Strip instanceId prefix from internal keys before putting on the wire.
      const regenStreamTools =
        isUniversalAgent && universalToolsSel !== null
          ? [...new Set([...universalToolsSel].map(stripInstancePrefix))]
          : undefined;
      const regenProjectId = slot.projectId ?? useChatStore.getState().projectScope?.projectId ?? null;
      const regenCaps = regenProjectId
        ? projectAgentCapabilities(useChatStore.getState().scopedAgentCapabilities, regenProjectId)
        : store.settings.agentCapabilities;
      const assistantRegenReasoningEffort = getRequestReasoningEffort(regenCtxKey, resolvedModel);
      await ChatApi.streamRegenerate(slot.convId, messageId, regenerateCallbacks, {
        modelKey: resolvedModel.modelKey,
        modelName: resolvedModel.modelName,
        modelFriendlyName: resolvedModel.modelFriendlyName,
        chatMode,
        filters: originalFilters ?? buildAssistantApiFilters(store.settings.filters),
        ...(regenStreamTools !== undefined ? { agentStreamTools: regenStreamTools } : {}),
        ...(isUniversalAgent ? { agentCapabilities: regenCaps } : {}),
        ...(assistantRegenReasoningEffort ? { reasoningEffort: assistantRegenReasoningEffort } : {}),
        // A regenerate is never a new conversation: the field says which executor it keeps.
        ...chatHarnessField(turnHarnessChoice(useChatStore.getState().slots[slotId], store.settings.queryMode, false)),
        runId,
        ...(edit ? { query: edit.query } : {}),
        ...(edit?.attachments !== undefined ? { attachments: edit.attachments } : {}),
        ...(answers ? { answers } : {}),
      });
    }
  } catch (error) {
    if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
    cancelPendingStatus();
    const aborted =
      (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError') ||
      (error instanceof Error && (error.name === 'AbortError' || error.name === 'CanceledError'));
    if (!slotIsOnRun(slotId, streamRunId)) return;
    if (!aborted) {
      console.error('[streaming] Fatal regenerate error for slot', slotId, error);
    }
    const dropped = turnIsAskedAgain && !aborted && !editWasNotTaken(error);
    useChatStore.getState().updateSlot(slotId, {
      ...(dropped ? {} : undoEditedQuestion()),
      isStreaming: false,
      regenerateMessageId: null,
      durableRun: null,
      routePending: false,
      streamingAssistantId: null,
      streamingContent: '',
      currentStatusMessage: null,
      streamingCitationMaps: null,
      streamingParts: [],
      abortController: null,
      runId: null,
      stopping: false,
    });
    if (dropped) followRemoteRun(slotId, editConvId);
    debugLog.flush(aborted ? 'regenerate-aborted' : 'regenerate-fatal-error', { slotId });
  }
}

/**
 * Stop the active stream for a slot.
 *
 * Cooperative-first: posts `POST .../cancel` (same `runId` the stream
 * request carried) and marks the slot `stopping` WITHOUT aborting the SSE
 * connection — so the backend has a chance to actually stop token
 * generation, persist the partial answer as `status: 'stopped'`, and send a
 * normal `RUN_FINISHED` that this same stream's `onComplete` renders (see
 * `streamMessageForSlot`/`streamRegenerateForSlot`).
 *
 * The cancel POST is retried until acknowledged (`postCancelUntilAcknowledged`)
 * because closing the connection no longer stops a run. A Stop before
 * `conversation_created` on a brand-new chat waits for the conversation id
 * rather than aborting.
 *
 * A `STOP_GRACE_MS` timer keeps the UI honest: if the run hasn't settled by
 * then (backend unreachable, stuck request), it drops the connection and
 * commits whatever text streamed so far as a locally-synthesized stopped
 * message — never leaves the composer stuck showing "Stopping…" forever.
 * The cancel retries carry on in the background after that, and if none has
 * been acknowledged yet the thread is flagged `remoteRunInProgress` so sends
 * wait for the server to report the run settled.
 *
 * No-ops if the slot isn't streaming or is already `stopping` (second Stop
 * click). Only a run with no `runId` (no Web Crypto) has nothing to target,
 * so it just drops the connection locally.
 *
 * Exported as `cancelStreamForSlot` — the name `runtime.ts`'s `onCancel`
 * and the chat-input Stop button both already call.
 */
export function cancelStreamForSlot(slotId: string): void {
  const store = useChatStore.getState();
  const slot = store.slots[slotId];
  if (!slot || !slot.isStreaming || slot.stopping) return;

  const durableRun = slot.durableRun;
  if (durableRun) {
    // Same cooperative shape as below: the stream stays open so the run's own
    // RUN_FINISHED (the stopped partial answer the server saved) settles the
    // slot. One request; the server needs no revision.
    store.updateSlot(slotId, { stopping: true });
    debugLog.flush('stream-stop-requested', { slotId, runId: durableRun.runId });
    cancelDurableRun(durableRun);
    commitStoppedAfterGrace(
      slotId,
      (cur) => cur.durableRun?.runId === durableRun.runId,
      DURABLE_STOP_GRACE_MS,
    );
    return;
  }

  // With the harness off for this user every turn is today's loop, so there is no route to wait for.
  if (slot.routePending && selectAgentHarnessEnabled(useFeatureFlagsStore.getState())) {
    // Aborting now would leave a durable run going unseen (LAT1): the turn's
    // first frame says which run to stop, and its callbacks stop it.
    const pendingRunId = slot.runId;
    store.updateSlot(slotId, { stopping: true });
    debugLog.flush('stream-stop-requested', { slotId, runId: pendingRunId, route: 'pending' });
    commitStoppedAfterGrace(slotId, (cur) => cur.runId === pendingRunId, DURABLE_STOP_GRACE_MS);
    return;
  }

  const { convId, runId, threadAgentId, isTemp } = slot;

  if (!runId) {
    slot.abortController?.abort();
    store.updateSlot(slotId, {
      isStreaming: false,
      streamingContent: '',
      streamingQuestion: '',
      currentStatusMessage: null,
      streamingCitationMaps: null,
      streamingParts: [],
      abortController: null,
      runId: null,
      stopping: false,
      regenerateMessageId: null,
      streamingAssistantId: null,
    });
    if (isTemp) {
      store.clearPendingConversation(slotId);
    }
    debugLog.flush('stream-cancelled-no-run-id', { slotId });
    return;
  }

  store.updateSlot(slotId, { stopping: true, routePending: false });
  debugLog.flush('stream-stop-requested', { slotId, runId });
  const cancel = postCancelUntilAcknowledged(slotId, runId, threadAgentId ?? null, convId ?? null);
  commitLegacyStopAfterGrace(slotId, runId, convId ?? null, cancel);
}

/**
 * The turn's first frame says which loop took it (LAT1, LAT2): a durable run
 * the slot follows and stops by its handle, or today's loop. A Stop pressed
 * before that frame is sent to whichever it is.
 */
function turnRouteCallbacks(
  slotId: string,
  streamRunId: string | null,
  /** The engine the turn's request named for the chat it creates (CMDUI2): read back once the route is known. */
  newChatEngine: string | null = null,
): Pick<StreamMessageCallbacks, 'onDurableRunAccepted' | 'onLegacyTurn'> {
  return {
    onDurableRunAccepted: (durableRun) => {
      if (newChatEngine !== null) {
        void confirmNewChatEngine(
          slotId,
          { ...(durableRun.agentId ? { agentId: durableRun.agentId } : {}), sessionId: durableRun.sessionId },
          newChatEngine,
        );
      }
      if (!slotIsOnRun(slotId, streamRunId)) return;
      const stopped = useChatStore.getState().slots[slotId]?.stopping === true;
      useChatStore.getState().updateSlot(slotId, { durableRun, lastDurableRun: durableRun, routePending: false });
      if (stopped) cancelDurableRun(durableRun);
    },
    onLegacyTurn: () => {
      if (newChatEngine !== null) void confirmNewChatEngine(slotId, null, newChatEngine);
      if (!slotIsOnRun(slotId, streamRunId)) return;
      const current = useChatStore.getState().slots[slotId];
      useChatStore.getState().updateSlot(slotId, { routePending: false });
      // A Stop that waited for the route (`cancelStreamForSlot`) is today's loop's Stop from here on.
      if (current?.routePending && current.stopping && streamRunId) {
        const convId = current.convId ?? null;
        const cancel = postCancelUntilAcknowledged(slotId, streamRunId, current.threadAgentId ?? null, convId);
        commitLegacyStopAfterGrace(slotId, streamRunId, convId, cancel);
      }
    },
  };
}

/** One cancel POST; the run's own stream then ends the turn with what the server saved. */
function cancelDurableRun(durableRun: DurableAgentRunHandle): void {
  ChatApi.cancelCurrentDurableRun(durableRun.agentId, durableRun.sessionId, durableRun.runId).catch((error) => {
    console.warn('[streaming] Failed to cancel durable run', durableRun.runId, error);
  });
}

/** The post-stop slot state both grace timers write: the stream is over, what streamed is kept. */
function stoppedSlotUpdate(cur: ChatSlot): Partial<ChatSlot> {
  return {
    isStreaming: false,
    streamingContent: '',
    streamingQuestion: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    streamingParts: [],
    abortController: null,
    runId: null,
    stopping: false,
    durableRun: null,
    routePending: false,
    regenerateMessageId: null,
    streamingAssistantId: null,
    messages: buildStoppedMessages(
      cur.messages,
      cur.regenerateMessageId,
      cur.streamingContent,
      cur.pendingAskUserQuestion,
      cur.streamingAssistantId,
    ),
  };
}

/**
 * Stop's guaranteed fallback for a durable run (or a turn whose route is not known yet): if the
 * stopped run has not settled the slot within the grace time, hard-abort the stream and commit
 * what streamed as a locally-synthesized stopped message.
 */
function commitStoppedAfterGrace(
  slotId: string,
  isSameRun: (slot: ChatSlot) => boolean,
  graceMs: number = STOP_GRACE_MS,
): void {
  setTimeout(() => {
    const cur = useChatStore.getState().slots[slotId];
    // Already settled (normal RUN_FINISHED, a later error, or a brand-new
    // stream started on this slot in the meantime) — nothing to do.
    if (!cur || !cur.isStreaming || !isSameRun(cur)) return;

    cur.abortController?.abort();
    useChatStore.getState().updateSlot(slotId, stoppedSlotUpdate(cur));
    if (cur.isTemp) {
      useChatStore.getState().clearPendingConversation(slotId);
    }
    debugLog.flush('stream-stop-grace-timeout', { slotId });
  }, graceMs);
}

/**
 * Today's loop's Stop fallback: as `commitStoppedAfterGrace`, but closing the stream no longer
 * stops the run, so an unacknowledged cancel leaves the thread waiting on the server.
 */
function commitLegacyStopAfterGrace(
  slotId: string,
  runId: string,
  convId: string | null,
  cancel: CancelHandle,
): void {
  setTimeout(() => {
    const cur = useChatStore.getState().slots[slotId];
    if (!cur || !cur.isStreaming || cur.runId !== runId) return;

    // The open connection is the only way a brand-new chat learns its
    // conversation id, so without one it stays open until the cancel is done.
    const knownConvId = convId ?? pendingStopConvIds.get(runId) ?? null;
    if (knownConvId) {
      cur.abortController?.abort();
    } else {
      void cancel.done.then(() => {
        cur.abortController?.abort();
        // The id only arrived after this timer: the run may still be going and the slot saw none of it.
        const lateConvId = cancel.convId();
        if (lateConvId && !cancel.isAcknowledged()) {
          followRemoteRun(slotId, lateConvId);
          return;
        }
        const parked = useChatStore.getState().slots[slotId];
        if (parked && !parked.isStreaming && !parked.convId && parked.remoteRunInProgress) {
          useChatStore.getState().updateSlot(slotId, { remoteRunInProgress: false });
        }
      });
    }
    useChatStore.getState().updateSlot(slotId, {
      ...stoppedSlotUpdate(cur),
      // A cancel the backend hasn't acknowledged may not have stopped the run:
      // treat the thread like a leftover run until the server says it settled.
      // Without an id there is nothing to poll yet, so sends wait for the cancel itself.
      remoteRunInProgress: knownConvId ? !cancel.isAcknowledged() : true,
      // A first-turn chat is still temp (`onConnected` keeps it so); the poll's reload skips temp slots.
      ...(knownConvId ? { convId: knownConvId, isTemp: false } : {}),
    });
    if (cur.isTemp) {
      useChatStore.getState().clearPendingConversation(slotId);
    }
    debugLog.flush('stream-stop-grace-timeout', { slotId });
  }, STOP_GRACE_MS);
}

/**
 * Reattach the UI to the durable run the conversation GET reported as
 * `activeRunId`. The run already owns its user message, so this creates only
 * a temporary assistant projection and never submits a second turn.
 */
/**
 * The approvals the frames already seen were waiting on, from the run's timeline; nothing when it cannot be read.
 * An engine run's steps need no restoring: they are in the saved stream checkpoint's parts (CTL-4).
 */
async function restoreFromTimeline(slotId: string, run: DurableAgentRunHandle): Promise<void> {
  const timeline = await ChatApi.getRunTimeline(run).catch(() => null);
  if (!timeline) return;
  seedPendingApprovals(slotId, [...(timeline.approvals ?? []), ...pendingConnects(timeline.connects)]);
}

export async function resumeActiveDurableRunForSlot(
  slotId: string,
  agentId: string | undefined,
  sessionId: string,
  activeRunId: string | null | undefined,
): Promise<void> {
  if (!activeRunId) return;
  if (!selectAgentHarnessResumeEnabled(useFeatureFlagsStore.getState())) return;
  await followDurableRunInSlot(slotId, agentId, sessionId, activeRunId);
}

/**
 * Shows a durable run of the slot's conversation as its current turn and follows its stream: a run reattached after
 * a refresh, or one the chat admitted itself (`/run`, SLASH3), whose `question` row is added first.
 */
export async function followDurableRunInSlot(
  slotId: string,
  agentId: string | undefined,
  sessionId: string,
  activeRunId: string,
  question?: string,
): Promise<void> {
  const fresh = useChatStore.getState().slots[slotId];
  if (!fresh || fresh.isStreaming || fresh.durableRun) return;
  const abortController = new AbortController();
  const pendingAssistantId = createPendingAssistantId();
  const messages = [...fresh.messages];
  if (question !== undefined) {
    messages.push({
      role: 'user',
      content: [{ type: 'text', text: question }],
      metadata: { custom: { createdAt: new Date().toISOString() } },
    });
  }
  const last = messages[messages.length - 1];
  if (!(last?.role === 'assistant' && getThreadMessagePlainText(last).trim() === '')) {
    messages.push({
      role: 'assistant',
      id: pendingAssistantId,
      content: [{ type: 'text', text: '' }],
    });
  }
  // The message list gives live props (the approval card among them) only to the row whose question is
  // `streamingQuestion`; without it a reattached run waiting on an approval showed no card (QA-5).
  const lastUser = [...messages].reverse().find((row) => row.role === 'user');
  const resumed = { ...(agentId ? { agentId } : {}), sessionId, runId: activeRunId };
  useChatStore.getState().updateSlot(slotId, {
    isStreaming: true,
    messages,
    streamingQuestion: lastUser ? getThreadMessagePlainText(lastUser) : '',
    streamingContent: '',
    streamingParts: [],
    streamingCitationMaps: null,
    currentStatusMessage: null,
    abortController,
    durableRun: resumed,
    lastDurableRun: resumed,
  });

  if (hasSavedDurableStream(activeRunId)) {
    await restoreFromTimeline(slotId, resumed);
  }
  let accumulated = '';
  try {
    await ChatApi.resumeDurableAgentRun(agentId, sessionId, activeRunId, {
      onChunk: (data) => {
        accumulated = data.accumulated;
        useChatStore.getState().updateSlot(slotId, {
          streamingContent: accumulated,
          ...(data.citations?.length
            ? {
                streamingCitationMaps: buildCitationMapsFromStreaming(data.citations),
              }
            : {}),
        });
      },
      onParts: (parts) => {
        useChatStore.getState().updateSlot(slotId, { streamingParts: parts });
      },
      onStatus: (data) => {
        useChatStore.getState().updateSlot(slotId, {
          currentStatusMessage: {
            id: `status-${Date.now()}`,
            status: data.status,
            message: data.message,
            timestamp: new Date().toISOString(),
          },
        });
      },
      onArtifact: (data) => {
        if (data.visibility === 'STAGING') return;
        const artifact = buildChatArtifact({
          id: data.artifactId,
          fileName: data.fileName,
          mimeType: data.mimeType,
          sizeBytes: data.sizeBytes,
          downloadUrl: data.downloadUrl,
          artifactType: data.artifactType,
          recordId: data.recordId,
          version: data.version,
          derivedFromCodeArtifactId: data.derivedFromCodeArtifactId,
          visibility: data.visibility,
          unavailable: data.unavailable,
        });
        const slot = useChatStore.getState().slots[slotId];
        if (!slot) return;
        useChatStore.getState().updateSlot(slotId, {
          artifacts: withArtifactCard(slot.artifacts, artifact, data.lifecycle),
        });
      },
      ...approvalStreamCallbacks(slotId),
      // WP-100 (C10.05): the run's spend as its stream pushes it.
      onRunCost: pushRunCost,
      ...planStreamCallbacks(slotId),
      onAskUserQuestion: (data) => {
        applyAskUserQuestionSse(slotId, data, pendingAssistantId);
      },
      onComplete: (data) => {
        const { messages: finalMessages, unansweredAskUserQuestion } = loadHistoricalMessages(
          data.conversation.messages,
        );
        const wasStopped = Boolean(useChatStore.getState().slots[slotId]?.stopping);
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          stopping: false,
          messages: finalMessages,
          streamingContent: '',
          streamingParts: [],
          streamingCitationMaps: null,
          currentStatusMessage: null,
          abortController: null,
          durableRun: null,
          routePending: false,
          artifacts: [],
          hasLoaded: true,
          conversationModelInfo: data.conversation.modelInfo,
          ...(unansweredAskUserQuestion
            ? { pendingAskUserQuestion: unansweredAskUserQuestion }
            : {}),
        });
        // A durable run reattached after a refresh finishes like a fresh send (#4249).
        notifyAnswerFinishedInBackground(slotId, wasStopped);
      },
      onError: (error) => {
        const slot = useChatStore.getState().slots[slotId];
        // After Stop, the grace timer commits the partial answer as stopped.
        if (!slot || slot.stopping) return;
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          messages: withStreamingErrorMessage(slot.messages, refusedTurnText(error as { code?: string }) ?? error.message, streamErrorInfo(error)),
          streamingContent: '',
          streamingParts: [],
          streamingCitationMaps: null,
          currentStatusMessage: null,
          abortController: null,
          durableRun: null,
          routePending: false,
        });
      },
      signal: abortController.signal,
    });
  } catch (error) {
    const aborted = abortController.signal.aborted;
    const slot = useChatStore.getState().slots[slotId];
    if (!slot) return;
    useChatStore.getState().updateSlot(slotId, {
      isStreaming: false,
      streamingContent: '',
      streamingParts: [],
      streamingCitationMaps: null,
      currentStatusMessage: null,
      abortController: null,
      durableRun: null,
      routePending: false,
      ...(!aborted
        ? {
            messages: withStreamingErrorMessage(
              slot.messages,
              error instanceof Error ? error.message : 'Failed to resume agent run',
            ),
          }
        : {}),
    });
  }
}

/**
 * Load the next (older) page of messages for a slot and prepend them.
 *
 * Claude/ChatGPT-style infinite scroll: page 1 = most recent batch;
 * each subsequent page returns an older batch. The MessageList calls this
 * when the user scrolls near the top while `messagePagination.hasOlderMessages`.
 */
export async function loadOlderMessagesForSlot(slotId: string): Promise<void> {
  const store = useChatStore.getState();
  const slot = store.slots[slotId];
  if (!slot || !slot.convId) return;

  const pagination = slot.messagePagination;
  if (!pagination?.hasOlderMessages || pagination.isLoadingOlder) return;

  const nextPage = pagination.currentPage + 1;

  // Mark loading so concurrent scroll events don't double-trigger
  store.updateSlot(slotId, {
    messagePagination: { ...pagination, isLoadingOlder: true },
  });

  try {
    const detail = slot.threadAgentId
      ? await AgentsApi.fetchAgentConversation(slot.threadAgentId, slot.convId, { page: nextPage })
      : await ChatApi.fetchConversation(slot.convId, nextPage);

    const newPagination = {
      currentPage: detail.pagination.page,
      hasOlderMessages: detail.pagination.hasNextPage,
      isLoadingOlder: false,
    };

    // Read the freshest slot state at write time to avoid stale closure
    const freshSlot = useChatStore.getState().slots[slotId];
    if (!freshSlot) return;

    // An ask_user_question card is spread over many rows, so an older page built
    // on its own cuts it in two. Join the rows and build the thread again, which
    // is what a full reload would show. A thread that is no longer exactly what
    // its rows build (live rows while streaming) keeps the plain prepend below.
    const loadedRows = freshSlot.rawMessages;
    if (loadedRows && !freshSlot.isStreaming && threadMatchesRows(freshSlot.messages, loadedRows)) {
      const rows = prependOlderRows(loadedRows, detail.messages);
      if (rows === loadedRows) {
        useChatStore.getState().updateSlot(slotId, {
          messagePagination: { currentPage: nextPage, hasOlderMessages: false, isLoadingOlder: false },
        });
        return;
      }
      const rebuilt = loadHistoricalMessages(rows);
      useChatStore.getState().updateSlot(slotId, {
        messages: rebuilt.messages,
        rawMessages: rows,
        messagePagination: newPagination,
        pendingAskUserQuestion: reconcilePendingAfterPrepend(
          freshSlot.pendingAskUserQuestion,
          rebuilt.unansweredAskUserQuestion,
          rebuilt.messages,
        ),
      });
      return;
    }

    const { messages: olderMessages } = loadHistoricalMessages(detail.messages);

    // Deduplicate: if the API returns messages whose IDs are already in the
    // thread (e.g. because a previous SSE complete gave us all messages), drop
    // them to prevent assistant-ui's MessageRepository from crashing with
    // "same id already exists in parent tree".
    const existingIds = new Set(freshSlot.messages.map((m) => m.id));
    const uniqueOlderMessages = olderMessages.filter((m) => !existingIds.has(m.id));

    if (uniqueOlderMessages.length === 0) {
      // All "older" messages are already present → nothing new to prepend;
      // mark pagination exhausted so we don't retry on the next scroll.
      useChatStore.getState().updateSlot(slotId, {
        messagePagination: { currentPage: nextPage, hasOlderMessages: false, isLoadingOlder: false },
      });
      return;
    }

    useChatStore.getState().updateSlot(slotId, {
      // Prepend unique older messages before the existing messages
      messages: [...uniqueOlderMessages, ...freshSlot.messages],
      messagePagination: newPagination,
    });
  } catch (err) {
    console.error('[streaming] Failed to load older messages for slot', slotId, err);
    useChatStore.getState().updateSlot(slotId, {
      messagePagination: { ...pagination, isLoadingOlder: false },
    });
  }
}

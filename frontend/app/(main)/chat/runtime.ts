/**
 * External Store Runtime bridge for assistant-ui.
 *
 * Provides:
 * 1. `buildExternalStoreConfig(activeSlotId)` — returns the config object
 *    consumed by `useExternalStoreRuntime`. Reads `messages` and `isRunning`
 *    from the active slot; wires `onNew` and `onCancel` to streaming.ts.
 *
 * 2. `loadHistoricalMessages()` — transforms backend ConversationMessage[]
 *    into ThreadMessageLike[] (used when initializing a slot).
 */

import type { ExternalStoreAdapter } from '@assistant-ui/react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { useChatStore, ctxKeyFromAgent, getEffectiveModel, isModelReasoningCapable, getAgentDefaultReasoningEffort, projectAgentCapabilities } from './store';
import { streamMessageForSlot, cancelStreamForSlot } from './streaming';
import { showNoModelToast } from './utils/no-model-toast';
import { showRemoteRunInProgressToast } from './utils/remote-run-toast';
import { fetchModelsForContext } from './utils/fetch-models-for-context';
import {
  buildAssistantApiFilters,
  buildStreamRequestModeFields,
  type AppliedFilterNode,
  type AppliedFilters,
  type AttachmentRef,
  type AskUserQuestionAnswer,
  type AskUserQuestionPayload,
  type ChatCollectionAttachment,
  type ChatKnowledgeFilters,
  type ChatSettings,
  type ChatSlot,
  type ConversationMessage,
  type MessagePart,
  type PendingAskUserQuestion,
  type StreamChatRequest,
  type ToolApprovalAnswer,
} from './types';
import { hasAnyFilter } from './utils/tree-selection';
import {
  buildCitationMapsFromApi,
} from './components/message-area/response-tabs/citations';
import { getClientTimezone, getClientCurrentTime } from './utils/client-time';
import { bareToolFullName } from './tool-groups';
import {
  hasUnansweredQuestions,
  mergeAskUserQuestionPayloads,
  parseAnswerMessage,
} from './components/message-area/ask-user-question-card';
import { appendResumeParts } from './utils/tool-display';
import { isToolApprovalResumeQuery } from './tool-approval';

/** Non-empty query required by the chat API when the user sends attachments only (matches Slack bot). */
const ATTACHMENT_ONLY_STREAM_QUERY = 'See below attached file(s).';

/** Prefer the `isFinal` text part from the transcript over `msg.content`.
 * Handles pre-fix conversations where `content` had narration mixed in,
 * and guards against backend regressions. Mirrors Python's extraction in
 * `AnswerFinalizer._run_success_path` (respond.py). */
export const EMPTY_ANSWER_FALLBACK =
  "I wasn't able to generate a response. Please try rephrasing.";

export function isUsableFollowUpText(text: string | undefined): boolean {
  const t = (text ?? '').trim();
  return t.length > 0 && t !== EMPTY_ANSWER_FALLBACK;
}

function extractFinalAnswer(
  parts: ConversationMessage['parts'],
  fallback: string,
): string {
  if (!parts?.length) return fallback;
  for (let i = parts.length - 1; i >= 0; i--) {
    const p = parts[i];
    if (p.type === 'text' && p.isFinal && p.content) {
      return p.content;
    }
  }
  return fallback;
}

/**
 * Extract text content from assistant-ui message content
 */
function extractTextContent(content: ThreadMessageLike['content']): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter(
      (part): part is { type: 'text'; text?: string } =>
        typeof part === 'object' && part !== null && 'type' in part && part.type === 'text'
    )
    .map((part) => (typeof part.text === 'string' ? part.text : ''))
    .join('');
}

/** Plain text of a thread message (used by streaming + message list). */
export function getThreadMessagePlainText(message: ThreadMessageLike): string {
  return extractTextContent(message.content);
}

/** KB collections attached on send (see chat input metadata). */
function readKbCollectionsFromMessage(
  message: ThreadMessageLike
): ChatCollectionAttachment[] | undefined {
  const raw = message.metadata?.custom?.collections;
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: ChatCollectionAttachment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const id = (item as { id?: unknown }).id;
    if (typeof id !== 'string') continue;
    const name = (item as { name?: unknown }).name;
    const kindRaw = (item as { kind?: unknown }).kind;
    const kind =
      kindRaw === 'recordGroup' || kindRaw === 'collectionRoot'
        ? kindRaw
        : undefined;
    out.push({
      id,
      name: typeof name === 'string' ? name : '',
      ...(kind ? { kind } : {}),
    });
  }
  return out.length > 0 ? out : undefined;
}

/** The record groups, folders and records the composer has selected. */
function selectionBelowAppLevel(
  settings: ChatSettings
): Pick<ChatKnowledgeFilters, 'recordGroups' | 'records' | 'recordsExact'> {
  return {
    recordGroups: [...(settings.filters.recordGroups ?? [])],
    records: [...(settings.filters.records ?? [])],
    ...(settings.filters.recordsExact?.length ? { recordsExact: [...settings.filters.recordsExact] } : {}),
  };
}

/** Mirrors chat-send scope resolution from the last user message that carried KB attachments. */
function resolveAssistantFiltersFromSlot(
  slot: { messages: ThreadMessageLike[] },
  settings: ChatSettings
): ChatKnowledgeFilters {
  const storeApps = settings.filters.apps ?? [];
  const storeKb = settings.filters.kb ?? [];
  for (let i = slot.messages.length - 1; i >= 0; i -= 1) {
    const m = slot.messages[i];
    if (m.role !== 'user') continue;
    const msgCollections = readKbCollectionsFromMessage(m);
    if (msgCollections && msgCollections.length > 0) {
      const msgRootIds = msgCollections
        .filter((c) => (c.kind ?? 'collectionRoot') !== 'recordGroup')
        .map((c) => c.id);
      const msgKbIds = msgCollections.filter((c) => c.kind === 'recordGroup').map((c) => c.id);
      return {
        apps: [...new Set([...storeApps, ...msgRootIds])],
        kb: [...msgKbIds],
        ...selectionBelowAppLevel(settings),
      };
    }
  }
  return { apps: [...storeApps], kb: [...storeKb], ...selectionBelowAppLevel(settings) };
}

/**
 * Resolve KB/app filters for a chat POST — either from the outgoing composer message
 * (normal send) or from the latest prior user turn that carried attachments (questionnaire submit).
 */
export function resolveAssistantFiltersForChatSubmit(
  slot: { messages: ThreadMessageLike[] },
  settings: ChatSettings,
  outgoingMessage?: ThreadMessageLike
): ChatKnowledgeFilters {
  const storeApps = settings.filters.apps ?? [];
  const storeKb = settings.filters.kb ?? [];

  if (outgoingMessage) {
    const msgCollections = readKbCollectionsFromMessage(outgoingMessage);
    if (msgCollections && msgCollections.length > 0) {
      const msgRootIds = msgCollections
        .filter((c) => (c.kind ?? 'collectionRoot') !== 'recordGroup')
        .map((c) => c.id);
      const msgKbIds = msgCollections.filter((c) => c.kind === 'recordGroup').map((c) => c.id);
      return {
        apps: [...new Set([...storeApps, ...msgRootIds])],
        kb: [...msgKbIds],
        ...selectionBelowAppLevel(settings),
      };
    }
    return { apps: [...storeApps], kb: [...storeKb], ...selectionBelowAppLevel(settings) };
  }

  return resolveAssistantFiltersFromSlot(slot, settings);
}

/** The agent a slot talks to: the thread's own, else the one in the URL. */
function effectiveAgentIdForSlot(slot: ChatSlot): string | undefined {
  const urlParams =
    typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
  const rawUrlAgent = urlParams?.get('agentId');
  const agentIdFromUrl = rawUrlAgent?.trim() || undefined;
  const slotAgent = slot.threadAgentId?.trim() || null;
  return slotAgent ?? agentIdFromUrl ?? undefined;
}

/**
 * Build the streaming POST body for the given slot (agent vs assistant, filters,
 * tools, model). Used by questionnaire submit and the chat composer bridge.
 *
 * `toolApproval` answers an approval card. Only an agent run can ask, so in an
 * assistant chat the answer goes in Agent mode even if the mode was switched since.
 */
export function buildStreamChatRequestForSlot(
  slotId: string,
  query: string,
  outgoingMessage?: ThreadMessageLike,
  toolApproval?: ToolApprovalAnswer,
  /** `agentMode`: send as agent mode whatever the composer is set to, as an approval answer is. */
  options?: { agentMode?: boolean }
): StreamChatRequest | null {
  const currentState = useChatStore.getState();
  const currentSlot = currentState.slots[slotId];
  if (!currentSlot) return null;

  const assistantFilters = resolveAssistantFiltersForChatSubmit(
    currentSlot,
    currentState.settings,
    outgoingMessage
  );

  const effectiveAgentId = effectiveAgentIdForSlot(currentSlot);

  const isUniversalAgentMode =
    !effectiveAgentId &&
    (currentState.settings.queryMode === 'agent' || Boolean(toolApproval) || Boolean(options?.agentMode));
  // Project context is hydrated from the URL/workspace (`useProjectScopeHydration`), the same
  // way `agentId` is read from the URL above. The server re-applies the allow-list regardless.
  const projectScope = effectiveAgentId ? null : currentState.projectScope;
  const projectIdForCaps = effectiveAgentId
    ? null
    : currentSlot.projectId ?? projectScope?.projectId ?? null;

  const toolsSel = effectiveAgentId
    ? currentState.agentStreamTools
    : isUniversalAgentMode
      ? projectScope
        ? currentState.projectStreamTools
        : currentState.universalAgentStreamTools
      : null;

  const toolCatalog = effectiveAgentId
    ? currentState.agentToolCatalogFullNames
    : projectScope
      ? projectScope.toolCatalogFullNames
      : currentState.universalAgentToolCatalogFullNames;

  const streamTools =
    effectiveAgentId || isUniversalAgentMode
      ? [...new Set((toolsSel === null ? [...toolCatalog] : [...toolsSel]).map(bareToolFullName))]
      : [];

  const modelCtxKey = ctxKeyFromAgent(effectiveAgentId ?? null);
  const rawModel = getEffectiveModel(modelCtxKey);
  if (!rawModel) {
    showNoModelToast();
  }
  const effectiveModel = rawModel ?? { modelKey: '', modelName: '', modelFriendlyName: '' };
  // Neither the user nor the agent chose an effort → leave it off the request
  // so the backend applies the model's own default, then "high".
  const reasoningEffortOverride = currentState.settings.reasoningEffort[modelCtxKey] ?? null;
  const agentDefault = getAgentDefaultReasoningEffort(modelCtxKey);
  const reasoningEffort =
    reasoningEffortOverride ??
    (isModelReasoningCapable(modelCtxKey, effectiveModel) ? agentDefault : null);

  const isAgent = Boolean(effectiveAgentId);
  const knowledgeScope = currentState.agentKnowledgeScope;
  const knowledgeDefaults = currentState.agentKnowledgeDefaults;
  const resolvedAgentKnowledge =
    isAgent && knowledgeScope === null ? knowledgeDefaults : knowledgeScope;

  const isWebSearch = currentState.settings.queryMode === 'web-search' && !isUniversalAgentMode;
  const resolvedScopedKnowledge = isAgent
    ? resolvedAgentKnowledge
    : projectScope && !isWebSearch
      ? (currentState.projectKnowledgeScope ?? projectScope.knowledgeDefaults)
      : null;

  const resolvedFilters: ChatKnowledgeFilters = resolvedScopedKnowledge
    ? {
        apps: resolvedScopedKnowledge.apps.filter(
          (id): id is string => typeof id === 'string' && id.trim().length > 0
        ),
        kb: resolvedScopedKnowledge.kb.filter(
          (id): id is string => typeof id === 'string' && id.trim().length > 0
        ),
        ...(resolvedScopedKnowledge.recordGroups?.length
          ? { recordGroups: [...resolvedScopedKnowledge.recordGroups] }
          : {}),
        ...(resolvedScopedKnowledge.records?.length ? { records: [...resolvedScopedKnowledge.records] } : {}),
        ...(resolvedScopedKnowledge.recordsExact?.length
          ? { recordsExact: [...resolvedScopedKnowledge.recordsExact] }
          : {}),
      }
    : isAgent
      ? { apps: [], kb: [] }
      : buildAssistantApiFilters(assistantFilters);

  const metaCache = currentState.collectionMetaCache;
  // Which nodes the question names with @: those show as chips in it when it is read back.
  const rawMentionIds = outgoingMessage?.metadata?.custom?.mentionIds;
  const mentionIds = new Set(
    Array.isArray(rawMentionIds) ? rawMentionIds.filter((id): id is string => typeof id === 'string') : []
  );
  const buildAppliedFilterNodes = (ids: string[]): AppliedFilterNode[] =>
    ids
      .filter((id) => id.trim().length > 0)
      .map((id) => {
        const meta = metaCache[id];
        return {
          id,
          name: meta?.name ?? currentState.collectionNamesCache[id] ?? id,
          nodeType: meta?.nodeType ?? '',
          connector: meta?.connector ?? '',
          ...(meta?.extension ? { extension: meta.extension } : {}),
          ...(meta?.mimeType ? { mimeType: meta.mimeType } : {}),
          mentioned: mentionIds.has(id),
        };
      });

  const hasFilters = hasAnyFilter(resolvedFilters);
  const appliedFilters: AppliedFilters | undefined = hasFilters
    ? {
        apps: buildAppliedFilterNodes(resolvedFilters.apps),
        kb: buildAppliedFilterNodes(resolvedFilters.kb),
        ...(resolvedFilters.recordGroups?.length
          ? { recordGroups: buildAppliedFilterNodes(resolvedFilters.recordGroups) }
          : {}),
        ...(resolvedFilters.records?.length
          ? { records: buildAppliedFilterNodes(resolvedFilters.records) }
          : {}),
        ...(resolvedFilters.recordsExact?.length
          ? { recordsExact: buildAppliedFilterNodes(resolvedFilters.recordsExact) }
          : {}),
      }
    : isAgent
      ? { apps: [], kb: [] }
      : undefined;

  const request: StreamChatRequest = {
    query,
    ...effectiveModel,
    ...(reasoningEffort ? { reasoningEffort } : {}),
    ...(isUniversalAgentMode
      ? { chatMode: 'agent' as const }
      : buildStreamRequestModeFields(currentState.settings, isAgent)),
    timezone: getClientTimezone(),
    currentTime: getClientCurrentTime(),
    filters: resolvedFilters,
    ...(appliedFilters ? { appliedFilters } : {}),
    conversationId: currentSlot.convId || undefined,
    // Only meaningful for a brand-new conversation — once `convId` exists the
    // session row is the source of truth and this is ignored server-side.
    ...(!currentSlot.convId && currentSlot.projectId
      ? { projectId: currentSlot.projectId }
      : {}),
    ...(effectiveAgentId
      ? {
          agentId: effectiveAgentId,
          // `toolsSel === null` means "everything selected" (no explicit
          // filter) — omit `agentStreamTools` entirely rather than sending
          // every toolCatalog fullName exploded out. The backend already
          // treats a missing/`None` `tools` field as "use every configured
          // toolset" (see agent.py), so this is both smaller on the wire
          // and semantically correct — an exploded full list would defeat
          // that "no filter" meaning and needlessly re-approach the
          // request-size cap on agents with many multi-action toolsets.
          ...(toolsSel !== null ? { agentStreamTools: streamTools } : {}),
          agentCapabilities: currentState.scopedAgentCapabilities[effectiveAgentId]
            ?? { internalSearch: true, webSearch: true },
        }
      : isUniversalAgentMode
        ? {
            ...(toolsSel !== null ? { agentStreamTools: streamTools } : {}),
            agentCapabilities: projectIdForCaps
              ? projectAgentCapabilities(currentState.scopedAgentCapabilities, projectIdForCaps)
              : currentState.settings.agentCapabilities,
          }
        : {}),
    ...(toolApproval ? { toolApproval } : {}),
  };

  return request;
}

export interface LoadHistoricalResult {
  messages: ThreadMessageLike[];
  unansweredAskUserQuestion: PendingAskUserQuestion | null;
}

function isAskUserQuestionResumeQuery(text: string | undefined | null): boolean {
  return typeof text === 'string' && text.trimStart().startsWith('User selections:');
}

/** The stored record of a card resume (a question card or an approval card)
 *  whose stream died. The live view reports that with a toast and keeps the
 *  card, so replaying the row as an error bubble would show a failure the user
 *  never saw. A plain chat failure sits behind a real user query and still renders. */
function isFailedCardResumeError(
  messages: ConversationMessage[],
  errorIndex: number,
): boolean {
  for (let j = errorIndex - 1; j >= 0; j -= 1) {
    const prev = messages[j];
    if (prev?.messageType === 'tool_call') continue;
    return (
      prev?.messageType === 'user_query' &&
      (isAskUserQuestionResumeQuery(prev.content) || isToolApprovalResumeQuery(prev.content))
    );
  }
  return false;
}

function findQuestionAssistantRow(
  result: ThreadMessageLike[],
  unansweredAssistantId: string | null,
): ThreadMessageLike | undefined {
  for (let i = result.length - 1; i >= 0; i -= 1) {
    const row = result[i];
    if (row.role !== 'assistant') continue;
    const custom = row.metadata?.custom as { persistedAskUserQuestion?: unknown } | undefined;
    if (custom?.persistedAskUserQuestion) return row;
    if (unansweredAssistantId && row.id === unansweredAssistantId) return row;
  }
  return undefined;
}

/** Walk past tool_calls and more User selections after a resume. */
function firstResumeFollowUp(
  messages: ConversationMessage[],
  resumeIndex: number,
): ConversationMessage | null {
  for (let k = resumeIndex + 1; k < messages.length; k++) {
    const next = messages[k];
    if (next.messageType === 'tool_call') continue;
    if (next.messageType === 'user_query') {
      if (isAskUserQuestionResumeQuery(next.content)) continue;
      return next;
    }
    return next;
  }
  return null;
}

/** A resume row only completes the card when a later bot_response has text. */
function hasFollowUpAfterResume(
  messages: ConversationMessage[],
  resumeIndex: number,
): boolean {
  const next = firstResumeFollowUp(messages, resumeIndex);
  if (next?.messageType !== 'bot_response') return false;
  return isUsableFollowUpText(extractFinalAnswer(next.parts, next.content));
}

/**
 * Merge the next bot onto the card unless a later regular chat sits first.
 * An empty bot still counts — chained questions land on the same row that way.
 */
function nextResumeFollowUpIsBot(
  messages: ConversationMessage[],
  resumeIndex: number,
): boolean {
  return firstResumeFollowUp(messages, resumeIndex)?.messageType === 'bot_response';
}

function isAskUserQuestionAnswered(
  messages: ConversationMessage[],
  cardBotIndex: number,
): boolean {
  for (let j = cardBotIndex + 1; j < messages.length; j++) {
    if (messages[j].messageType !== 'user_query') continue;
    if (!isAskUserQuestionResumeQuery(messages[j].content)) continue;
    if (hasFollowUpAfterResume(messages, j)) return true;
  }
  return false;
}

function stampPersistedQuestionCard(
  result: ThreadMessageLike[],
  assistantId: string,
  payload: AskUserQuestionPayload,
  overwrite = false,
): void {
  const row = result.find((m) => m.role === 'assistant' && m.id === assistantId);
  if (!row) return;
  const prevCustom = (row.metadata?.custom ?? {}) as Record<string, unknown>;
  const prevPayload = prevCustom.persistedAskUserQuestion as AskUserQuestionPayload | undefined;
  if (prevPayload && !overwrite) return;
  const next = prevPayload && overwrite
    ? mergeAskUserQuestionPayloads(prevPayload, payload)
    : payload;
  Object.assign(row, {
    metadata: {
      ...row.metadata,
      custom: { ...prevCustom, persistedAskUserQuestion: next },
    },
  });
}

function askPayloadFromUnknown(raw: unknown): AskUserQuestionPayload | null {
  if (typeof raw === 'string') {
    try {
      return askPayloadFromUnknown(JSON.parse(raw));
    } catch {
      return null;
    }
  }
  if (!raw || typeof raw !== 'object') return null;
  const tr = raw as Record<string, unknown>;
  if (Array.isArray(tr.questions) && tr.questions.length > 0) {
    return tr as unknown as AskUserQuestionPayload;
  }
  return askPayloadFromUnknown(tr.toolData);
}

function askPayloadFromToolCall(msg: ConversationMessage): AskUserQuestionPayload | null {
  const askTool = msg.tools?.find((t) =>
    typeof t.toolName === 'string' && t.toolName.includes('ask_user_question'),
  );
  return askPayloadFromUnknown(askTool?.toolResult);
}

function askPayloadFromParts(parts: ConversationMessage['parts']): AskUserQuestionPayload | null {
  if (!parts?.length) return null;
  for (const part of parts) {
    if (part.type !== 'tool_call' || !part.toolName?.includes('ask_user_question')) {
      continue;
    }
    const payload = askPayloadFromUnknown(part.resultPreview ?? part.resultSummary);
    if (payload) return payload;
  }
  return null;
}

function peekFollowingAskPayload(
  messages: ConversationMessage[],
  botIndex: number,
): AskUserQuestionPayload | null {
  for (let j = botIndex + 1; j < messages.length; j++) {
    const next = messages[j];
    if (next.messageType !== 'tool_call') break;
    const payload = askPayloadFromToolCall(next);
    if (payload) return payload;
  }
  return null;
}

/**
 * Transform backend conversation messages into assistant-ui thread format.
 *
 * Builds CitationMaps from CitationApiResponse for each bot_response.
 *
 * Handles `tool_call` messages:
 *   - Filters them out of the output (no ThreadMessageLike entry).
 *   - When a `tool_call` with `ask_user_question` precedes a `bot_response`:
 *     • If a later `User selections:` row exists → attaches payload as
 *       `persistedAskUserQuestion` in metadata (read-only display).
 *     • If no `User selections:` follows → returns it as
 *       `unansweredAskUserQuestion` for the caller to restore interactive state.
 *   - A `user_query` that starts with `User selections:` is the synthetic
 *     resume of that card — it is not shown as a user bubble. The following
 *     `bot_response` is merged into the question's assistant row so the
 *     answer continues in the same turn.
 */
export function loadHistoricalMessages(
  messages: ConversationMessage[]
): LoadHistoricalResult {
  const result: ThreadMessageLike[] = [];
  let toolPayload: AskUserQuestionPayload | null = null;
  let lastUnansweredAssistantId: string | null = null;
  let lastUnansweredPayload: AskUserQuestionPayload | null = null;
  let lastUnansweredAnswers: Record<string, AskUserQuestionAnswer> = {};
  let mergeNextBotIntoId: string | null = null;
  // A second answer to a card that already had its reply (another tab): the server ran nothing.
  let skipStaleResumeReply = false;
  const closedCardRowIds = new Set<string>();

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];

    if (msg.messageType === 'tool_call') {
      const payload = askPayloadFromToolCall(msg);
      if (payload) {
        const last = result[result.length - 1];
        if (last?.role === 'assistant' && typeof last.id === 'string') {
          const botIndex = messages.findIndex(
            (m) => String(m._id) === String(last.id),
          );
          stampPersistedQuestionCard(result, last.id, payload, true);
          const rowCustom = (last.metadata?.custom ?? {}) as {
            persistedAskUserQuestion?: AskUserQuestionPayload;
            persistedAskUserQuestionAnswers?: Record<string, AskUserQuestionAnswer>;
          };
          const stamped = rowCustom.persistedAskUserQuestion ?? payload;
          const stampedAnswers = rowCustom.persistedAskUserQuestionAnswers ?? {};
          toolPayload = stamped;
          const knownAnswers = lastUnansweredAssistantId === last.id
            ? { ...lastUnansweredAnswers, ...stampedAnswers }
            : stampedAnswers;
          // The agent asked a question the user already answered, so merging it
          // added nothing unanswered. Reopening the card would loop the user
          // through the same choice, so it stays closed on the answers it has.
          if (Object.keys(knownAnswers).length > 0 && !hasUnansweredQuestions(stamped, knownAnswers)) {
            closedCardRowIds.add(last.id);
            Object.assign(last, {
              metadata: {
                ...last.metadata,
                custom: {
                  ...(last.metadata?.custom ?? {}),
                  persistedAskUserQuestionAnswers: knownAnswers,
                },
              },
            });
            if (lastUnansweredAssistantId === last.id) {
              lastUnansweredAssistantId = null;
              lastUnansweredPayload = null;
              lastUnansweredAnswers = {};
            }
            continue;
          }
          closedCardRowIds.delete(last.id);
          // A resume that ended on another question leaves this turn answered
          // while the newly merged question is not — keep the card interactive,
          // carrying the earlier selections so it reopens on the new question.
          const answered =
            botIndex >= 0 &&
            isAskUserQuestionAnswered(messages, botIndex) &&
            !hasUnansweredQuestions(stamped, stampedAnswers);
          if (!answered) {
            lastUnansweredAssistantId = last.id;
            lastUnansweredPayload = stamped;
            lastUnansweredAnswers = { ...lastUnansweredAnswers, ...stampedAnswers };
          }
        } else {
          toolPayload = toolPayload
            ? mergeAskUserQuestionPayloads(toolPayload, payload)
            : payload;
        }
      }
      continue;
    }

    if (msg.messageType === 'error') {
      toolPayload = null;
      if (isFailedCardResumeError(messages, i)) {
        // That answer produced no reply to merge; a later one comes after its own answer row.
        mergeNextBotIntoId = null;
        continue;
      }
      result.push({
        id: msg._id,
        role: 'assistant' as const,
        content: [{ type: 'text' as const, text: msg.content || 'An error occurred. Please try again.' }],
        metadata: {
          custom: {
            citationMaps: buildCitationMapsFromApi([]),
          },
        },
      });
      continue;
    }

    if (msg.messageType === 'bot_response' && skipStaleResumeReply) {
      skipStaleResumeReply = false;
      toolPayload = null;
      continue;
    }

    if (msg.messageType === 'bot_response') {
      const capturedPayload =
        toolPayload ?? askPayloadFromParts(msg.parts) ?? askPayloadFromToolCall(msg);
      toolPayload = null;

      const isAnswered = capturedPayload
        ? isAskUserQuestionAnswered(messages, i)
        : false;
      // A reply merged into an earlier card row (`mergeNextBotIntoId`) never
      // becomes a row of its own, so the card must stay addressed to the row
      // that survives: a resume bound to the merged-away id cannot find it and
      // streams onto the newest turn instead. When that row is already the
      // tracked one, its payload and selections are the fuller pair — the
      // `tool_call` branch above merged the new question into them.
      const cardRowId = mergeNextBotIntoId ?? msg._id;
      if (
        capturedPayload &&
        !isAnswered &&
        !closedCardRowIds.has(cardRowId) &&
        cardRowId !== lastUnansweredAssistantId
      ) {
        if (lastUnansweredAssistantId && lastUnansweredPayload) {
          stampPersistedQuestionCard(
            result, lastUnansweredAssistantId, lastUnansweredPayload,
          );
        }
        lastUnansweredAssistantId = cardRowId;
        lastUnansweredPayload = capturedPayload;
        lastUnansweredAnswers = {};
      }

      // Each rating is appended to `feedback`, so the last entry is the one the user chose.
      const feedbackHistory = (msg.feedback as Array<{ isHelpful?: boolean }> | undefined) ?? [];
      const feedbackEntry = feedbackHistory[feedbackHistory.length - 1];
      const feedbackInfo = feedbackEntry?.isHelpful === true
        ? { value: 'like' as const }
        : feedbackEntry?.isHelpful === false
          ? { value: 'dislike' as const }
          : undefined;

      const answerText = extractFinalAnswer(msg.parts, msg.content);
      // A run stopped before any text arrived is saved as an empty stopped
      // reply. The live view drops that row (`buildStoppedMessages`); showing
      // it after a reload would add an empty "Stopped" bubble the user never saw.
      // The sign-in part alone isn't something the user saw stream.
      const shownParts = msg.parts?.some((part) => part.type !== 'mcp_sign_in') ?? false;
      if (
        msg.status === 'stopped' &&
        !answerText.trim() &&
        !shownParts &&
        !capturedPayload &&
        !peekFollowingAskPayload(messages, i)
      ) {
        mergeNextBotIntoId = null;
        continue;
      }

      if (mergeNextBotIntoId) {
        if (!answerText.trim() && !msg.parts?.length) {
          mergeNextBotIntoId = null;
          continue;
        }
        const last = result.find((m) => m.role === 'assistant' && m.id === mergeNextBotIntoId);
        mergeNextBotIntoId = null;
        if (last?.role === 'assistant') {
          const prevCustom = (last.metadata?.custom ?? {}) as Record<string, unknown>;
          const prevCustomRest = { ...prevCustom };
          delete prevCustomRest.status;
          const prevParts = Array.isArray(prevCustomRest.persistedParts)
            ? (prevCustomRest.persistedParts as MessagePart[])
            : [];
          const nextParts = msg.parts?.length ? appendResumeParts(prevParts, msg.parts) : prevParts;
          Object.assign(last, {
            content: [{ type: 'text' as const, text: answerText }],
            metadata: {
              ...last.metadata,
              custom: {
                ...prevCustomRest,
                messageId: msg._id,
                citationMaps: buildCitationMapsFromApi(msg.citations || []),
                confidence: msg.confidence,
                modelInfo: msg.modelInfo,
                ...(feedbackInfo ? { feedbackInfo } : {}),
                ...(msg.status === 'stopped' ? { status: 'stopped' as const } : {}),
                ...(nextParts.length ? { persistedParts: nextParts } : {}),
              },
            },
          });
          continue;
        }
      }

      result.push({
        id: msg._id,
        role: 'assistant' as const,
        content: [{ type: 'text' as const, text: answerText }],
        metadata: {
          custom: {
            messageId: msg._id,
            citationMaps: buildCitationMapsFromApi(msg.citations || []),
            confidence: msg.confidence,
            modelInfo: msg.modelInfo,
            ...(feedbackInfo ? { feedbackInfo } : {}),
            ...(msg.status === 'stopped' ? { status: 'stopped' as const } : {}),
            ...(capturedPayload
              ? { persistedAskUserQuestion: capturedPayload }
              : {}),
            // Agent-activity transcript (`agui` protocol only — see
            // TranscriptCollector/buildAIResponseMessage). Absent for the
            // legacy protocol and every pre-existing conversation; consumers
            // fall back to plain `content` (see AgentActivityTimeline).
            ...(msg.parts?.length ? { persistedParts: msg.parts } : {}),
          },
        },
      });
      continue;
    }

    if (isToolApprovalResumeQuery(msg.content)) {
      // An answer to an approval card carried on the reply that stopped on the card: it is no
      // bubble of its own, and the reply it produced joins that row.
      const row = [...result].reverse().find((m) => m.role === 'assistant');
      if (row?.role === 'assistant' && typeof row.id === 'string') mergeNextBotIntoId = row.id;
      toolPayload = null;
      continue;
    }

    if (isAskUserQuestionResumeQuery(msg.content)) {
      const last = findQuestionAssistantRow(result, lastUnansweredAssistantId);
      if (last?.role === 'assistant' && typeof last.id === 'string') {
        const prevCustom = (last.metadata?.custom ?? {}) as Record<string, unknown>;
        const payload = (
          prevCustom.persistedAskUserQuestion ?? lastUnansweredPayload
        ) as AskUserQuestionPayload | undefined;
        const settled = prevCustom.persistedAskUserQuestionAnswers as
          | Record<string, AskUserQuestionAnswer>
          | undefined;
        // A reply that was stopped did not finish, so answering again is a retry, not a repeat.
        if (
          !lastUnansweredAssistantId &&
          payload &&
          settled &&
          Object.keys(settled).length > 0 &&
          !hasUnansweredQuestions(payload, settled) &&
          prevCustom.status !== 'stopped'
        ) {
          skipStaleResumeReply = nextResumeFollowUpIsBot(messages, i);
          toolPayload = null;
          continue;
        }
        const followUpComplete = hasFollowUpAfterResume(messages, i);
        mergeNextBotIntoId = nextResumeFollowUpIsBot(messages, i) ? last.id : null;
        const parsed = payload ? parseAnswerMessage(msg.content, payload) : {};
        const prevAnswers = (
          (prevCustom.persistedAskUserQuestionAnswers as Record<string, AskUserQuestionAnswer> | undefined)
          ?? lastUnansweredAnswers
        );
        const answers = { ...prevAnswers, ...parsed };
        Object.assign(last, {
          metadata: {
            ...last.metadata,
            custom: {
              ...prevCustom,
              // Older threads could still answer a card after chatting past it; the answer wins.
              ...(payload
                ? { persistedAskUserQuestion: payload.repliedInChat ? { ...payload, repliedInChat: undefined } : payload }
                : {}),
              ...(followUpComplete && Object.keys(answers).length
                ? { persistedAskUserQuestionAnswers: answers }
                : {}),
            },
          },
        });
        if (followUpComplete) {
          lastUnansweredAssistantId = null;
          lastUnansweredPayload = null;
          lastUnansweredAnswers = {};
        } else {
          lastUnansweredAssistantId = last.id;
          if (payload) lastUnansweredPayload = payload;
          lastUnansweredAnswers = answers;
        }
      }
      toolPayload = null;
      continue;
    }

    mergeNextBotIntoId = null;
    toolPayload = null;
    // Typed while a card was open, the message answered it (the server treats it so): the card closes.
    if (lastUnansweredAssistantId && lastUnansweredPayload) {
      const cardRowId = lastUnansweredAssistantId;
      const row = result.find((m) => m.role === 'assistant' && m.id === cardRowId);
      if (row) {
        const prevCustom = (row.metadata?.custom ?? {}) as Record<string, unknown>;
        const payload = (prevCustom.persistedAskUserQuestion as AskUserQuestionPayload | undefined)
          ?? lastUnansweredPayload;
        Object.assign(row, {
          metadata: {
            ...row.metadata,
            custom: {
              ...prevCustom,
              persistedAskUserQuestion: { ...payload, repliedInChat: true },
              ...(Object.keys(lastUnansweredAnswers).length > 0
                ? { persistedAskUserQuestionAnswers: lastUnansweredAnswers }
                : {}),
            },
          },
        });
      }
      lastUnansweredAssistantId = null;
      lastUnansweredPayload = null;
      lastUnansweredAnswers = {};
    }
    // A real question: the reply after it is its own row, whatever came before.
    mergeNextBotIntoId = null;
    result.push({
      id: msg._id,
      role: 'user' as const,
      content: [{ type: 'text' as const, text: msg.content }],
      metadata: {
        custom: {
          createdAt: msg.createdAt,
          ...(msg.appliedFilters ? { appliedFilters: msg.appliedFilters } : {}),
          ...(msg.attachments?.length ? { attachments: msg.attachments } : {}),
        },
      },
    });
  }

  let unanswered: PendingAskUserQuestion | null = null;
  if (lastUnansweredAssistantId && lastUnansweredPayload) {
    unanswered = {
      assistantMessageId: lastUnansweredAssistantId,
      payload: lastUnansweredPayload,
      answers: lastUnansweredAnswers,
      ...(Object.keys(lastUnansweredAnswers).length > 0
        ? { sentAnswers: lastUnansweredAnswers }
        : {}),
      status: 'pending',
    };
  }

  return { messages: result, unansweredAskUserQuestion: unanswered };
}

/** Attachment refs attached on send (see chat input metadata). */
function readAttachmentsFromMessage(
  message: ThreadMessageLike
): AttachmentRef[] | undefined {
  const raw = message.metadata?.custom?.attachments;
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: AttachmentRef[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const recordId = (item as { recordId?: unknown }).recordId;
    const virtualRecordId = (item as { virtualRecordId?: unknown }).virtualRecordId;
    if (typeof recordId !== 'string' || typeof virtualRecordId !== 'string') continue;
    out.push({
      recordId,
      recordName: String((item as { recordName?: unknown }).recordName ?? ''),
      mimeType: String((item as { mimeType?: unknown }).mimeType ?? ''),
      extension: String((item as { extension?: unknown }).extension ?? ''),
      virtualRecordId,
    });
  }
  return out.length > 0 ? out : undefined;
}

/** An older page's rows ahead of the rows the thread was built from. Returns
 *  `loaded` itself when the page adds nothing new. */
export function prependOlderRows(
  loaded: ConversationMessage[],
  older: ConversationMessage[],
): ConversationMessage[] {
  const have = new Set(loaded.map((m) => m._id));
  const fresh = older.filter((m) => !have.has(m._id));
  return fresh.length === 0 ? loaded : [...fresh, ...loaded];
}

function rowStatus(message: ThreadMessageLike): unknown {
  return (message.metadata?.custom as { status?: unknown } | undefined)?.status;
}

/** Whether `messages` is still exactly what `rows` build. A thread that has
 *  drifted (live rows, a stopped reply) cannot be rebuilt without losing them.
 *  A stopped resume keeps the card row's id, so its text and status count too. */
export function threadMatchesRows(
  messages: ThreadMessageLike[],
  rows: ConversationMessage[],
): boolean {
  const built = loadHistoricalMessages(rows).messages;
  return built.length === messages.length && built.every((m, i) => {
    const shown = messages[i];
    return m.id === shown.id
      && getThreadMessagePlainText(m) === getThreadMessagePlainText(shown)
      && rowStatus(m) === rowStatus(shown);
  });
}

function followCardRow(
  pending: PendingAskUserQuestion,
  messages: ThreadMessageLike[],
): PendingAskUserQuestion {
  const rows = messages.filter((m) => m.role === 'assistant');
  if (rows.some((m) => m.id === pending.assistantMessageId)) return pending;
  const ids = new Set(pending.payload.questions.map((q) => q.uuid));
  const row = rows.find((m) => {
    const card = (m.metadata?.custom as { persistedAskUserQuestion?: AskUserQuestionPayload } | undefined)
      ?.persistedAskUserQuestion;
    return card?.questions?.some((q) => q.uuid && ids.has(q.uuid));
  });
  return row && typeof row.id === 'string' ? { ...pending, assistantMessageId: row.id } : pending;
}

/** The open card after an older page joined the thread. Joining can merge the
 *  card's row into an earlier one, so the open card is re-pointed at it, and
 *  answers picked but not yet sent are kept. */
export function reconcilePendingAfterPrepend(
  current: PendingAskUserQuestion | null,
  rebuilt: PendingAskUserQuestion | null,
  messages: ThreadMessageLike[],
): PendingAskUserQuestion | null {
  if (!current) return rebuilt;
  if (rebuilt) {
    return current.status === 'pending'
      ? { ...rebuilt, answers: { ...rebuilt.answers, ...current.answers } }
      : rebuilt;
  }
  return followCardRow(current, messages);
}
/**
 * Build the ExternalStoreAdapter config for `useExternalStoreRuntime`.
 *
 * This function is called on every render of the chat page.
 * It reads the active slot's messages + streaming state and provides
 * `onNew` / `onCancel` callbacks routed to slot-scoped streaming.
 *
 * @param activeSlotId — current active slot key (or null for new chat screen)
 */
export function buildExternalStoreConfig(
  activeSlotId: string | null
): ExternalStoreAdapter<ThreadMessageLike> {
  const state = useChatStore.getState();
  const slot = activeSlotId ? state.slots[activeSlotId] : null;

  return {
    messages: slot?.messages ?? [],
    isRunning: slot?.isStreaming ?? false,

    // Required when T = ThreadMessageLike (identity — our messages are already ThreadMessageLike)
    convertMessage: (msg: ThreadMessageLike) => msg,

    onNew: async (message) => {
      // Read activeSlotId from store at invocation time — NOT from the
      // closure. ChatInputWrapper.handleSend creates a slot and sets
      // activeSlotId synchronously in Zustand before calling
      // threadRuntime.append(), but React hasn't re-rendered yet, so
      // the closure's `activeSlotId` is still stale (null for new chats).
      const targetSlotId = useChatStore.getState().activeSlotId;
      if (!targetSlotId) return;

      const displayQuery = extractTextContent(message.content).trim();
      const msgAttachmentsEarly = readAttachmentsFromMessage(message);
      if (!displayQuery && (!msgAttachmentsEarly || msgAttachmentsEarly.length === 0)) return;

      const currentState = useChatStore.getState();
      const currentSlot = currentState.slots[targetSlotId];
      if (!currentSlot) return;
      // Stop then send: `stopping` means the user already cancelled this run
      // and a follow-up is allowed to start before the grace timer settles it.
      if (currentSlot.isStreaming && !currentSlot.stopping) return;
      if (currentSlot.remoteRunInProgress) {
        showRemoteRunInProgressToast();
        return;
      }

      const msgAttachments = msgAttachmentsEarly;

      const apiQuery =
        displayQuery ||
        (msgAttachments && msgAttachments.length > 0
          ? ATTACHMENT_ONLY_STREAM_QUERY
          : '');
      if (!apiQuery) return;

      // A message sent before the page's model list arrives would go out with no
      // model and be rejected; wait for the list, as regenerate does.
      const modelCtxKey = ctxKeyFromAgent(effectiveAgentIdForSlot(currentSlot) ?? null);
      if (!getEffectiveModel(modelCtxKey)) {
        await fetchModelsForContext(modelCtxKey).catch((error: unknown) => {
          console.warn('[runtime] Failed to fetch models before sending:', error);
        });
      }

      const request = buildStreamChatRequestForSlot(targetSlotId, apiQuery, message);
      if (!request) return;

      if (msgAttachments) {
        request.attachments = msgAttachments;
      }

      // Fire-and-forget — streaming.ts handles all state updates.
      // Keep `displayQuery` for the slot user row + streamingQuestion so attachment-only turns still match an empty text bubble.
      streamMessageForSlot(targetSlotId, displayQuery, request);
    },

    onCancel: async () => {
      const targetSlotId = useChatStore.getState().activeSlotId;
      if (targetSlotId) {
        cancelStreamForSlot(targetSlotId);
      }
    },
  };
}

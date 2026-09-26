/**
 * External Store Runtime bridge for assistant-ui.
 *
 * Provides:
 * 1. `buildExternalStoreConfig(activeSlotId)` â€” returns the config object
 *    consumed by `useExternalStoreRuntime`. Reads `messages` and `isRunning`
 *    from the active slot; wires `onNew` and `onCancel` to streaming.ts.
 *
 * 2. `loadHistoricalMessages()` â€” transforms backend ConversationMessage[]
 *    into ThreadMessageLike[] (used when initializing a slot).
 */

import type { ExternalStoreAdapter } from '@assistant-ui/react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { useChatStore, ctxKeyFromAgent, getEffectiveModel, isModelReasoningCapable, getAgentDefaultReasoningEffort } from './store';
import { streamMessageForSlot, cancelStreamForSlot } from './streaming';
import { showNoModelToast } from './utils/no-model-toast';
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
  DEFAULT_REASONING_EFFORT,
} from './types';
import {
  buildCitationMapsFromApi,
} from './components/message-area/response-tabs/citations';
import { getClientTimezone, getClientCurrentTime } from './utils/client-time';
import { bareToolFullName } from './tool-groups';
import { mergeAskUserQuestionPayloads, parseAnswerMessage } from './components/message-area/ask-user-question-card';
import { appendResumeParts } from './utils/tool-display';

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
      };
    }
  }
  return { apps: [...storeApps], kb: [...storeKb] };
}

/**
 * Resolve KB/app filters for a chat POST â€” either from the outgoing composer message
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
      };
    }
    return { apps: [...storeApps], kb: [...storeKb] };
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
 */
export function buildStreamChatRequestForSlot(
  slotId: string,
  query: string,
  outgoingMessage?: ThreadMessageLike
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
    !effectiveAgentId && currentState.settings.queryMode === 'agent';
  // Project context is hydrated from the URL/workspace (`useProjectScopeHydration`), the same
  // way `agentId` is read from the URL above. The server re-applies the allow-list regardless.
  const projectScope = effectiveAgentId ? null : currentState.projectScope;

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
  // No explicit user choice → prefer the agent's configured default, then
  // fall back to DEFAULT_REASONING_EFFORT for reasoning-capable models.
  const reasoningEffortOverride = currentState.settings.reasoningEffort[modelCtxKey] ?? null;
  const agentDefault = getAgentDefaultReasoningEffort(modelCtxKey);
  const reasoningEffort =
    reasoningEffortOverride ??
    (isModelReasoningCapable(modelCtxKey, effectiveModel)
      ? (agentDefault ?? DEFAULT_REASONING_EFFORT)
      : null);

  const isAgent = Boolean(effectiveAgentId);
  const knowledgeScope = currentState.agentKnowledgeScope;
  const knowledgeDefaults = currentState.agentKnowledgeDefaults;
  const resolvedAgentKnowledge =
    isAgent && knowledgeScope === null ? knowledgeDefaults : knowledgeScope;

  const isWebSearch = currentState.settings.queryMode === 'web-search';
  const resolvedScopedKnowledge = isAgent
    ? resolvedAgentKnowledge
    : projectScope && !isWebSearch
      ? (currentState.projectKnowledgeScope ?? projectScope.knowledgeDefaults)
      : null;

  const resolvedFilters = resolvedScopedKnowledge
    ? {
        apps: resolvedScopedKnowledge.apps.filter(
          (id): id is string => typeof id === 'string' && id.trim().length > 0
        ),
        kb: resolvedScopedKnowledge.kb.filter(
          (id): id is string => typeof id === 'string' && id.trim().length > 0
        ),
      }
    : isAgent
      ? { apps: [], kb: [] }
      : buildAssistantApiFilters(assistantFilters);

  const metaCache = currentState.collectionMetaCache;
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
        };
      });

  const hasFilters = resolvedFilters.apps.length > 0 || resolvedFilters.kb.length > 0;
  const appliedFilters: AppliedFilters | undefined = hasFilters
    ? {
        apps: buildAppliedFilterNodes(resolvedFilters.apps),
        kb: buildAppliedFilterNodes(resolvedFilters.kb),
      }
    : isAgent
      ? { apps: [], kb: [] }
      : undefined;

  const request: StreamChatRequest = {
    query,
    ...effectiveModel,
    ...(reasoningEffort ? { reasoningEffort } : {}),
    ...buildStreamRequestModeFields(currentState.settings, isAgent),
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
            agentCapabilities: currentState.settings.agentCapabilities,
          }
        : {}),
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

/** The stored record of a card resume whose stream died. The live view reports
 *  that with a toast and keeps the card, so replaying the row as an error
 *  bubble would show a failure the user never saw. A plain chat failure sits
 *  behind a real user query and still renders. */
function isFailedAskUserQuestionResumeError(
  messages: ConversationMessage[],
  errorIndex: number,
): boolean {
  for (let j = errorIndex - 1; j >= 0; j -= 1) {
    const prev = messages[j];
    if (prev?.messageType === 'tool_call') continue;
    return (
      prev?.messageType === 'user_query' &&
      isAskUserQuestionResumeQuery(prev.content)
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

/** A resume row only completes the card when a later bot_response has text. */
function hasFollowUpAfterResume(
  messages: ConversationMessage[],
  resumeIndex: number,
): boolean {
  for (let k = resumeIndex + 1; k < messages.length; k++) {
    const next = messages[k];
    if (next.messageType === 'tool_call') continue;
    if (next.messageType === 'user_query') {
      if (isAskUserQuestionResumeQuery(next.content)) continue;
      return false;
    }
    if (next.messageType === 'error') return false;
    if (next.messageType === 'bot_response') {
      return isUsableFollowUpText(extractFinalAnswer(next.parts, next.content));
    }
  }
  return false;
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
          const stamped = (
            (last.metadata?.custom ?? {}) as { persistedAskUserQuestion?: AskUserQuestionPayload }
          ).persistedAskUserQuestion ?? payload;
          toolPayload = stamped;
          const answered =
            botIndex >= 0 && isAskUserQuestionAnswered(messages, botIndex);
          if (!answered) {
            lastUnansweredAssistantId = last.id;
            lastUnansweredPayload = stamped;
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
      if (isFailedAskUserQuestionResumeError(messages, i)) {
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

    if (msg.messageType === 'bot_response') {
      const capturedPayload =
        toolPayload ?? askPayloadFromParts(msg.parts) ?? askPayloadFromToolCall(msg);
      toolPayload = null;

      const isAnswered = capturedPayload
        ? isAskUserQuestionAnswered(messages, i)
        : false;
      if (capturedPayload && !isAnswered) {
        if (lastUnansweredAssistantId && lastUnansweredPayload) {
          stampPersistedQuestionCard(
            result, lastUnansweredAssistantId, lastUnansweredPayload,
          );
        }
        lastUnansweredAssistantId = msg._id;
        lastUnansweredPayload = capturedPayload;
        lastUnansweredAnswers = {};
      }

      const feedbackEntry = (msg.feedback as Array<{ isHelpful?: boolean }> | undefined)?.[0];
      const feedbackInfo = feedbackEntry?.isHelpful === true
        ? { value: 'like' as const }
        : feedbackEntry?.isHelpful === false
          ? { value: 'dislike' as const }
          : undefined;

      const answerText = extractFinalAnswer(msg.parts, msg.content);
      // A run stopped before any text arrived is saved as an empty stopped
      // reply. The live view drops that row (`buildStoppedMessages`); showing
      // it after a reload would add an empty "Stopped" bubble the user never saw.
      if (
        msg.status === 'stopped' &&
        !answerText.trim() &&
        !msg.parts?.length &&
        !capturedPayload &&
        !peekFollowingAskPayload(messages, i)
      ) {
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
          const prevParts = Array.isArray(prevCustom.persistedParts)
            ? (prevCustom.persistedParts as MessagePart[])
            : [];
          const nextParts = msg.parts?.length ? appendResumeParts(prevParts, msg.parts) : prevParts;
          Object.assign(last, {
            content: [{ type: 'text' as const, text: answerText }],
            metadata: {
              ...last.metadata,
              custom: {
                ...prevCustom,
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

    if (isAskUserQuestionResumeQuery(msg.content)) {
      const last = findQuestionAssistantRow(result, lastUnansweredAssistantId);
      if (last?.role === 'assistant' && typeof last.id === 'string') {
        mergeNextBotIntoId = last.id;
        const prevCustom = (last.metadata?.custom ?? {}) as Record<string, unknown>;
        const payload = (
          prevCustom.persistedAskUserQuestion ?? lastUnansweredPayload
        ) as AskUserQuestionPayload | undefined;
        const parsed = payload ? parseAnswerMessage(msg.content, payload) : {};
        const prevAnswers = (
          (prevCustom.persistedAskUserQuestionAnswers as Record<string, AskUserQuestionAnswer> | undefined)
          ?? lastUnansweredAnswers
        );
        const answers = { ...prevAnswers, ...parsed };
        const followUpComplete = hasFollowUpAfterResume(messages, i);
        Object.assign(last, {
          metadata: {
            ...last.metadata,
            custom: {
              ...prevCustom,
              ...(payload ? { persistedAskUserQuestion: payload } : {}),
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

    toolPayload = null;
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

/**
 * Build the ExternalStoreAdapter config for `useExternalStoreRuntime`.
 *
 * This function is called on every render of the chat page.
 * It reads the active slot's messages + streaming state and provides
 * `onNew` / `onCancel` callbacks routed to slot-scoped streaming.
 *
 * @param activeSlotId â€” current active slot key (or null for new chat screen)
 */
export function buildExternalStoreConfig(
  activeSlotId: string | null
): ExternalStoreAdapter<ThreadMessageLike> {
  const state = useChatStore.getState();
  const slot = activeSlotId ? state.slots[activeSlotId] : null;

  return {
    messages: slot?.messages ?? [],
    isRunning: slot?.isStreaming ?? false,

    // Required when T = ThreadMessageLike (identity â€” our messages are already ThreadMessageLike)
    convertMessage: (msg: ThreadMessageLike) => msg,

    onNew: async (message) => {
      // Read activeSlotId from store at invocation time â€” NOT from the
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

      // Fire-and-forget â€” streaming.ts handles all state updates.
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

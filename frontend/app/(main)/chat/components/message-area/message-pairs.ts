import type { AppliedFilters, AskUserQuestionAnswer, AskUserQuestionPayload, AttachmentRef, ChatCollectionAttachment, MessagePart } from '../../types';
import type { ConfidenceLevel, ModelInfo } from '../../types';
import type { CitationMaps } from './response-tabs/citations';
import type { ChatStreamErrorInfo } from '../../stream-error';
import { chatExtension } from '@/config';
import type { PairMessage, RowCustom } from '../../chat-extension';

export interface MessagePair {
  key: string;
  /** Backend _id of the bot_response message (used for regenerate) */
  messageId?: string;
  question: string;
  answer: string;
  citationMaps: CitationMaps;
  confidence?: ConfidenceLevel;
  /** Delegate agent whose output was sent as the answer (e.g. `coding_agent`). */
  answeredVia?: string;
  isStreaming: boolean;
  modelInfo?: ModelInfo;
  feedbackInfo?: { value?: 'like' | 'dislike' };
  /** Collections attached to this message (from user message metadata) */
  collections?: Array<{ id: string; name: string }>;
  appliedFilters?: AppliedFilters;
  /** ISO timestamp of when the user sent this query */
  createdAt?: string;
  /** Attachments uploaded with this user query (PDF / JPEG / PNG). */
  attachments?: AttachmentRef[];
  /** Persisted ask_user_question payload from a historical tool_call (read-only display) */
  persistedAskUserQuestion?: AskUserQuestionPayload;
  persistedAskUserQuestionAnswers?: Record<string, AskUserQuestionAnswer>;
  /** Persisted agent-activity transcript (absent for older / legacy-protocol messages) */
  persistedParts?: MessagePart[];
  /** Set when this response was cut short by a user-initiated Stop. */
  status?: 'stopped';
  /** The server's error code and details when this reply is a failed run (live turns only). */
  streamError?: ChatStreamErrorInfo;
  /** The run failed: `answer` holds the error text, not an answer. */
  failed?: boolean;
  /** No assistant row follows this question — render the question alone, no answer area. */
  unanswered?: boolean;
}

type MessageContent = readonly { type: string; text?: string }[];

type AssistantCustom = {
  messageId?: string;
  citationMaps?: CitationMaps;
  confidence?: ConfidenceLevel;
  answeredVia?: string;
  modelInfo?: ModelInfo;
  feedbackInfo?: { value?: 'like' | 'dislike' };
  persistedAskUserQuestion?: AskUserQuestionPayload;
  persistedAskUserQuestionAnswers?: Record<string, AskUserQuestionAnswer>;
  persistedParts?: MessagePart[];
  status?: 'stopped';
  streamError?: ChatStreamErrorInfo;
  failed?: boolean;
};

type UserCustom = {
  collections?: Array<{ id: string; name: string }>;
  appliedFilters?: AppliedFilters;
  createdAt?: string;
  attachments?: AttachmentRef[];
};

export interface BuildMessagePairsOptions {
  isStreaming: boolean;
  streamingQuestion: string;
  pendingCollections: Array<{ id: string; name: string }>;
  regenerateMessageId: string | null;
  /** Stable empty value, so unchanged rows keep their identity across renders. */
  emptyCitationMaps: CitationMaps;
}

/**
 * Extract text content from assistant-ui message content array
 */
export function extractTextContent(content: MessageContent): string {
  return content
    .filter((part) => part.type === 'text' && part.text)
    .map((part) => part.text)
    .join('');
}

const isStandalone = (msg: PairMessage | null | undefined): boolean =>
  msg != null && chatExtension.pairs.isStandalone(msg);

/** The nearest row before/after `index` that is not standalone: such a row between a question and its answer does not break the pair. */
function neighbour(messages: readonly PairMessage[], index: number, step: 1 | -1): PairMessage | null {
  for (let j = index + step; j >= 0 && j < messages.length; j += step) {
    if (!isStandalone(messages[j])) return messages[j];
  }
  return null;
}

/** Build message pairs (user question + assistant answer) in chronological order. */
export function buildMessagePairs(
  messages: readonly PairMessage[],
  options: BuildMessagePairsOptions
): MessagePair[] {
  const {
    isStreaming,
    streamingQuestion,
    pendingCollections,
    regenerateMessageId,
    emptyCitationMaps,
  } = options;
  const pairs: MessagePair[] = [];
  // Only the *last* assistant in the thread can be the live SSE target for a
  // new send. (Never use `!content` alone: agent threads can retain empty
  // `content` on older rows after a bad load or edge case, which would paint
  // the current stream + citations onto every such row.)
  let lastAssistantIndex = -1;
  for (let j = 0; j < messages.length; j += 1) {
    if (messages[j].role === 'assistant') {
      lastAssistantIndex = j;
    }
  }

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (isStandalone(msg)) {
      const pair = chatExtension.pairs.standalonePair(msg, i, { emptyCitationMaps });
      if (pair) pairs.push(pair);
      continue;
    }
    if (msg.role === 'user' && neighbour(messages, i, 1)?.role !== 'assistant') {
      // A question with no assistant message after it. A row is otherwise
      // only emitted per assistant message, so this question would not be
      // drawn at all: Stop before the first token drops the empty assistant
      // placeholder (`buildStoppedMessages`), and a reload drops the empty
      // stopped reply the backend saved (`loadHistoricalMessages`). The
      // question is real conversation state -- it is stored, and later turns
      // are answered in its context -- so it stays, in the place it was
      // asked rather than at the end.
      const userCustom = msg.metadata?.custom as UserCustom | undefined;
      pairs.push(chatExtension.pairs.decorate({
        key: msg.id ?? `user-${i}`,
        question: extractTextContent(msg.content as MessageContent),
        answer: '',
        citationMaps: emptyCitationMaps,
        isStreaming: false,
        collections: userCustom?.collections,
        appliedFilters: userCustom?.appliedFilters,
        createdAt: userCustom?.createdAt,
        attachments: userCustom?.attachments,
        unanswered: true,
      }, { user: userCustom as RowCustom | undefined, standaloneBefore: 0 }));
      continue;
    }
    if (msg.role === 'assistant') {
      const content = extractTextContent(msg.content as MessageContent);

      const metadata = msg.metadata?.custom as AssistantCustom | undefined;

      // Find preceding user message
      const prevMsg = neighbour(messages, i, -1);
      const question = prevMsg?.role === 'user'
        ? extractTextContent(prevMsg.content as MessageContent)
        : 'Question';

      let standaloneBefore = 0;
      for (let j = i - 1; j >= 0 && isStandalone(messages[j]); j -= 1) standaloneBefore += 1;
      if (prevMsg?.role !== 'user') standaloneBefore = 0;

      // Check if this message is being regenerated
      const isBeingRegenerated = Boolean(
        regenerateMessageId &&
        (metadata?.messageId === regenerateMessageId || msg.id === regenerateMessageId),
      );

      // New sends attach to the last assistant. Ask-user resume can target an
      // older card row when a later turn (e.g. "hiii") is now last — then the
      // last pair's question is not `streamingQuestion`.
      const isLastAssistant = i === lastAssistantIndex;
      const beforeLast = lastAssistantIndex > 0 ? neighbour(messages, lastAssistantIndex, -1) : null;
      const lastQuestion = beforeLast?.role === 'user'
        ? extractTextContent(beforeLast.content as MessageContent)
        : '';
      const isCurrentlyStreaming =
        isStreaming &&
        question === streamingQuestion &&
        (isLastAssistant || lastQuestion !== streamingQuestion);

      // appliedFilters from the preceding user message metadata
      const userMsgCustom = prevMsg?.metadata?.custom as UserCustom | undefined;

      pairs.push(chatExtension.pairs.decorate({
        key: msg.id ?? `asst-${i}`,
        messageId: metadata?.messageId,
        question,
        answer: isBeingRegenerated ? '' : content,
        citationMaps: (isCurrentlyStreaming || isBeingRegenerated)
          ? emptyCitationMaps
          : (metadata?.citationMaps || emptyCitationMaps),
        confidence: metadata?.confidence,
        ...(metadata?.answeredVia ? { answeredVia: metadata.answeredVia } : {}),
        isStreaming: isCurrentlyStreaming || isBeingRegenerated,
        modelInfo: metadata?.modelInfo,
        feedbackInfo: metadata?.feedbackInfo,
        collections: isCurrentlyStreaming
          ? (pendingCollections.length > 0 ? pendingCollections : userMsgCustom?.collections)
          : userMsgCustom?.collections,
        appliedFilters: userMsgCustom?.appliedFilters,
        createdAt: userMsgCustom?.createdAt,
        attachments: userMsgCustom?.attachments,
        persistedAskUserQuestion: metadata?.persistedAskUserQuestion,
        persistedAskUserQuestionAnswers: metadata?.persistedAskUserQuestionAnswers,
        persistedParts: metadata?.persistedParts,
        status: metadata?.status,
        streamError: metadata?.streamError,
        ...(metadata?.failed ? { failed: true } : {}),
      }, {
        user: userMsgCustom as RowCustom | undefined,
        assistant: metadata as RowCustom | undefined,
        standaloneBefore,
      }));
    }
  }

  return pairs;
}

/** Rows equal field by field to the previous build keep their old object, so memoised rows skip re-rendering. */
export function keepUnchangedPairs(
  previous: ReadonlyMap<string, MessagePair>,
  next: MessagePair[]
): MessagePair[] {
  return next.map((pair) => {
    const old = previous.get(pair.key);
    if (!old) return pair;
    const keys = Object.keys(pair) as (keyof MessagePair)[];
    const same =
      keys.length === Object.keys(old).length && keys.every((k) => Object.is(pair[k], old[k]));
    return same ? old : pair;
  });
}

export type RetryTurn = Pick<MessagePair, 'question' | 'attachments' | 'collections' | 'appliedFilters'>;

/** The user message that asks a failed turn again, with its attachments and knowledge scope. */
export function retryTurnMessage(turn: RetryTurn) {
  // Reloaded turns carry only `appliedFilters`; the send path reads `collections`.
  const collections: ChatCollectionAttachment[] = turn.collections?.length
    ? turn.collections
    : [
        ...(turn.appliedFilters?.apps ?? []).map(({ id, name }) => ({ id, name, kind: 'collectionRoot' as const })),
        ...(turn.appliedFilters?.kb ?? []).map(({ id, name }) => ({ id, name, kind: 'recordGroup' as const })),
      ];
  return {
    role: 'user' as const,
    content: [{ type: 'text' as const, text: turn.question }],
    metadata: {
      custom: {
        collections: collections.length > 0 ? collections : undefined,
        attachments: turn.attachments?.length ? turn.attachments : undefined,
      },
    },
    startRun: true,
  };
}

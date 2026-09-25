import type { AppliedFilters, AskUserQuestionPayload, AttachmentRef, MessagePart } from '../../types';
import type { ConfidenceLevel, ModelInfo } from '../../types';
import type { CitationMaps } from './response-tabs/citations';

export interface MessagePair {
  key: string;
  /** Backend _id of the bot_response message (used for regenerate) */
  messageId?: string;
  question: string;
  answer: string;
  citationMaps: CitationMaps;
  confidence?: ConfidenceLevel;
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
  /** Persisted agent-activity transcript (absent for older / legacy-protocol messages) */
  persistedParts?: MessagePart[];
  /** Set when this response was cut short by a user-initiated Stop. */
  status?: 'stopped';
  /** No assistant row follows this question — render the question alone, no answer area. */
  unanswered?: boolean;
}

type MessageContent = readonly { type: string; text?: string }[];

interface PairMessage {
  readonly id?: string;
  readonly role: string;
  readonly content: unknown;
  readonly metadata?: { custom?: unknown };
}

type AssistantCustom = {
  messageId?: string;
  citationMaps?: CitationMaps;
  confidence?: ConfidenceLevel;
  modelInfo?: ModelInfo;
  feedbackInfo?: { value?: 'like' | 'dislike' };
  persistedAskUserQuestion?: AskUserQuestionPayload;
  persistedParts?: MessagePart[];
  status?: 'stopped';
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
    if (msg.role === 'user' && messages[i + 1]?.role !== 'assistant') {
      // A question with no assistant message after it. A row is otherwise
      // only emitted per assistant message, so this question would not be
      // drawn at all: Stop before the first token drops the empty assistant
      // placeholder (`buildStoppedMessages`), and a reload drops the empty
      // stopped reply the backend saved (`loadHistoricalMessages`). The
      // question is real conversation state -- it is stored, and later turns
      // are answered in its context -- so it stays, in the place it was
      // asked rather than at the end.
      const userCustom = msg.metadata?.custom as UserCustom | undefined;
      pairs.push({
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
      });
      continue;
    }
    if (msg.role === 'assistant') {
      const content = extractTextContent(msg.content as MessageContent);

      const metadata = msg.metadata?.custom as AssistantCustom | undefined;

      // Find preceding user message
      const prevMsg = i > 0 ? messages[i - 1] : null;
      const question = prevMsg?.role === 'user'
        ? extractTextContent(prevMsg.content as MessageContent)
        : 'Question';

      // Check if this message is being regenerated
      const isBeingRegenerated = !!regenerateMessageId && metadata?.messageId === regenerateMessageId;

      // Live stream attaches only to the last assistant in the thread when
      // its user message matches the query we sent (see placeholder assistant
      // in `streamMessageForSlot`).
      const isLastAssistant = i === lastAssistantIndex;
      const isCurrentlyStreaming =
        isStreaming && isLastAssistant && question === streamingQuestion;

      // appliedFilters from the preceding user message metadata
      const userMsgCustom = prevMsg?.metadata?.custom as UserCustom | undefined;

      pairs.push({
        key: msg.id ?? `asst-${i}`,
        messageId: metadata?.messageId,
        question,
        answer: isBeingRegenerated ? '' : content,
        citationMaps: (isCurrentlyStreaming || isBeingRegenerated)
          ? emptyCitationMaps
          : (metadata?.citationMaps || emptyCitationMaps),
        confidence: metadata?.confidence,
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
        persistedParts: metadata?.persistedParts,
        status: metadata?.status,
      });
    }
  }

  return pairs;
}

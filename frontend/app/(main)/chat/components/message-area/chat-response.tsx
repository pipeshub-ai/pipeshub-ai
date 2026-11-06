'use client';

import React, { useState, useMemo, useRef, useCallback, useEffect, useLayoutEffect, useContext } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { SelectedCollections } from '../selected-collections';
import { AppliedFilters } from '../applied-filters';
import { ResponseTabs } from './response-tabs';
import { ConfidenceIndicator } from './confidence-indicator';
import { SearchDegradedNote } from './search-degraded-note';
import { AnswerContent } from './answer-content';
import { StatusMessageComponent } from './status-message';
import { MessageActions } from './message-actions';
import { SourcesTab } from './response-tabs/citations/sources-tab';
import { CitationsTab } from './response-tabs/citations/citations-tab';
import { ArtifactsPanel } from './artifacts-panel';
import { useArtifactActions } from './use-artifact-actions';
import {
  AskUserQuestionCard,
  askUserQuestionOwnsRow,
  showsStoppedMarker,
  buildResumeMessage,
  isAskUserQuestionExpired,
  normalizeAskUserQuestionPayload,
} from './ask-user-question-card';
import { ConnectToolsCard } from './connect-tools-card';
import { UnavailableToolsNotice } from './unavailable-tools-notice';
import { McpSignInCard } from './mcp-sign-in-card';
import { ToolApprovalCard, ToolApprovalReceipts } from './tool-approval-card';
import {
  answeredToolApproval,
  findToolApproval,
  hasApprovedCall,
  settledToolApprovals,
  withoutWaitingLine,
} from '../../tool-approval';
import { mcpSignInServers } from '../../mcp-sign-in';
import { cannotRun, toolsBlockedDetails, type ChatStreamErrorInfo } from '../../stream-error';
import type { UnavailableTool } from '../../unavailable-tools';
import { AgentActivityTimeline, CollapsibleActivitySection, getVisibleRootParts, hasMultiStepActivity } from './agent-activity';
import { TRAIL_FOLD_EVENT } from './keep-on-screen';
import { ExpandableUserQuery } from './expandable-user-query';
import { SentPasteChip } from './sent-paste-chip';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { AnswerFailed } from './answer-failed';
import { RunErrorCard } from './run-error-card';
import { chatSlots } from '@/config';
import type { ChatResponseLayout, ChatResponseParts } from '../../chat-extension';
import { withoutFailedTurn, type MessagePair, type RetryTurn } from './message-pairs';
import { useCommandStore } from '@/lib/store/command-store';
import { useChatStore } from '../../store';
import { AnswerSlotContext, answerSlotIdNow, useAnswerSlotId } from './answer-slot-scope';
import { debugLog } from '../../debug-logger';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { AnswerPanelScope } from '../answer-panel/answer-panel-store';
import { SourcesButton, sourceConnectors } from '../answer-panel/sources-button';
import type { AskUserQuestionAnswer, AskUserQuestionPayload, AttachmentRef, ConfidenceLevel, ModelInfo, StatusMessage, ResponseTab, ChatArtifact, AppliedFilters as AppliedFiltersData, MessagePart } from '../../types';
import { FileIcon } from '@/app/components/ui/file-icon';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { emptyCitationMaps } from './response-tabs/citations';
import { repairStreamingMarkdown } from '../../utils/repair-streaming-markdown';
import { mayBeNarration } from '../../utils/narration-hold';
import { processMarkdownContent } from '../../utils/process-markdown-content';
import { extractAnswerMarkers } from '../../utils/parse-download-markers';
import { isAskUserQuestionTool, shownQuestionOf } from '../../utils/tool-display';
import { hasAnyFilter } from '../../utils/tree-selection';
import { mentionedNodes } from '../../utils/node-mentions';
import { DownloadTasks } from './download-tasks';
import {
  isPresentationFile,
  isDocxFile,
  isLegacyWordDocFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
import { TextPreviewDialog } from '../text-preview-dialog';
import { isPastedTextAttachment } from '../../utils/paste-attachment';
import { useTranslation } from 'react-i18next';
import { CitationMessageRowKeyContext } from './response-tabs/citations/citation-popover-control';
import { useInlineCitationPopoverStore } from './response-tabs/citations/citation-popover-store';
import { useNarrationGlide } from './narration-glide';

/** The question's time: the clock today, the date and the clock before today. */
function formatQuestionTime(isoString: string): string {
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return '';
  const time = { hour: 'numeric', minute: '2-digit' } as const;
  return date.toDateString() === new Date().toDateString()
    ? date.toLocaleTimeString(undefined, time)
    : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...time });
}

// Stable empty reference — avoids creating new objects in default params
const EMPTY_CITATION_MAPS: CitationMaps = emptyCitationMaps();

// The card every chat draws the run's work in. No bottom padding: the activity inside ends with its own bottom
// margin. A step of light above the page, as wide as the answer column; it sets the work apart, so no rule follows it.
const ACTIVITY_CARD_STYLE: React.CSSProperties = {
  border: '1px solid var(--olive-5)',
  background: 'var(--olive-2)',
  borderRadius: 'var(--radius-3)',
  padding: 'var(--space-3) var(--space-3) 0',
  marginBottom: 'var(--space-5)',
};

function buildQuestionCardReadAloudText(payload: AskUserQuestionPayload): string {
  const parts: string[] = [];
  if (payload.userIntent) parts.push(payload.userIntent);
  payload.questions.forEach((q, i) => {
    parts.push(`Question ${i + 1}: ${q.question}`);
    q.options.forEach((opt) => parts.push(`Option: ${opt.label}`));
  });
  return parts.join('. ');
}

interface FeedbackInfo {
  value?: 'like' | 'dislike';
}

export interface ChatResponseProps {
  question: string;
  answer: string;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
  confidence?: ConfidenceLevel;
  /** A search behind this answer was served with less than the full search. */
  searchDegraded?: boolean;
  isStreaming?: boolean;
  modelInfo?: ModelInfo;
  /** Collections attached to this message (e.g. KB filters the user selected) */
  collections?: Array<{ id: string; name: string }>;
  appliedFilters?: AppliedFiltersData;
  /** Backend _id of the bot_response message (used for regenerate) */
  messageId?: string;
  /** Whether this is the last bot message in the conversation */
  isLastMessage?: boolean;
  /** This reply's row in the thread (`messagePairs[].key`): an approval card's answer carries it on. */
  rowId?: string;
  /**
   * The row's identity on screen, which stays the same when the streamed reply is swapped for the saved one.
   * Not read while the answer shows the step timeline (it has no clock to key).
   */
  renderKey?: string;
  /** The page joined this run part-way (reloaded mid-answer). Not read while the answer shows the step timeline. */
  resumed?: boolean;
  /** Streaming content — only passed for the currently-streaming message */
  streamingContent?: string;
  /** Current status message — only passed for the currently-streaming message */
  currentStatusMessage?: StatusMessage | null;
  /** Streaming citation maps — only passed for the currently-streaming message */
  streamingCitationMaps?: CitationMaps | null;
  /** Artifacts generated during streaming (coding sandbox, etc.) */
  streamingArtifacts?: ChatArtifact[];
  /** recordId -> highest version seen anywhere in this conversation (see `MessageList`) — powers the "newer version available" hint on older cards. */
  latestArtifactVersions?: ReadonlyMap<string, number>;
  /** Live agent-activity transcript — only passed for the currently-streaming message. */
  streamingParts?: MessagePart[];
  /** Persisted agent-activity transcript from `ConversationMessage.parts` (absent for older messages). */
  persistedParts?: MessagePart[];
  /**
   * Thread row key (`messagePairs[].key`) for list-scoped inline-citation
   * popover store (see `citationMessageRowKey`). Omit in read-only views (e.g. archived) so badges stay uncontrolled.
   */
  citationMessageRowKey?: string;
  /** ISO timestamp of when the user sent this query */
  createdAt?: string;
  /** Attachments uploaded with this user query (PDF / JPEG / PNG). */
  attachments?: AttachmentRef[];
  /** Persisted ask_user_question payload from a historical tool_call — renders read-only question card */
  persistedAskUserQuestion?: AskUserQuestionPayload;
  persistedAskUserQuestionAnswers?: Record<string, AskUserQuestionAnswer>;
  /** Persisted feedback value from the backend — initialises the like/dislike button state */
  feedbackInfo?: { value?: 'like' | 'dislike' };
  /** Set when this response was cut short by a user-initiated Stop (see `IMessage.status`, Node). */
  status?: 'stopped';
  /** The server's error code and details when this reply is a failed run. */
  streamError?: ChatStreamErrorInfo;
  /** Tools the person asking had not connected; the answer went on without them. */
  unavailableTools?: UnavailableTool[];
  /** The run failed: `answer` is the error text. */
  failed?: boolean;
  /** Resend the turn; offered on a failed last answer to someone who can send in this chat. */
  onRetry?: (turn: RetryTurn) => void;
  /** The viewer can send in this chat; regenerate is offered only then. */
  canSend?: boolean;
  /**
   * No assistant row exists for this question (see `buildMessagePairs`). The
   * question is drawn on its own: an answer area, tabs and message actions
   * would all be empty controls over a reply that was never produced.
   */
  unanswered?: boolean;
  /** The pair this row draws, for row extensions. */
  pair?: MessagePair;
  /** Row layout from the list body. */
  layout?: ChatResponseLayout;
}

export const ChatResponse = React.memo(function ChatResponse({
  question,
  answer,
  citationMaps = EMPTY_CITATION_MAPS,
  citationCallbacks,
  confidence,
  searchDegraded = false,
  isStreaming = false,
  modelInfo,
  collections,
  appliedFilters,
  attachments,
  messageId,
  isLastMessage = false,
  rowId,
  streamingContent = '',
  currentStatusMessage: currentStatusMessageProp = null,
  streamingCitationMaps = null,
  streamingArtifacts,
  latestArtifactVersions,
  streamingParts,
  persistedParts,
  citationMessageRowKey,
  createdAt,
  persistedAskUserQuestion,
  persistedAskUserQuestionAnswers,
  feedbackInfo,
  status,
  streamError,
  unavailableTools,
  failed = false,
  onRetry,
  canSend = true,
  unanswered = false,
  pair,
  layout,
}: ChatResponseProps) {
  debugLog.tick('[chat] [ChatResponse]');
  const { t } = useTranslation();
  const blockedTools = isStreaming ? null : toolsBlockedDetails(streamError);
  const isMobile = useIsMobile();
  const ext = chatSlots.useResponseExtension({ pair, layout, question, isStreaming, isLastMessage, unanswered });
  // From 1024px a chat's own list has the column beside it, which takes the answer's tabs whether it is open or shut.
  const panelScope = useContext(AnswerPanelScope);
  const panelTakesTabs = panelScope?.column === true;
  // From 1024px the tabs are not drawn under the question (the right column, or a room's own panel, holds them), so the
  // answer starts 16px under the question, with no line between them. Below that the inline tabs follow the question.
  const wideRow = !unanswered && (panelTakesTabs || ext.headline?.compact === false);
  // Text that streamed as the answer and moves into the trail glides there (`useNarrationGlide`).
  const shellRef = useRef<HTMLDivElement>(null);
  useNarrationGlide(shellRef, isStreaming);

  /** Shown only if the stream is active but no SSE status has arrived yet */
  const streamingFallbackStatus = useMemo(
    (): StatusMessage => ({
      id: 'status-waiting',
      status: 'processing',
      message: t('chatStream.thinkingFallback'),
      timestamp: '',
    }),
    [t],
  );

  // ── Render-reason tracking ─────────────────────────────────────────
  const prevCRRef = useRef<Record<string, unknown>>({});
  const currentCRVals: Record<string, unknown> = {
    question, answer, citationMaps, citationCallbacks, confidence,
    isStreaming, modelInfo, collections, appliedFilters, messageId,
    isLastMessage, streamingContent, currentStatusMessage: currentStatusMessageProp,
    streamingCitationMaps, streamingParts, persistedParts, createdAt, persistedAskUserQuestion, persistedAskUserQuestionAnswers, status,
  };
  const crReasons: string[] = [];
  for (const [k, v] of Object.entries(currentCRVals)) {
    // eslint-disable-next-line react-hooks/refs -- intentional: debug render-reason tracking
    if (!Object.is(v, prevCRRef.current[k])) crReasons.push(k);
  }
  if (crReasons.length > 0) {
    debugLog.reason('[chat] [ChatResponse]', crReasons);
  }
  // eslint-disable-next-line react-hooks/refs -- intentional: update previous-props snapshot for next render diff
  prevCRRef.current = currentCRVals;

  // ── Local tab state with mutual exclusivity ────────────────────────
  // Each ChatResponse manages its own tab locally. 'answer' is the default.
  // To enforce that only one message can show sources/citations at a time,
  // we track the active expanded message ID in the slot store.
  const [localTab, setLocalTab] = useState<ResponseTab>('answer');

  const scopedSlotId = useContext(AnswerSlotContext);
  const answerSlotId = useAnswerSlotId();
  // Read the slot-level activeExpandedMessageId so we can reset to 'answer'
  // when a different message becomes the expanded one.
  const activeExpandedMessageId = useChatStore((s) =>
    answerSlotId ? s.slots[answerSlotId]?.activeExpandedMessageId ?? null : null
  );
  const updateSlot = useChatStore((s) => s.updateSlot);
  const setPreviewFile = useChatStore((s) => s.setPreviewFile);
  const setPreviewMode = useChatStore((s) => s.setPreviewMode);

  /** Attachment currently shown in the lightweight text preview dialog (pasted-text attachments only). */
  const [textPreviewAttachment, setTextPreviewAttachment] = useState<AttachmentRef | null>(null);

  const handleAttachmentPreview = useCallback(
    async (att: AttachmentRef) => {
      setPreviewFile({
        id: att.recordId,
        name: att.recordName,
        url: '',
        type: att.mimeType,
        isLoading: true,
        hideFileDetails: true,
        showDownload: true,
      });
      setPreviewMode('sidebar');

      try {
        const streamAsPdf =
          isPresentationFile(att.mimeType, att.recordName) ||
          isLegacyWordDocFile(att.mimeType, att.recordName);
        const streamOptions = streamAsPdf ? { convertTo: 'application/pdf' } : undefined;
        const blob = await KnowledgeBaseApi.streamRecord(att.recordId, streamOptions);
        const resolvedType = resolvePreviewMimeAfterStream(
          att.mimeType,
          att.recordName,
          blob,
          !!streamOptions,
        );
        const isDocx = isDocxFile(att.mimeType, att.recordName, att.recordName, att.extension, att.extension);
        const url = isDocx ? '' : URL.createObjectURL(blob);
        setPreviewFile({
          id: att.recordId,
          name: att.recordName,
          url,
          blob: isDocx ? blob : undefined,
          type: resolvedType,
          isLoading: false,
          previewRenderable: true,
          hideFileDetails: true,
          showDownload: true,
        });
      } catch (error) {
        setPreviewFile({
          id: att.recordId,
          name: att.recordName,
          url: '',
          type: att.mimeType,
          error: error instanceof Error ? error.message : 'Failed to load file',
          isLoading: false,
          hideFileDetails: true,
          showDownload: true,
        });
      }
    },
    [setPreviewFile, setPreviewMode],
  );

  const artifactActions = useArtifactActions(latestArtifactVersions);

  const pendingAskUserQuestion = useChatStore((s) =>
    answerSlotId ? s.slots[answerSlotId]?.pendingAskUserQuestion ?? null : null
  );
  // A card typed past in the chat: its box folds when the reply's run ends, so this row follows the chat's runs.
  const repliedInChat = Boolean(persistedAskUserQuestion?.repliedInChat);
  const chatRunning = useChatStore((s) => repliedInChat && Boolean(answerSlotId && s.slots[answerSlotId]?.isStreaming));

  const askQuestionMatchesRow = askUserQuestionOwnsRow(
    pendingAskUserQuestion,
    citationMessageRowKey,
    messageId,
  );
  const questionPending =
    askQuestionMatchesRow && pendingAskUserQuestion?.status === 'pending';

  // If another message was expanded (or expansion was cleared), reset to 'answer'.
  // We only react when our localTab is non-answer — avoids unnecessary effects.
  const prevExpandedRef = useRef(activeExpandedMessageId);
  useEffect(() => {
    if (
      localTab !== 'answer' &&
      activeExpandedMessageId !== messageId &&
      activeExpandedMessageId !== prevExpandedRef.current
    ) {
      setLocalTab('answer');
    }
    prevExpandedRef.current = activeExpandedMessageId;
  }, [activeExpandedMessageId, localTab, messageId]);

  // Also ensure we reset if the slot switches to a different conversation
  // (activeExpandedMessageId becomes null on slot evict/init).
  const activeTab = panelTakesTabs
    ? 'answer'
    : (activeExpandedMessageId === messageId || !messageId)
      ? localTab
      : 'answer';

  const setActiveTab = useCallback((tab: ResponseTab) => {
    setLocalTab(tab);
    if (!answerSlotId) return;
    if (tab === 'answer') {
      // Clear expansion only if we own it
      updateSlot(answerSlotId, { activeExpandedMessageId: null });
    } else {
      updateSlot(answerSlotId, { activeExpandedMessageId: messageId ?? null });
    }
  }, [answerSlotId, messageId, updateSlot]);

  const setInlineCitationKey = useInlineCitationPopoverStore((s) => s.setActiveKey);
  const prevLocalTabForCite = useRef<ResponseTab>('answer');
  useEffect(() => {
    if (prevLocalTabForCite.current === 'answer' && localTab !== 'answer') {
      setInlineCitationKey(null);
    }
    prevLocalTabForCite.current = localTab;
  }, [localTab, setInlineCitationKey]);

  // Merge streaming citations when streaming, fall back to metadata citations
  const effectiveCitationMaps = isStreaming && streamingCitationMaps
    ? streamingCitationMaps
    : citationMaps;
  // Read via ref (not a `useMemo` dep) by `wrappedCallbacks` below, so that
  // object stays referentially stable across citation-only updates.
  const effectiveCitationMapsRef = useRef(effectiveCitationMaps);
  effectiveCitationMapsRef.current = effectiveCitationMaps;

  // A row that carries on after its question card holds the text an earlier run saved on it, which is not this
  // run's answer: while it runs, only what this run streams is shown.
  const resumesCardRow = isStreaming && Boolean(persistedAskUserQuestion || askQuestionMatchesRow);
  // Text the run is still writing stays out of the answer while it may be the sentence said before a tool call
  // (`mayBeNarration`): when the call comes, that sentence is first drawn in the run's box, not here and then moved.
  const liveAnswer = useMemo(
    () => (isStreaming && mayBeNarration(streamingContent) ? '' : streamingContent),
    [isStreaming, streamingContent],
  );
  // Use streaming content when streaming, otherwise use the final answer.
  // Apply structural repair to in-progress content only — the final message
  // from the server is always complete and must not be patched.
  // Always strip backend citation links → `[N]` so `AnswerContent` can render chips.
  // Memoized: these are whole-string regex passes, and `ChatResponse` can
  // re-render for reasons unrelated to content (tab switch, citation
  // arrival) — recomputing on every such render defeats the point of
  // memoizing further down the tree in `AnswerContent`.
  const processedContent = useMemo(
    () =>
      processMarkdownContent(
        isStreaming && liveAnswer
          ? repairStreamingMarkdown(liveAnswer)
          : resumesCardRow
            ? ''
            : answer,
      ),
    [isStreaming, liveAnswer, resumesCardRow, answer],
  );
  // Markers are always stripped from the markdown; they become cards only in
  // a saved answer (see `MarkerSource`). While streaming, live cards come
  // solely from the backend's typed artifact events (`streamingArtifacts`),
  // never from the model's own text.
  const {
    text: displayContent,
    artifacts: persistedArtifacts,
    downloadTasks,
  } = useMemo(
    () => extractAnswerMarkers(processedContent, isStreaming ? 'streamed' : 'persisted'),
    [processedContent, isStreaming],
  );
  const EMPTY_ANSWER_FALLBACK =
    "I wasn't able to generate a response. Please try rephrasing.";
  const hasAskToolPart = (isStreaming ? streamingParts : persistedParts)?.some(
    (part) => part.type === 'tool_call' && isAskUserQuestionTool(part.toolName),
  );
  const hasQuestionCard = Boolean(
    persistedAskUserQuestion || askQuestionMatchesRow || hasAskToolPart,
  );
  const visibleAnswer =
    hasQuestionCard && displayContent.trim() === EMPTY_ANSWER_FALLBACK
      ? ''
      : displayContent;
  const effectiveArtifacts: ChatArtifact[] = isStreaming
    ? streamingArtifacts ?? []
    : persistedArtifacts;
  const currentStatusMessage = currentStatusMessageProp;
  const streamingStatusToShow =
    currentStatusMessage ?? (isStreaming && !displayContent ? streamingFallbackStatus : null);

  // Live transcript while streaming, persisted transcript after reload —
  // same components render either (see AgentActivityTimeline's docstring).
  // Falls back to nothing for messages saved before this feature shipped.
  const effectiveParts = isStreaming ? streamingParts : persistedParts;

  // A multi-step (ReAct) response has at least one tool call, reasoning
  // block, or sub-agent delegation. The trailing (unsettled) text streams
  // straight into `AnswerContent` for both simple and multi-step responses
  // — see `filterRootParts` in `agent-activity.tsx`. This keeps the common
  // case (trailing text IS the final answer) seamless: it grows in the
  // answer area as it streams and there's no jump when `RUN_FINISHED`
  // lands. If it later turns out to be narration (a tool call/reasoning
  // block follows), it's settled into the timeline and cleared from the
  // answer buffer in the same update, so it never renders in both places.
  const multiStep = useMemo(() => hasMultiStepActivity(effectiveParts), [effectiveParts]);
  // A turn that ended waiting for approval; shown once the reply is settled.
  const toolApproval = useMemo(
    () => (isStreaming ? null : findToolApproval(effectiveParts)),
    [effectiveParts, isStreaming],
  );
  const approvalReceipts = useMemo(() => settledToolApprovals(effectiveParts), [effectiveParts]);
  // "Waiting for your approval…" closes a reply that stopped on a card. The card says it while it
  // waits; once answered, the reply carries on in this row, and until its new text streams in the
  // row still holds those words. They are never the answer.
  const hasApprovalCard = useMemo(
    () => findToolApproval(effectiveParts) !== null || approvalReceipts.length > 0,
    [effectiveParts, approvalReceipts],
  );
  const answerShown = hasApprovalCard ? withoutWaitingLine(visibleAnswer) : visibleAnswer;
  // Regenerating could run an approved call again, or answer a card twice: the server refuses both.
  const ranApprovedCall = useMemo(
    () => hasApprovedCall(effectiveParts) || answeredToolApproval(effectiveParts),
    [effectiveParts],
  );
  // While the composer's Regenerate bar is open for the latest reply to a tapped answer, its card can
  // be answered again: the bar re-runs the reply with the answers it has, or with the ones picked here.
  // A typed reply, an expired card and a reply that ran an approved call regenerate as usual (the
  // server refuses changed answers for them).
  const regenTargetMessageId = useChatStore((s) => s.regenTargetMessageId);
  const regenAnswers = useChatStore((s) => s.regenAnswers);
  // Right after an answer the card is the live one (read-only once sent); after a reload it is the saved one.
  const liveAnsweredCard =
    askQuestionMatchesRow && pendingAskUserQuestion && pendingAskUserQuestion.status !== 'pending'
      ? pendingAskUserQuestion
      : null;
  const changeablePayload = liveAnsweredCard?.payload ?? persistedAskUserQuestion;
  const changeableAnswers = liveAnsweredCard?.answers ?? persistedAskUserQuestionAnswers ?? {};
  const canChangeAnswers = Boolean(
    isLastMessage &&
      !isStreaming &&
      messageId &&
      answerSlotId &&
      ext.allowRegenerate &&
      canSend &&
      changeablePayload &&
      !changeablePayload.repliedInChat &&
      !isAskUserQuestionExpired(changeablePayload) &&
      !ranApprovedCall &&
      Object.keys(changeableAnswers).length > 0 &&
      (liveAnsweredCard || (persistedAskUserQuestion && !askQuestionMatchesRow)),
  );
  const changingAnswers = canChangeAnswers && regenTargetMessageId === messageId;
  const pickAnswers = (answers: Record<string, AskUserQuestionAnswer>) => {
    if (!messageId || !changeablePayload) return;
    useChatStore.getState().setRegenAnswers({
      messageId,
      payload: changeablePayload,
      message: buildResumeMessage(changeablePayload, answers),
      answers,
    });
  };
  const changeAnswersEditor = changeablePayload ? (
    <AskUserQuestionCard
      // Its own mount, so it opens at the first question rather than where the read-only card was.
      key="change-answers"
      payload={changeablePayload}
      initialAnswers={changeableAnswers}
      status="pending"
      initialStep={0}
      onAnswersChange={pickAnswers}
      onSubmit={(_message, answers) => {
        pickAnswers(answers);
        useCommandStore.getState().dispatch('runRegenerate');
      }}
    />
  ) : null;
  // During the re-run the card shows the answers it is running with, until the saved reply replaces it.
  const rerunAnswers = isStreaming && regenAnswers?.messageId === messageId ? regenAnswers : null;
  useEffect(() => {
    if (isStreaming || !messageId || useChatStore.getState().regenAnswers?.messageId !== messageId) return;
    if (useChatStore.getState().regenTargetMessageId === messageId) return;
    useChatStore.getState().setRegenAnswers(null);
  }, [isStreaming, messageId]);
  const rerunCard = rerunAnswers ? (
    <AskUserQuestionCard
      key="rerun"
      payload={rerunAnswers.payload}
      initialAnswers={rerunAnswers.answers}
      status="persisted"
    />
  ) : null;
  // Servers that refused for missing permission: signed in to again from the latest, settled reply.
  const signInServers = useMemo(
    () => (isStreaming || !isLastMessage ? [] : mcpSignInServers(effectiveParts)),
    [effectiveParts, isStreaming, isLastMessage],
  );
  // Drives both the activity card and the collapsible-summary wrapper
  // around a completed activity timeline — computed with the exact same
  // filtering the timeline itself applies, so it never disagrees with what
  // actually renders (e.g. a transcript containing only the isFinal part
  // and nothing else shows no card).
  const visibleActivityParts = useMemo(
    () => (effectiveParts ? getVisibleRootParts(effectiveParts, isStreaming) : []),
    [effectiveParts, isStreaming],
  );
  // A question waiting on its person names them in the summary row, so the box never reads as done.
  const waitingOnQuestion =
    questionPending && pendingAskUserQuestion && !isAskUserQuestionExpired(pendingAskUserQuestion.payload)
      ? ext.askCardReadOnlyFor
        ? t('askUserQuestion.waitingFor', { name: ext.askCardReadOnlyFor.name || t('askUserQuestion.unknownPerson') })
        : t('askUserQuestion.waitingForYou')
      : undefined;
  // Collapse control is offered once narration/answer has started (or for
  // completed messages). While only thinking/tools are streaming, keep the
  // timeline always-visible with no collapse chrome.
  const canCollapseActivity =
    !isStreaming ||
    !!displayContent ||
    Boolean(waitingOnQuestion) ||
    visibleActivityParts.some((p) => p.type === 'text' && !!p.settled && !p.isFinal);

  // Every answered question is a row of the box, where it was asked. A card shows outside the box only while it waits
  // for an answer, or when no row of the box holds it (a reply saved before its steps were).
  const questionsInBox = useMemo(
    () =>
      new Set(
        visibleActivityParts.flatMap((part) => {
          const question = shownQuestionOf(part);
          return question ? normalizeAskUserQuestionPayload(question.payload).questions.map((q) => q.uuid) : [];
        }),
      ),
    [visibleActivityParts],
  );
  const inBox = (payload: AskUserQuestionPayload) =>
    normalizeAskUserQuestionPayload(payload).questions.every((q) => questionsInBox.has(q.uuid));
  const showStoredCard = Boolean(persistedAskUserQuestion) && !askQuestionMatchesRow && !inBox(persistedAskUserQuestion!);
  // A card the run stops on is drawn with the saved row, never under the streamed one: that trail still ends in a status
  // line the saved one lacks, so the card would move up as the saved row takes over.
  const showLiveCard =
    askQuestionMatchesRow &&
    Boolean(pendingAskUserQuestion) &&
    !(isStreaming && pendingAskUserQuestion!.status === 'pending') &&
    (pendingAskUserQuestion!.status === 'pending' || !inBox(pendingAskUserQuestion!.payload));
  const shownStepCount = visibleActivityParts.length;

  // Wrap citation callbacks so that onPreview always receives this message's
  // citationMaps — the panel needs all citations for the previewed record.
  // `effectiveCitationMaps` is read via `effectiveCitationMapsRef` instead of
  // being a dependency here, so `wrappedCallbacks` keeps its identity across
  // citation-only updates — it flows down as a prop to every citation badge
  // (`answer-content.tsx`), whose `React.memo` compares `callbacks` by
  // reference. Without this, a new citation batch arriving mid-stream would
  // invalidate every badge on the page, not just the ones it actually affects.
  const wrappedCallbacks = useMemo<CitationCallbacks | undefined>(() => {
    if (!citationCallbacks) return undefined;
    return {
      ...citationCallbacks,
      onPreview: citationCallbacks.onPreview
        ? (citation) => citationCallbacks.onPreview!(citation, effectiveCitationMapsRef.current)
        : undefined,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [citationCallbacks]);

  // Derive counts from citation maps
  const sourcesCount = effectiveCitationMaps.sourcesOrder.length;
  const citationCount = Object.keys(effectiveCitationMaps.citationsOrder).length;
  const connectors = useMemo(() => sourceConnectors(effectiveCitationMaps), [effectiveCitationMaps]);
  // The answer's way into the column, on its Sources tab, when the answer drew on something; an extension that sets
  // its own actions (even none) keeps them.
  const leadingActions =
    ext.leadingActions !== undefined
      ? ext.leadingActions
      : panelTakesTabs && answerSlotId && rowId && connectors.length > 0
        ? <SourcesButton slotId={answerSlotId} connectors={connectors} answerKey={rowId} />
        : undefined;

  const renderActivity = (
    parts: MessagePart[],
    collapsible: boolean,
    currentStatus: StatusMessage | null,
  ) => {
    const timeline = (
      <AgentActivityTimeline
        parts={parts}
        isStreaming={isStreaming}
        currentStatus={currentStatus}
        citationMaps={effectiveCitationMaps}
        citationCallbacks={wrappedCallbacks}
      />
    );
    return collapsible ? (
      <CollapsibleActivitySection
        parts={parts}
        isStreaming={isStreaming}
        waiting={waitingOnQuestion}
        replied={repliedInChat}
        chatRunning={chatRunning}
      >
        {timeline}
      </CollapsibleActivitySection>
    ) : (
      timeline
    );
  };

  // Sending again cannot lift a refusal only an admin can: the card's text says who to ask.
  const canRetry = isLastMessage && onRetry !== undefined && ext.allowRegenerate && !ranApprovedCall && !cannotRun(streamError);
  const retry = () => {
    const slotId = answerSlotIdNow(scopedSlotId);
    const slot = slotId ? useChatStore.getState().slots[slotId] : undefined;
    // The failed copy gives way to the question asked again, so it shows once. A busy chat may not take the send, so it stays.
    const rows = slot && rowId && !slot.isStreaming && !slot.remoteRunInProgress ? withoutFailedTurn(slot.messages, rowId) : null;
    if (rows && slotId) updateSlot(slotId, { messages: rows });
    onRetry?.({ question, attachments, collections, appliedFilters });
  };

  // How sure the model is of its answer, whenever it gave a level, whether or not it read sources. The badge tops the
  // answer text, under every trail and question card, so a run with no answer text yet shows none. The level comes
  // with the saved answer: while the answer streams the badge's place is kept, so its appearing moves nothing. Should
  // the answer end without one (a stop, a model that gave none), the place closes as the trail folds, with the same
  // motion, and the answer under it is held where the reader has it like the trail's fold.
  const [keptLevelPlace, setKeptLevelPlace] = useState(false);
  if (isStreaming && answerShown && !keptLevelPlace) setKeptLevelPlace(true);
  const levelPlaceCloses = keptLevelPlace && !isStreaming && !confidence && !ext.hideConfidence;
  const levelBadge = ext.hideConfidence
    ? null
    : !isStreaming && confidence
      ? <ConfidenceIndicator confidence={confidence} marginBottom="0" />
      : isStreaming || keptLevelPlace
        ? <ConfidenceIndicator confidence="High" marginBottom="0" placeholder />
        : null;
  // One element from the first streamed word to the end, so the badge taking its place is the same node.
  const confidenceBadge = levelBadge && (
    <div className="level-slot" data-open={!levelPlaceCloses}>
      <div>
        <div style={{ paddingBottom: 'var(--space-2)' }}>{levelBadge}</div>
      </div>
    </div>
  );
  // Before paint, as the trail's fold does: the scroll box around the answer holds it still while the place closes.
  useLayoutEffect(() => {
    if (levelPlaceCloses) shellRef.current?.dispatchEvent(new Event(TRAIL_FOLD_EVENT, { bubbles: true }));
  }, [levelPlaceCloses]);

  /* Agent activity timeline — thinking / tool calls / sub-agents,
     streamed live or rendered from the persisted transcript.
     While streaming, the live status ("Thinking...", "Using
     Jira Search...") renders as the LAST timeline entry instead
     of a disconnected element below the answer — see
     AgentActivityTimeline's `currentStatus` prop. Once narration
     or the answer starts (or the response is complete), wrap in
     CollapsibleActivitySection so the user can collapse the
     trace if they want — never auto-collapsed while live.
     Gated on visible parts (not raw part count) — a
     completed simple response's `effectiveParts` is just the one
     `isFinal` text part, which the timeline itself filters out.
     `isStreaming && multiStep` is OR'd in so the timeline stays
     reachable while a multi-step run is active but hasn't
     produced a visible part yet. One box holds the whole turn:
     the work of every run and each question answered between
     them, in the order they happened. */
  const activity =
    effectiveParts &&
    (visibleActivityParts.length > 0 || (isStreaming && multiStep)) &&
    renderActivity(
      effectiveParts,
      canCollapseActivity,
      // non-multiStep: null prevents duplicate status with StatusMessageComponent below
      multiStep ? streamingStatusToShow : null,
    );
  // A card drawn right above the answer, which then starts 16px under it.
  const cardAboveAnswer = rerunCard
    ? !activity
    : changingAnswers || (askQuestionMatchesRow ? showLiveCard : showStoredCard);

  const renderTabContent = (opts?: { padding?: string; leadTight?: boolean }) => {
    // 24px under the question or the tabs, as a margin so it merges with the question's own and with the first
    // block's (a status line, a heading): the first line lands in one place, with a trail or without, live or done.
    // On a wide row, 16 straight under the question.
    // No bottom spacing: the last block's own margin and the toolbar's 24px collapse into one gap.
    const spacing: React.CSSProperties = opts?.padding
      ? { padding: opts.padding }
      : { marginTop: wideRow ? 'var(--space-4)' : 'var(--space-5)' };
    switch (activeTab) {
      case 'answer':
        if (failed && !isStreaming && !blockedTools) {
          return (
            <Box style={spacing}>
              <AnswerFailed
                message={answer}
                onRetry={canRetry ? retry : undefined}
              />
            </Box>
          );
        }
        return (
          <Box style={spacing} className={opts?.leadTight ? 'answer-lead-tight' : undefined}>
            {/* A re-run with changed answers streams only the reply to them: the answers it runs with come first. */}
            {rerunCard}
            {activity && (
              <Box data-testid="chat-activity-card" style={ACTIVITY_CARD_STYLE}>
                {activity}
              </Box>
            )}

            {/* A saved card no row of the box holds — read-only display */}
            {rerunCard || askQuestionMatchesRow ? null : changingAnswers ? (
              changeAnswersEditor
            ) : showStoredCard ? (
              <AskUserQuestionCard
                payload={persistedAskUserQuestion!}
                initialAnswers={persistedAskUserQuestionAnswers ?? {}}
                status="persisted"
              />
            ) : null}

            {ext.answerExtras}

            {/* The card waiting for an answer, under the box that led to it */}
            {rerunCard || !askQuestionMatchesRow || !pendingAskUserQuestion ? null : changingAnswers ? (
              changeAnswersEditor
            ) : showLiveCard ? (
              <AskUserQuestionCard
                payload={pendingAskUserQuestion.payload}
                initialAnswers={pendingAskUserQuestion.answers}
                status={pendingAskUserQuestion.status}
                sentAnswers={pendingAskUserQuestion.sentAnswers}
                readOnlyFor={ext.askCardReadOnlyFor}
                submitHeld={ext.askCardSubmitHeld}
                onAnswersChange={(nextAnswers) => {
                  const sid = answerSlotIdNow(scopedSlotId);
                  const p = sid ? useChatStore.getState().slots[sid]?.pendingAskUserQuestion : null;
                  if (!sid || !p) return;
                  useChatStore.getState().updateSlot(sid, {
                    pendingAskUserQuestion: { ...p, answers: nextAnswers },
                  });
                }}
                onSubmit={(message, nextAnswers) => {
                  const sid = answerSlotIdNow(scopedSlotId);
                  const p = sid ? useChatStore.getState().slots[sid]?.pendingAskUserQuestion : null;
                  if (!sid || !p) return;
                  useChatStore.getState().updateSlot(sid, {
                    pendingAskUserQuestion: { ...p, answers: nextAnswers, status: 'submitted' },
                  });
                  const request = buildStreamChatRequestForSlot(sid, message);
                  if (request) ext.decorateResumeRequest?.(request, p);
                  if (request) {
                    void streamMessageForSlot(sid, message, request, {
                      resumeAskUserQuestion: true,
                    });
                  }
                }}
              />
            ) : null}

            {/* A reply stopped on tools that aren't connected shows how to connect them instead. */}
            {blockedTools && (ext.toolsConnectReadOnlyFor ? (
              <UnavailableToolsNotice
                items={blockedTools.items}
                answer=""
                question={question}
                readOnlyFor={ext.toolsConnectReadOnlyFor}
              />
            ) : (
              <ConnectToolsCard
                items={blockedTools.items}
                agentId={blockedTools.agentId}
                serviceAccount={blockedTools.serviceAccount}
                question={question}
                blocked
                testId={blockedTools.items.every((item) => item.kind === 'mcp') ? 'mcp-connect-required' : undefined}
              />
            ))}

            {/* Continuation / final answer — after the box, so the turn reads
                its work and questions, then the answer. Hidden while a card
                is still waiting for answers. */}
            {approvalReceipts.length > 0 && (
              <Box mb="3">
                <ToolApprovalReceipts items={approvalReceipts} />
              </Box>
            )}

            {answerShown && !questionPending && !blockedTools && (
              <Box
                style={{
                  // 24px above the badge, as a margin that merges with the box's above it; 16px under a card, and
                  // on a wide row when the badge is the first thing under the question (globals.css).
                  marginTop: confidenceBadge && !levelPlaceCloses
                    ? 'var(--answer-lead-gap, var(--space-5))'
                    : cardAboveAnswer
                      ? 'var(--space-4)'
                      : 0,
                  // The badge's place closing eases this to the answer's own spacing, with the fold.
                  transition: keptLevelPlace ? 'margin-top calc(var(--motion-page) * 0.9375) var(--motion-ease)' : undefined,
                }}
              >
                {confidenceBadge}
                <AnswerContent
                  content={answerShown}
                  citationMaps={effectiveCitationMaps}
                  citationCallbacks={wrappedCallbacks}
                  isStreaming={isStreaming}
                />
              </Box>
            )}

            {!isStreaming && searchDegraded && <SearchDegradedNote />}

            {/* The answer went on without tools the asker hasn't connected: offer to connect them once it is done. */}
            {!isStreaming && !failed && !blockedTools && answerShown && unavailableTools && unavailableTools.length > 0 && (
              <UnavailableToolsNotice
                items={unavailableTools}
                answer={answerShown}
                question={question}
                readOnlyFor={ext.toolsConnectReadOnlyFor}
              />
            )}

            {/* After an answered question the reply carries on in this row and can stop on an
                approval: its card shows under the question. Only an unanswered question comes first. */}
            {toolApproval && !questionPending && (
              <Box mt="3">
                <ToolApprovalCard
                  key={toolApproval.approvalId}
                  approval={toolApproval}
                  isLatest={isLastMessage}
                  rowId={rowId}
                  readOnlyFor={ext.approvalCardReadOnlyFor}
                />
              </Box>
            )}

            {signInServers.length > 0 && (
              <Box mt="3">
                <McpSignInCard servers={signInServers} />
              </Box>
            )}

            {/* A response cut short by Stop says so in the answer's place: what is
                kept, and Try again where a retry is allowed. Only once settled. */}
            {showsStoppedMarker({
              status,
              isStreaming,
              hasQuestionCard,
              hasAnswerText: visibleAnswer.trim() !== '',
              repliedInChat: Boolean(persistedAskUserQuestion?.repliedInChat),
            }) && (
              <Box style={{ marginTop: answerShown.trim() ? 'var(--space-2)' : 0 }}>
                <RunErrorCard
                  tone="neutral"
                  icon="stop_circle"
                  testId="chat-stopped-card"
                  title={ext.stoppedByOther?.title ?? t('chat.runError.stoppedTitle')}
                  body={
                    shownStepCount > 0
                      ? t('chat.runError.stoppedBody')
                      : answerShown.trim()
                        ? (ext.stoppedByOther?.partialBody ?? t('chat.runError.stoppedBodyPartial'))
                        : undefined
                  }
                  retryLabel={t('chat.answerFailedRetry')}
                  onRetry={canRetry ? retry : undefined}
                />
              </Box>
            )}

            {/* "Currently doing X…" status for SIMPLE (non-multi-step)
                responses only — multi-step responses show the same status
                as the last timeline entry instead (see above), rendered
                LAST there for the same "tracks the bottom of the growing
                message" reason this one is placed last here. */}
            {isStreaming && streamingStatusToShow && !multiStep && (
              <StatusMessageComponent status={streamingStatusToShow} />
            )}

            {/* Legacy download buttons */}
            {downloadTasks.length > 0 && !questionPending && (
              <DownloadTasks tasks={downloadTasks} />
            )}

            {/* Artifacts generated by sandbox tools */}
            {effectiveArtifacts.length > 0 && !questionPending && (
              <ArtifactsPanel
                artifacts={effectiveArtifacts}
                latestArtifactVersions={latestArtifactVersions}
                onPreview={artifactActions.onPreview}
                onViewSource={artifactActions.onViewSource}
              />
            )}
          </Box>
        );
      case 'sources':
        return (
          <SourcesTab
            citationMaps={effectiveCitationMaps}
            callbacks={wrappedCallbacks}
          />
        );
      case 'citation':
        return (
          <CitationsTab
            citationMaps={effectiveCitationMaps}
            callbacks={wrappedCallbacks}
          />
        );
      default:
        return null;
    }
  };

  // Mirrors the backend's regenerate rules: only the newest question, and not
  // one that ran an approved action or is itself an approval answer. A turn that went
  // through a question card can be edited once its card is answered (the whole turn is
  // asked again), never while the card is still waiting.
  const rowHasCard = askQuestionMatchesRow || Boolean(persistedAskUserQuestion);
  const cardAnswered = Object.keys(persistedAskUserQuestionAnswers ?? {}).length > 0;
  // Someone a chat was shared with reads it; the server only lets its owner edit, unless the extension decides.
  const isChatOwner = useChatStore((st) =>
    answerSlotId ? st.slots[answerSlotId]?.isOwner !== false : true,
  );
  const canEditQuestion =
    (ext.allowEditQuery ?? isChatOwner) &&
    isLastMessage &&
    !questionPending &&
    (!rowHasCard || cardAnswered) &&
    !ranApprovedCall &&
    !question.trimStart().startsWith('Tool approval:');

  const handleEditQuery = useCallback(() => {
    if (!messageId || isStreaming) return;
    useCommandStore.getState().dispatch('showEditQuery', {
      messageId,
      text: question,
      appliedFilters,
      attachments,
      ...(scopedSlotId ? { slotId: scopedSlotId } : {}),
    });
  }, [messageId, question, isStreaming, appliedFilters, attachments, scopedSlotId]);

  // While this row's question card waits for an answer, read aloud and copy the
  // question and its options. Once answered, the reply written after it is the content.
  const speakContent = questionPending && pendingAskUserQuestion
    ? buildQuestionCardReadAloudText(pendingAskUserQuestion?.payload)
    : visibleAnswer;

  const appliedNodes = mentionedNodes(appliedFilters);

  // With the right column beside the chat, its Context tab lists the question's sources, so none sit under it.
  const filterChips = !panelTakesTabs && hasAnyFilter(appliedFilters);
  const chipsUnderQuestion = Boolean(appliedFilters && ext.showAppliedFilters !== false && filterChips) || Boolean(attachments?.length);
  const filtersAndAttachments = (
    <>
      {/* Applied filter chips — shown when connector/KB filters were scoped on this query */}
      {appliedFilters && ext.showAppliedFilters !== false && filterChips && (
        <Box style={{ marginBottom: 'var(--space-3)' }}>
          <AppliedFilters appliedFilters={appliedFilters} />
        </Box>
      )}

      {/* Attachment chips — uploaded files sent with this message */}
      {attachments && attachments.length > 0 && (
        <Flex
          align="center"
          gap="2"
          style={{
            marginBottom: 'var(--space-3)',
            overflowX: 'auto',
            overflowY: 'hidden',
          }}
          className="no-scrollbar"
        >
          {attachments.map((att) => {
            const isPastedText = isPastedTextAttachment(att);
            if (isPastedText) {
              return (
                <SentPasteChip
                  key={att.virtualRecordId || att.recordId}
                  kind="text"
                  onOpen={() => setTextPreviewAttachment(att)}
                />
              );
            }
            const openAttachment = () => handleAttachmentPreview(att);
            return (
              <Flex
                key={att.virtualRecordId || att.recordId}
                align="center"
                gap="1"
                role="button"
                tabIndex={0}
                title={att.recordName}
                onClick={openAttachment}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    // Space would otherwise scroll the transcript.
                    e.preventDefault();
                    openAttachment();
                  }
                }}
                style={{
                  flexShrink: 0,
                  padding: 'var(--space-1) var(--space-2)',
                  backgroundColor: 'var(--gray-a2)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-1)',
                  maxWidth: '200px',
                  cursor: 'pointer',
                  transition: 'background-color 0.15s',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--gray-a4)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--gray-a2)';
                }}
              >
                <FileIcon
                  extension={getMimeTypeExtension(att.mimeType) || att.extension || undefined}
                  filename={att.recordName}
                  size={14}
                  fallbackIcon="insert_drive_file"
                />
                <Text
                  size="1"
                  style={{
                    color: 'var(--text-secondary)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {att.recordName}
                </Text>
              </Flex>
            );
          })}
        </Flex>
      )}

    </>
  );

  const previewDialog = textPreviewAttachment ? (
    <TextPreviewDialog
      key={textPreviewAttachment.virtualRecordId || textPreviewAttachment.recordId}
      open
      onOpenChange={(open) => {
        if (!open) setTextPreviewAttachment(null);
      }}
      title={t('chat.attachments.pastedText', { defaultValue: 'Pasted text' })}
      loadText={async () => {
        const blob = await KnowledgeBaseApi.streamRecord(textPreviewAttachment.recordId);
        return blob.text();
      }}
    />
  ) : null;

  const renderActions = (opts?: { compact?: boolean }) => (
    <MessageActions
      compact={opts?.compact}
      allowRegenerate={ext.allowRegenerate && canSend}
      allowFeedback={ext.allowFeedback}
      allowFork={ext.allowFork}
      leadingActions={leadingActions}
      content={speakContent}
      citationMaps={effectiveCitationMaps}
      modelInfo={modelInfo}
      isStreaming={isStreaming}
      messageId={messageId}
      question={question}
      isLastMessage={isLastMessage && !questionPending}
      appliedFilters={appliedFilters}
      feedbackInfo={feedbackInfo}
      canRegenerate={!ranApprovedCall}
      parts={effectiveParts}
    />
  );

  const withRowKey = (node: React.ReactNode) =>
    citationMessageRowKey ? (
      <CitationMessageRowKeyContext.Provider value={citationMessageRowKey}>{node}</CitationMessageRowKeyContext.Provider>
    ) : (
      node
    );

  const parts: ChatResponseParts = {
    activeTab,
    setActiveTab,
    sourcesCount,
    citationCount,
    askCardOwnsRow: askQuestionMatchesRow || Boolean(persistedAskUserQuestion),
    renderTabContent,
    renderActions,
    filtersAndAttachments,
    previewDialog,
    withRowKey,
    onEditQuery: handleEditQuery,
    questionMentions: appliedNodes,
  };
  const customRow = ext.renderRow?.(parts);
  if (customRow) return <>{customRow}</>;

  const shell = (
    <Box ref={shellRef} style={{ width: '100%' }}>
      <Box
        data-chat-question
        style={{
          // On a wide row what follows sets its own 16 px down, so with nothing between them this adds none.
          // A headline is 24 px above what follows: the tabs, or the answer under its own 16 px.
          marginBottom: wideRow && !chipsUnderQuestion
            ? 0
            : ext.headline
            ? ext.hideTabs
              ? 'var(--space-2)'
              : 'var(--space-5)'
            : (collections && collections.length > 0) || filterChips
              ? 'var(--space-3)'
              // The answer's own tabs follow, below 1024px in a chat: 24px down to them, as the design's narrow layout.
              : panelScope?.column === false && !ext.hideTabs && !unanswered
                ? 'var(--space-5)'
                : 'var(--space-4)',
        }}
      >
        <ExpandableUserQuery
          question={question}
          isMobile={isMobile}
          messageId={messageId}
          isStreaming={isStreaming}
          onEdit={canEditQuestion ? handleEditQuery : undefined}
          headline={ext.headline}
          mentions={appliedNodes}
        />
        {/* Under the question, when it was sent; an extension may draw its own line instead (a room's byline has it). */}
        {ext.questionMeta !== undefined ? (
          ext.questionMeta
        ) : createdAt ? (
          <Text asChild size="1" style={{ color: 'var(--text-tertiary)', marginTop: 'var(--space-1)', display: 'block' }}>
            <time data-testid="question-time" dateTime={createdAt}>
              {formatQuestionTime(createdAt)}
            </time>
          </Text>
        ) : null}
      </Box>

      {filtersAndAttachments}

      {!unanswered && ext.beforeTabs}

      {/* Tabs */}
      {/* Tabs — hide Sources/Citations counts when the ask_user_question card
          (streaming or persisted) owns this row; those tabs reflect answer
          chunks that are suppressed. */}
      {!unanswered && !ext.hideTabs && !panelTakesTabs && (
        <ResponseTabs
          spread={panelScope?.column === false}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          sourcesCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : sourcesCount}
          citationCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : citationCount}
        />
      )}

      {/* Tab Content */}
      {!unanswered && renderTabContent(wideRow && !ext.beforeTabs && (ext.hideTabs || panelTakesTabs) ? { leadTight: true } : undefined)}

      {!unanswered && ext.afterContent?.(activeTab)}

      {/* Message Actions (feedback, copy, regenerate, model info) */}
      {/* A stopped reply with no text has nothing to rate, copy or read aloud. */}
      {!unanswered && activeTab === 'answer' && !(status === 'stopped' && !isStreaming && !visibleAnswer.trim()) && renderActions()}

      {previewDialog}
    </Box>
  );

  return <>{withRowKey(shell)}</>;
});

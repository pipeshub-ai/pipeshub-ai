'use client';

import React, { useState, useMemo, useRef, useCallback, useEffect, useContext } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { SelectedCollections } from '../selected-collections';
import { AppliedFilters } from '../applied-filters';
import { ResponseTabs } from './response-tabs';
import { ConfidenceIndicator } from './confidence-indicator';
import { AnswerContent } from './answer-content';
import { StatusMessageComponent } from './status-message';
import { MessageActions } from './message-actions';
import { SourcesTab } from './response-tabs/citations/sources-tab';
import { CitationsTab } from './response-tabs/citations/citations-tab';
import { ArtifactsPanel } from './artifacts-panel';
import { useArtifactActions } from './use-artifact-actions';
import { AskUserQuestionCard, askUserQuestionOwnsRow } from './ask-user-question-card';
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
import { toolsBlockedDetails, type ChatStreamErrorInfo } from '../../stream-error';
import type { UnavailableTool } from '../../unavailable-tools';
import { AgentActivityTimeline, CollapsibleActivitySection, getVisibleRootParts, hasMultiStepActivity } from './agent-activity';
import { ExpandableUserQuery } from './expandable-user-query';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { AnswerFailed } from './answer-failed';
import { formatMessageTime } from './format-message-time';
import { chatSlots } from '@/config';
import type { ChatResponseLayout, ChatResponseParts } from '../../chat-extension';
import type { MessagePair, RetryTurn } from './message-pairs';
import { useCommandStore } from '@/lib/store/command-store';
import { useChatStore } from '../../store';
import { AnswerSlotContext, answerSlotIdNow, useAnswerSlotId } from './answer-slot-scope';
import { debugLog } from '../../debug-logger';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { AskUserQuestionAnswer, AskUserQuestionPayload, AttachmentRef, ConfidenceLevel, ModelInfo, StatusMessage, ResponseTab, ChatArtifact, AppliedFilters as AppliedFiltersData, AppliedFilterNode, MessagePart } from '../../types';
import { FileIcon } from '@/app/components/ui/file-icon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { emptyCitationMaps } from './response-tabs/citations';
import { repairStreamingMarkdown } from '../../utils/repair-streaming-markdown';
import { processMarkdownContent } from '../../utils/process-markdown-content';
import { parseDownloadMarkers, parseArtifactMarkers } from '../../utils/parse-download-markers';
import { isAskUserQuestionTool } from '../../utils/tool-display';
import { BELOW_APP_KEYS, hasAnyFilter } from '../../utils/tree-selection';
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

// Stable empty reference — avoids creating new objects in default params
const EMPTY_CITATION_MAPS: CitationMaps = emptyCitationMaps();

// No bottom padding: the activity inside ends with its own bottom margin. A step of light above the page, as wide as
// the answer column, the same as the steps in a one-person chat.
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

/**
 * The nodes the question named with @, to show as chips in it. A message saved
 * before that was recorded falls back to its folders and records: an app or a
 * collection is often named like an ordinary word.
 */
function mentionedNodes(applied: AppliedFiltersData | undefined): AppliedFilterNode[] {
  if (!applied) return [];
  const all = [...(applied.apps ?? []), ...(applied.kb ?? []), ...BELOW_APP_KEYS.flatMap((key) => applied[key] ?? [])];
  if (all.some((node) => typeof node.mentioned === 'boolean')) return all.filter((node) => node.mentioned);
  return BELOW_APP_KEYS.flatMap((key) => applied[key] ?? []);
}

export const ChatResponse = React.memo(function ChatResponse({
  question,
  answer,
  citationMaps = EMPTY_CITATION_MAPS,
  citationCallbacks,
  confidence,
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
  const activeTab = (activeExpandedMessageId === messageId || !messageId)
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
        isStreaming && streamingContent ? repairStreamingMarkdown(streamingContent) : answer,
      ),
    [isStreaming, streamingContent, answer],
  );
  // Extract persisted artifact + legacy download-task markers so the markdown
  // pipeline doesn't try to render them as raw text. The backend appends these
  // markers to the final saved answer content:
  //   ::artifact[name](url){mime|docId|recordId}
  //   ::download_conversation_task[name](url)  (legacy CSV download)
  const { text: contentWithoutArtifacts, artifacts: persistedArtifacts } = useMemo(
    () => parseArtifactMarkers(processedContent),
    [processedContent],
  );
  const { text: displayContent, tasks: downloadTasks } = useMemo(
    () => parseDownloadMarkers(contentWithoutArtifacts),
    [contentWithoutArtifacts],
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
  // During streaming, use live artifacts from SSE events (they arrive before
  // the final content exists). Once streaming ends, the markers in the saved
  // content become the source of truth — slot.artifacts gets wiped on
  // complete, so parsing from content keeps the panel populated for both
  // freshly completed and historically loaded messages.
  const effectiveArtifacts: ChatArtifact[] =
    isStreaming && streamingArtifacts && streamingArtifacts.length > 0
      ? streamingArtifacts
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
  // Servers that refused for missing permission: signed in to again from the latest, settled reply.
  const signInServers = useMemo(
    () => (isStreaming || !isLastMessage ? [] : mcpSignInServers(effectiveParts)),
    [effectiveParts, isStreaming, isLastMessage],
  );
  // Drives both the "Answer" separator and the collapsible-summary wrapper
  // around a completed activity timeline — computed with the exact same
  // filtering the timeline itself applies, so it never disagrees with what
  // actually renders (e.g. a transcript containing only the isFinal part
  // and nothing else shows no separator).
  const visibleActivityParts = useMemo(
    () => (effectiveParts ? getVisibleRootParts(effectiveParts, isStreaming) : []),
    [effectiveParts, isStreaming],
  );
  const hasVisibleActivity = visibleActivityParts.length > 0;
  // Collapse control is offered once narration/answer has started (or for
  // completed messages). While only thinking/tools are streaming, keep the
  // timeline always-visible with no collapse chrome.
  const canCollapseActivity =
    !isStreaming ||
    !!displayContent ||
    visibleActivityParts.some((p) => p.type === 'text' && !!p.settled && !p.isFinal);

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

  const canRetry = isLastMessage && onRetry !== undefined && ext.allowRegenerate && !ranApprovedCall;

  // Show confidence only when not streaming and has answer
  const confidenceBadge = !isStreaming && confidence && !ext.hideConfidence && (
    <ConfidenceIndicator confidence={confidence} marginBottom={ext.headline ? 'var(--space-2)' : undefined} />
  );

  /* Agent activity timeline — thinking / tool calls / sub-agents,
     streamed live or rendered from the persisted transcript.
     While streaming, the live status ("Thinking...", "Using
     Jira Search...") renders as the LAST timeline entry instead
     of a disconnected element below the answer — see
     AgentActivityTimeline's `currentStatus` prop. Once narration
     or the answer starts (or the response is complete), wrap in
     CollapsibleActivitySection so the user can collapse the
     trace if they want — never auto-collapsed while live.
     Gated on `hasVisibleActivity` (not raw part count) — a
     completed simple response's `effectiveParts` is just the one
     `isFinal` text part, which the timeline itself filters out.
     `isStreaming && multiStep` is OR'd in so the timeline stays
     reachable while a multi-step run is active but hasn't
     produced a visible part yet. Shown on ask_user_question
     rows too — the card is the question UI, not a reason to
     hide the tools that ran before it. */
  const activity = effectiveParts && (hasVisibleActivity || (isStreaming && multiStep)) && (
    canCollapseActivity ? (
      <CollapsibleActivitySection parts={effectiveParts} isStreaming={isStreaming}>
        <AgentActivityTimeline
          parts={effectiveParts}
          isStreaming={isStreaming}
          // non-multiStep: null prevents duplicate status with StatusMessageComponent below
          currentStatus={multiStep ? streamingStatusToShow : null}
          citationMaps={effectiveCitationMaps}
          citationCallbacks={wrappedCallbacks}
        />
      </CollapsibleActivitySection>
    ) : (
      <AgentActivityTimeline
        parts={effectiveParts}
        isStreaming={isStreaming}
        currentStatus={multiStep ? streamingStatusToShow : null}
        citationMaps={effectiveCitationMaps}
        citationCallbacks={wrappedCallbacks}
      />
    )
  );

  const renderTabContent = (opts?: { padding?: string }) => {
    const padding = opts?.padding ?? 'var(--space-4) 0';
    switch (activeTab) {
      case 'answer':
        if (failed && !isStreaming && !blockedTools) {
          return (
            <Box style={{ padding }}>
              <AnswerFailed
                message={answer}
                onRetry={canRetry ? () => onRetry?.({ question, attachments, collections, appliedFilters }) : undefined}
              />
            </Box>
          );
        }
        return (
          <Box style={{ padding }}>
            {ext.activityCard ? (
              <>
                {activity && (
                  <Box data-testid="chat-activity-card" style={ACTIVITY_CARD_STYLE}>
                    {activity}
                  </Box>
                )}
                {confidenceBadge}
              </>
            ) : (
              <>
                {confidenceBadge}
                {activity}
              </>
            )}

            {/* Separator marking the handoff from "here's the work" to
                "here's the answer" — only when there's activity actually
                visible above (not just a lone isFinal part that the
                timeline itself filtered out) and content to show below
                (answer text or the ask_user_question card). The card
                already sets the work apart. */}
            {!ext.activityCard && hasVisibleActivity && (answerShown || questionPending || persistedAskUserQuestion || toolApproval || approvalReceipts.length > 0) && (
              <Box
                data-testid="chat-activity-separator"
                className="agent-activity-enter"
                style={{
                  borderTop: '1px solid var(--slate-4)',
                  marginTop: 'var(--space-1)',
                  marginBottom: 'var(--space-3)',
                }}
              />
            )}

            {/* Persisted ask_user_question from historical tool_call — read-only display */}
            {persistedAskUserQuestion && !askQuestionMatchesRow ? (
              <AskUserQuestionCard
                payload={persistedAskUserQuestion}
                initialAnswers={persistedAskUserQuestionAnswers ?? {}}
                status="persisted"
              />
            ) : null}

            {ext.answerExtras}

            {/* Active (streaming/pending) ask_user_question card */}
            {askQuestionMatchesRow && pendingAskUserQuestion ? (
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

            {/* Continuation / final answer — below the question card so the
                turn reads tools → question → answer. Hidden while the card
                is still waiting for answers. */}
            {approvalReceipts.length > 0 && (
              <Box mb="3">
                <ToolApprovalReceipts items={approvalReceipts} />
              </Box>
            )}

            {answerShown && !questionPending && !blockedTools && (
              <Box
                style={{
                  marginTop:
                    persistedAskUserQuestion || askQuestionMatchesRow
                      ? 'var(--space-4)'
                      : undefined,
                }}
              >
                <AnswerContent
                  content={answerShown}
                  citationMaps={effectiveCitationMaps}
                  citationCallbacks={wrappedCallbacks}
                  isStreaming={isStreaming}
                />
              </Box>
            )}

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

            {/* Subtle marker for a response cut short by Stop. Only shown once
                settled (never while still streaming/regenerating) — regenerate
                remains available via MessageActions below, same as any other
                completed response. */}
            {!isStreaming && status === 'stopped' && !questionPending && (
              <Flex
                align="center"
                gap="1"
                data-testid="chat-stopped-marker"
                style={{ marginTop: 'var(--space-2)' }}
              >
                <MaterialIcon name="stop_circle" size={14} color="var(--slate-9)" />
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  {answerShown.trim() ? t('chat.stoppedMarker', { defaultValue: 'Stopped' }) : t('chat.stoppedBeforeAnswer')}
                </Text>
              </Flex>
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
  // Someone a chat was shared with reads it; the server only lets its owner edit.
  const isChatOwner = useChatStore((st) =>
    answerSlotId ? st.slots[answerSlotId]?.isOwner !== false : true,
  );
  const canEditQuestion =
    isChatOwner &&
    isLastMessage &&
    !questionPending &&
    (!rowHasCard || cardAnswered) &&
    !ranApprovedCall &&
    !question.trimStart().startsWith('Tool approval:');

  const handleEditQuery = useCallback(() => {
    if (!messageId || isStreaming || !canEditQuestion) return;
    useCommandStore.getState().dispatch('showEditQuery', {
      messageId,
      text: question,
      appliedFilters,
      attachments,
    });
  }, [messageId, question, isStreaming, canEditQuestion, appliedFilters, attachments]);

  // When the active question card owns this row, read aloud the question text
  // and its options instead of the hidden bot response.
  const speakContent = askQuestionMatchesRow && pendingAskUserQuestion
    ? buildQuestionCardReadAloudText(pendingAskUserQuestion?.payload)
    : visibleAnswer;

  const appliedNodes = mentionedNodes(appliedFilters);

  const filtersAndAttachments = (
    <>
      {/* Applied filter chips — shown when connector/KB filters were scoped on this query */}
      {appliedFilters && ext.showAppliedFilters !== false && hasAnyFilter(appliedFilters) && (
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
            const openAttachment = () =>
              isPastedText ? setTextPreviewAttachment(att) : handleAttachmentPreview(att);
            return (
              <Flex
                key={att.virtualRecordId || att.recordId}
                align="center"
                gap="1"
                role="button"
                tabIndex={0}
                title={isPastedText ? t('chat.attachments.pastedText', { defaultValue: 'Pasted text' }) : att.recordName}
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
                  backgroundColor: isPastedText ? 'var(--accent-a2)' : 'var(--olive-a2)',
                  border: isPastedText ? '1px dashed var(--accent-a7)' : '1px solid var(--olive-3)',
                  borderRadius: 'var(--radius-1)',
                  maxWidth: '200px',
                  cursor: 'pointer',
                  transition: 'background-color 0.15s',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor = isPastedText
                    ? 'var(--accent-a4)'
                    : 'var(--olive-a4)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor = isPastedText
                    ? 'var(--accent-a2)'
                    : 'var(--olive-a2)';
                }}
              >
                {isPastedText ? (
                  <MaterialIcon name="content_paste" size={14} color="var(--accent-9)" />
                ) : (
                  <FileIcon
                    extension={getMimeTypeExtension(att.mimeType) || att.extension || undefined}
                    filename={att.recordName}
                    size={14}
                    fallbackIcon="insert_drive_file"
                  />
                )}
                <Text
                  size="1"
                  style={{
                    color: 'var(--slate-11)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {isPastedText
                    ? t('chat.attachments.pastedText', { defaultValue: 'Pasted text' })
                    : att.recordName}
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
      allowFeedback={ext.allowFeedback !== false}
      leadingActions={ext.leadingActions}
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
    <Box style={{ width: '100%' }}>
      <Box
        data-chat-question
        style={{
          // A headline is 24 px above what follows: the tabs, or the answer under its own 16 px.
          marginBottom: ext.headline
            ? ext.hideTabs
              ? 'var(--space-2)'
              : 'var(--space-5)'
            : (collections && collections.length > 0) || hasAnyFilter(appliedFilters)
              ? 'var(--space-3)'
              : 'var(--space-4)',
        }}
      >
        <ExpandableUserQuery
          question={question}
          isMobile={isMobile}
          messageId={messageId}
          isStreaming={isStreaming}
          onEdit={ext.allowEditQuery === false || !canEditQuestion ? undefined : handleEditQuery}
          headline={ext.headline}
          mentions={appliedNodes}
        />
        {ext.questionMeta ?? (createdAt && (
          <Text
            size="1"
            style={{
              color: 'var(--slate-9)',
              marginTop: 'var(--space-1)',
              display: 'block',
            }}
          >
            {formatMessageTime(createdAt)}
          </Text>
        ))}
      </Box>

      {filtersAndAttachments}

      {!unanswered && ext.beforeTabs}

      {/* Tabs */}
      {/* Tabs — hide Sources/Citations counts when the ask_user_question card
          (streaming or persisted) owns this row; those tabs reflect answer
          chunks that are suppressed. */}
      {!unanswered && !ext.hideTabs && (
        <ResponseTabs
          activeTab={activeTab}
          onTabChange={setActiveTab}
          sourcesCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : sourcesCount}
          citationCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : citationCount}
        />
      )}

      {/* Tab Content */}
      {!unanswered && renderTabContent()}

      {!unanswered && ext.afterContent?.(activeTab)}

      {/* Message Actions (feedback, copy, regenerate, model info) */}
      {!unanswered && activeTab === 'answer' && renderActions()}

      {previewDialog}
    </Box>
  );

  return <>{withRowKey(shell)}</>;
});

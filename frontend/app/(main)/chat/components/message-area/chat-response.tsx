'use client';

import React, { useState, useMemo, useRef, useCallback, useEffect, useId } from 'react';
import { Box, Button, Callout, Flex, Text } from '@radix-ui/themes';
import { SelectedCollections } from '../selected-collections';
import { AppliedFilters } from '../applied-filters';
import { ResponseTabs, responseTabIds } from './response-tabs';
import { ConfidenceIndicator } from './confidence-indicator';
import { SearchDegradedNote } from './search-degraded-note';
import { AccessNotices, type AccessNotice } from '@/app/components/feedback/access-notice';
import { AnswerContent } from './answer-content';
import { QuoteSelectionLayer } from './quote-selection-toolbar';
import { StatusMessageComponent } from './status-message';
import { MessageActions } from './message-actions';
import { SourcesTab } from './response-tabs/citations/sources-tab';
import { CitationsTab } from './response-tabs/citations/citations-tab';
import { ArtifactsPanel, isArtifactGone } from './artifacts-panel';
import { ApprovalInteraction, InteractionReceipt, QueuedInteraction, type InteractionState } from './interaction-card';
import { connectWaitingLine } from './connect-card';
import { VerificationPanel } from './run-gate-cards';
import { FilesChanged } from './files-changed';
import { RunSummary } from './run-summary';
import { NotFoundNotice } from './not-found-notice';
import { CouldNotDoNotice } from './could-not-do-notice';
import { cardWords, type CardWords } from './grounding-card';
import { notFoundOf, withoutNotFoundLine } from '../../subject-not-found';
import { couldNotDoOf, withoutLeadLine } from '../../could-not-do';
import { CloneUnavailableCards, OutsideAccess } from './outside-access';
import { changedFilesFromArtifacts, changedFilesFromEngine, type ChangedFile } from '../../engine-steps';
import { MemoryChip } from './memory-chip';
import { AttachmentChip } from './attachment-chip';
import { decideApproval, decideApprovals, decidedRuns } from '../../approvals';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import {
  AskUserQuestionCard,
  answerSummary,
  askUserQuestionOwnsRow,
  showsStoppedMarker,
  buildResumeMessage,
  isAskUserQuestionExpired,
} from './ask-user-question-card';
import {
  activeInteractionKey,
  approvalAnchor,
  groupApprovals,
  isOpenGroup,
  mergeApprovals,
  openCalls,
  orderInteractions,
  partAnchors,
  questionAnchor,
  savedApprovals,
  waitingSince,
  type ApprovalGroup,
  type PlacedInteraction,
} from '../../interactions';
import { ErrorNextStep } from './error-next-step';
import { McpConnectRequiredCard } from './mcp-connect-required-card';
import { McpSignInCard } from './mcp-sign-in-card';
import { ToolApprovalCard, ToolApprovalReceipts } from './tool-approval-card';
import { FloatedAskPointer } from '../job-ask/floated-ask-pointer';
import { floatsGroup, pointerKey, useFloatedAsks } from '../job-ask/job-asks';
import {
  answeredToolApproval,
  findToolApproval,
  hasApprovedCall,
  settledToolApprovals,
  withoutWaitingLine,
} from '../../tool-approval';
import { mcpSignInServers } from '../../mcp-sign-in';
import { mcpConfigMissingDetails, type ChatStreamErrorInfo } from '../../stream-error';
import { TurnJobs } from './job-card/turn-jobs';
import { useIsJobPart } from './job-card/job-parts';
import { AgentActivityTimeline, CollapsibleActivitySection, getVisibleRootParts, hasMultiStepActivity, planFromParts, RunNotices, SteeringMessage } from './agent-activity';
import { ExpandableUserQuery } from './expandable-user-query';
import { answerQuestionCard } from '../../question-answer';
import { questionSendKey, useCardSend } from '../../card-send';
import { admissionRefusalText } from '../../harness-errors';
import { CardSendNotice } from './card-send-notice';
import { useCommandStore } from '@/lib/store/command-store';
import { useChatStore } from '../../store';
import { useRenderSlotId } from './render-slot';
import { debugLog } from '../../debug-logger';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { buildAssistantApiFilters, type ConversationMessage, type AskUserQuestionAnswer, type AskUserQuestionPayload, type AttachmentRef, type ConfidenceLevel, type ModelInfo, type StatusMessage, type ResponseTab, type ChatArtifact, type AppliedFilters as AppliedFiltersData, type AppliedFilterNode, type MessagePart } from '../../types';
import type { SentCommand } from '../../commands';
import { FileIcon } from '@/app/components/ui/file-icon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { CutShortReason } from '@/lib/contracts/harness/generated/core/enums';
import { budgetScopeOf, cutShortOf } from '../../cut-short';
import type { BudgetScope } from '../../types';
import { CutShortNotice } from './cut-short-notice';
import { OutcomeNotice } from './outcome-notice';
import { useLiveRunCost } from './use-live-run-cost';
import { hasLegacyOutcomeWords, runOutcomeOf } from '../../run-outcome';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { emptyCitationMaps } from './response-tabs/citations';
import { repairStreamingMarkdown } from '../../utils/repair-streaming-markdown';
import { citedAnswer, type CitedAnswer } from '../../utils/cited-answer';
import { extractAnswerMarkers, isSignedUrl, isTrustedApiUrl } from '../../utils/parse-download-markers';
import { isAskUserQuestionTool } from '../../utils/tool-display';
import { BELOW_APP_KEYS, hasAnyFilter } from '../../utils/tree-selection';
import { DownloadTasks } from './download-tasks';
import { useDecisionAuthority } from '../../hooks/use-decision-authority';
import { approvalAskRules } from '@/config';
import { ViewActivityButton } from '../activity-panel/activity-panel-host';
import { RunCommandActions } from './run-command-actions';
import { sentRunLine } from '../../sandbox-commands';
import { ContextCompactedRow } from './context-compacted-row';
import { useMessageRun } from '../activity-panel/use-activity-run';
import { useRunAgentVersion } from '../../hooks/use-run-agent-version';
import { RunVersionLabel } from './run-version-label';
import {
  isDocxFile,
  resolvePreviewMimeAfterStream,
  streamRecordForPreview,
} from '@/app/components/file-preview/utils';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
import { TextPreviewDialog } from '../text-preview-dialog';
import { isPastedTextAttachment } from '../../utils/paste-attachment';
import { useTranslation } from 'react-i18next';
import { CitationMessageRowKeyContext } from './response-tabs/citations/citation-popover-control';
import { useInlineCitationPopoverStore } from './response-tabs/citations/citation-popover-store';

// Stable empty reference — avoids creating new objects in default params
const EMPTY_CITATION_MAPS: CitationMaps = emptyCitationMaps();

function formatMessageTime(isoString: string): string {
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return '';
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const timeStr = date.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
  if (isToday) return timeStr;
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function buildQuestionCardReadAloudText(payload: AskUserQuestionPayload, words: CardWords): string {
  const parts: string[] = [];
  if (payload.userIntent) parts.push(words.intent(payload.userIntent));
  payload.questions.forEach((q, i) => {
    parts.push(`Question ${i + 1}: ${words.question(q.question)}`);
    q.options.forEach((opt) => parts.push(`Option: ${words.label(opt.label)}`));
  });
  return parts.join('. ');
}

interface FeedbackInfo {
  value?: 'like' | 'dislike';
}

interface ChatResponseProps {
  question: string;
  answer: string;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
  confidence?: ConfidenceLevel;
  /** A search behind this answer was served with less than the full search. */
  searchDegraded?: boolean;
  accessNotices?: AccessNotice[];
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
  latestArtifactVersions?: Map<string, number>;
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
  /** The /command the question was sent as (SLASH1). */
  command?: SentCommand;
  /** Persisted ask_user_question payload from a historical tool_call — renders read-only question card */
  persistedAskUserQuestion?: AskUserQuestionPayload;
  persistedAskUserQuestionAnswers?: Record<string, AskUserQuestionAnswer>;
  /** Persisted feedback value from the backend — initialises the like/dislike button state */
  feedbackInfo?: { value?: 'like' | 'dislike' };
  /** Set when this response was cut short by a user-initiated Stop (see `IMessage.status`, Node). */
  status?: 'stopped';
  /** The limit that cut the run's work short (BUDGETEND): the answer is what it gathered, with a notice. */
  truncatedBy?: CutShortReason;
  /** With `truncatedBy` `budget`: which budget beyond the run's own ran out (C9.06, WP-51); `budgetStopLines` words it. */
  budgetScope?: BudgetScope;
  /** ANSWER-CONTRACT-FU-2: the saved message's not-found fact, read when its final part is a live one. */
  notFound?: unknown;
  /** R32REPO-FU-1: the saved message's "could not do" fact, read the same way. */
  limits?: unknown;
  /** The server's error code and details when this reply is a failed run. */
  streamError?: ChatStreamErrorInfo;
  /**
   * No assistant row exists for this question (see `buildMessagePairs`). The
   * question is drawn on its own: an answer area, tabs and message actions
   * would all be empty controls over a reply that was never produced.
   */
  unanswered?: boolean;
  /** The durable run that wrote this answer, when the saved message names it (CTL-3: its activity and plan). */
  runId?: string;
  /** The answer is withheld: show why, and a way to ask again. */
  answerHidden?: ConversationMessage['answerHidden'];
  /**
   * A Compare column: the question is drawn once above both columns, and the
   * reply cannot be regenerated or re-answered while two answers compete.
   */
  columnMode?: boolean;
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
  searchDegraded = false,
  accessNotices,
  isStreaming = false,
  modelInfo,
  collections,
  appliedFilters,
  attachments,
  command,
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
  truncatedBy,
  budgetScope,
  notFound: savedNotFound,
  limits: savedLimits,
  streamError,
  unanswered = false,
  runId,
  answerHidden,
  columnMode = false,
}: ChatResponseProps) {
  debugLog.tick('[chat] [ChatResponse]');
  const { t } = useTranslation();
  const mcpConnectDetails = isStreaming ? null : mcpConfigMissingDetails(streamError);
  // The reply failed (the provider was rate limiting, overloaded, ...): its text says why,
  // shown as an error so it doesn't read like an answer.
  const answerFailed = !isStreaming && !!streamError && !mcpConnectDetails;
  const isMobile = useIsMobile();

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

  // Read the slot-level activeExpandedMessageId so we can reset to 'answer'
  // when a different message becomes the expanded one.
  const activeSlotId = useRenderSlotId();
  // Asks the same question again as a new turn of this chat, answered with today's access.
  const askAgain = useCallback(() => {
    const slot = activeSlotId ? useChatStore.getState().slots[activeSlotId] : null;
    // Two answers compete in a Compare column: one is chosen, not asked again.
    if (columnMode || !activeSlotId || !slot || slot.isStreaming) return;
    const request = buildStreamChatRequestForSlot(activeSlotId, question);
    if (!request) return;
    // The turn's own scope and files, not what the composer holds now. The server's usual
    // checks apply: a file the person can no longer read is refused as always.
    if (appliedFilters) {
      request.appliedFilters = appliedFilters;
      request.filters = buildAssistantApiFilters({
        apps: appliedFilters.apps.map((a) => a.id),
        kb: appliedFilters.kb.map((k) => k.id),
        recordGroups: (appliedFilters.recordGroups ?? []).map((g) => g.id),
        records: (appliedFilters.records ?? []).map((r) => r.id),
        recordsExact: (appliedFilters.recordsExact ?? []).map((r) => r.id),
      });
    } else {
      // An unscoped question stays unscoped, whatever the composer holds now.
      delete request.appliedFilters;
      request.filters = { apps: [], kb: [] };
    }
    if (attachments && attachments.length > 0) request.attachments = attachments;
    void streamMessageForSlot(activeSlotId, question, request);
  }, [activeSlotId, question, appliedFilters, attachments, columnMode]);
  const activeExpandedMessageId = useChatStore((s) =>
    activeSlotId ? s.slots[activeSlotId]?.activeExpandedMessageId ?? null : null
  );
  const updateSlot = useChatStore((s) => s.updateSlot);
  const setPreviewFile = useChatStore((s) => s.setPreviewFile);
  const clearPreview = useChatStore((s) => s.clearPreview);
  const setPreviewMode = useChatStore((s) => s.setPreviewMode);
  // Someone a chat was shared with reads it; the server only lets its owner edit or quote.
  const isChatOwner = useChatStore((st) =>
    activeSlotId ? st.slots[activeSlotId]?.isOwner !== false : true,
  );

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
        const { blob, asPdf } = await streamRecordForPreview(
          (options) => KnowledgeBaseApi.streamRecord(att.recordId, options),
          att.mimeType,
          att.recordName,
        );
        const resolvedType = resolvePreviewMimeAfterStream(
          att.mimeType,
          att.recordName,
          blob,
          asPdf,
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

  /**
   * Streams `artifact.recordId` at `overrideVersion` (defaulting to
   * `artifact.version`) and replaces the preview panel's content in place.
   * Used both for the initial "Preview" click (`ArtifactsPanel.onPreview`)
   * and for every later version switch (bound back to this same function as
   * `ChatPreviewFile.onVersionChange`) — one code path, so streaming options
   * (PDF conversion for PPT/DOCX, mime resolution, DOCX blob handling) never
   * drift between the two entry points.
   */
  const loadArtifactPreview = useCallback(
    async (artifact: ChatArtifact, overrideVersion?: number) => {
      const version = overrideVersion ?? artifact.version;
      const recordId = artifact.recordId;
      const isSwitch = overrideVersion !== undefined;
      const latestVersion = recordId ? latestArtifactVersions?.get(recordId) : undefined;
      const effectiveLatest =
        latestVersion !== undefined && version !== undefined
          ? Math.max(latestVersion, version)
          : latestVersion ?? version;
      const onVersionChange = recordId
        ? (v: number) => loadArtifactPreview(artifact, v)
        : undefined;

      if (recordId) {
        // A version switch (not the initial open) — keep the current
        // content on screen and only flip the small spinner in the version
        // pill, so the panel doesn't flash back to the loading skeleton.
        if (isSwitch) {
          const current = useChatStore.getState().previewFile;
          if (current?.id === recordId) setPreviewFile({ ...current, isSwitchingVersion: true });
        }
        try {
          const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
          const { blob, asPdf } = await streamRecordForPreview(
            (options) => KnowledgeBaseApi.streamRecord(recordId, options),
            artifact.mimeType,
            artifact.fileName,
            {
              ...(version !== undefined ? { version } : {}),
              // The card shows a deleted artifact itself; a version switch has no card to tell.
              ...(isSwitch ? {} : { suppressErrorToast: isArtifactGone }),
            },
          );
          const resolvedType = resolvePreviewMimeAfterStream(
            artifact.mimeType,
            artifact.fileName,
            blob,
            asPdf,
          );
          const isDocx = isDocxFile(artifact.mimeType, artifact.fileName);
          const objectUrl = isDocx ? '' : URL.createObjectURL(blob);

          // Release the previous blob URL now that nothing references it —
          // otherwise every version switch leaks one object URL.
          const previous = useChatStore.getState().previewFile;
          if (previous?.url?.startsWith('blob:')) URL.revokeObjectURL(previous.url);

          setPreviewFile({
            id: recordId,
            url: objectUrl,
            blob: isDocx ? blob : undefined,
            name: artifact.fileName,
            type: resolvedType,
            sourceType: artifact.mimeType,
            size: artifact.sizeBytes,
            hideFileDetails: true,
            showDownload: true,
            version,
            latestVersion: effectiveLatest,
            onVersionChange,
            isSwitchingVersion: false,
          });
          return;
        } catch (error) {
          if (isSwitch) {
            // Keep showing the last successfully loaded version rather than
            // falling back to a stale/foreign URL below.
            const current = useChatStore.getState().previewFile;
            if (current?.id === recordId) setPreviewFile({ ...current, isSwitchingVersion: false });
            return;
          }
          if (isArtifactGone(error)) throw error;
          // Initial-open failure — fall through to the URL-classification
          // fallback below (never trusts an arbitrary marker URL, see
          // `ArtifactsPanel`'s `handleDownload` docstring for the same rule).
        }
      }

      const fallbackUrl = artifact.downloadUrl?.trim();
      const trusted = !!fallbackUrl && (isTrustedApiUrl(fallbackUrl) || isSignedUrl(fallbackUrl));
      setPreviewFile({
        id: artifact.id,
        url: trusted ? fallbackUrl : '',
        ...(trusted ? {} : { error: t('artifacts.previewFailed') }),
        name: artifact.fileName,
        type: artifact.mimeType,
        size: artifact.sizeBytes,
        hideFileDetails: true,
        showDownload: true,
        version,
        latestVersion: effectiveLatest,
        onVersionChange,
      });
    },
    [latestArtifactVersions, setPreviewFile, t],
  );

  const pendingAskUserQuestion = useChatStore((s) =>
    activeSlotId ? s.slots[activeSlotId]?.pendingAskUserQuestion ?? null : null
  );

  const pendingApprovals = useChatStore((s) =>
    activeSlotId ? s.slots[activeSlotId]?.pendingApprovals : undefined
  );
  const durableRun = useChatStore((s) =>
    activeSlotId ? s.slots[activeSlotId]?.durableRun ?? null : null
  );
  const approvalAuthority = useDecisionAuthority(
    durableRun,
    isStreaming && (pendingApprovals?.some((approval) => approval.status === 'pending') ?? false),
    pendingApprovals?.some((approval) => approval.status === 'pending' && approvalAskRules.holds(approval.reason)) ?? false,
  );

  const askQuestionMatchesRow = askUserQuestionOwnsRow(
    pendingAskUserQuestion,
    citationMessageRowKey,
    messageId,
  );
  // CARDFAIL: an answer that could not be sent yet: the card keeps the person's choice and says why.
  const questionSendKeyOfRow =
    askQuestionMatchesRow && pendingAskUserQuestion ? questionSendKey(pendingAskUserQuestion.assistantMessageId) : null;
  const questionSend = useCardSend(questionSendKeyOfRow);
  const questionRetrying = questionSend?.phase === 'retrying' && pendingAskUserQuestion?.status === 'submitted';
  // A question the person moved past by sending a new message is skipped, not waiting (UXINT-5 D6).
  const questionPending =
    askQuestionMatchesRow && (pendingAskUserQuestion?.status === 'pending' || questionRetrying) && isLastMessage;
  const interactionWaiting = useChatStore((s) =>
    activeSlotId ? s.slots[activeSlotId]?.interactionWaiting ?? null : null
  );
  const paused = isStreaming && interactionWaiting?.paused === true;
  const queuedSteers = useChatStore((s) => (activeSlotId ? s.slots[activeSlotId]?.queuedSteers : undefined));
  const isOwner = useChatStore((s) => (activeSlotId ? s.slots[activeSlotId]?.isOwner : undefined));

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

  const tabIdBase = useId();
  // Also ensure we reset if the slot switches to a different conversation
  // (activeExpandedMessageId becomes null on slot evict/init).
  const activeTab = (activeExpandedMessageId === messageId || !messageId)
    ? localTab
    : 'answer';

  const setActiveTab = useCallback((tab: ResponseTab) => {
    setLocalTab(tab);
    if (!activeSlotId) return;
    if (tab === 'answer') {
      // Clear expansion only if we own it
      updateSlot(activeSlotId, { activeExpandedMessageId: null });
    } else {
      updateSlot(activeSlotId, { activeExpandedMessageId: messageId ?? null });
    }
  }, [activeSlotId, messageId, updateSlot]);

  const setInlineCitationKey = useInlineCitationPopoverStore((s) => s.setActiveKey);
  const prevLocalTabForCite = useRef<ResponseTab>('answer');
  useEffect(() => {
    if (prevLocalTabForCite.current === 'answer' && localTab !== 'answer') {
      setInlineCitationKey(null);
    }
    prevLocalTabForCite.current = localTab;
  }, [localTab, setInlineCitationKey]);

  // Merge streaming citations when streaming, fall back to metadata citations
  const storedCitationMaps = isStreaming && streamingCitationMaps
    ? streamingCitationMaps
    : citationMaps;
  // Use streaming content when streaming, otherwise use the final answer.
  // Apply structural repair to in-progress content only — the final message
  // from the server is always complete and must not be patched.
  // The answer's one citation rule (`citedAnswer`, the job reply's too) turns its
  // links into `[N]` chips with a source behind each: a page it links that no stored
  // citation covers (an engine's, repeated here) becomes a source of its own, and the
  // tabs, popover and copy read the same maps as the chips.
  // Memoized: these are whole-string regex passes, and `ChatResponse` can
  // re-render for reasons unrelated to content (tab switch, citation
  // arrival) — recomputing on every such render defeats the point of
  // memoizing further down the tree in `AnswerContent`.
  const lastCitedRef = useRef<CitedAnswer | undefined>(undefined);
  const cited = useMemo(() => {
    const streamingNow = Boolean(isStreaming && streamingContent);
    const next = citedAnswer(
      streamingNow ? repairStreamingMarkdown(streamingContent) : answer,
      storedCitationMaps,
      { streaming: streamingNow, previous: lastCitedRef.current },
    );
    lastCitedRef.current = next;
    return next;
  }, [isStreaming, streamingContent, answer, storedCitationMaps]);
  const processedContent = cited.text;
  const effectiveCitationMaps = cited.citationMaps;
  // Read via ref (not a `useMemo` dep) by `wrappedCallbacks` below, so that
  // object stays referentially stable across citation-only updates.
  const effectiveCitationMapsRef = useRef(effectiveCitationMaps);
  effectiveCitationMapsRef.current = effectiveCitationMaps;

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
  const cutShort = cutShortOf(truncatedBy, persistedParts);
  const sharedBudget = budgetScopeOf(cutShort, budgetScope, persistedParts);
  // WP-89: the run's settled outcome, worded here; an answer saved before it carries its own English words.
  const settledOutcome = runOutcomeOf(undefined, persistedParts);

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
  // ANSWER-CONTRACT-FU-2: the subject was not found; the chat words that above the answer, which then starts after
  // the English line saved for surfaces with no UI.
  const notFound = isStreaming ? null : notFoundOf(persistedParts, savedNotFound);
  const answerOwn = hasApprovalCard ? withoutWaitingLine(visibleAnswer) : visibleAnswer;
  // R32REPO-FU-1: what the run could not do is worded the same way, after the not-found line.
  const couldNotDo = isStreaming ? null : couldNotDoOf(persistedParts, savedLimits);
  const afterNotFound = notFound !== null ? withoutNotFoundLine(answerOwn, notFound) : answerOwn;
  const answerShown = couldNotDo !== null ? withoutLeadLine(afterNotFound, couldNotDo.line) : afterNotFound;
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
    !columnMode &&
    isLastMessage &&
      !isStreaming &&
      messageId &&
      activeSlotId &&
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
  // BGUI-2: the turn's jobs are drawn once, after its steps (`TurnJobs`); the steps leave them out.
  const isJob = useIsJobPart();
  const jobParts = useMemo(() => visibleActivityParts.filter(isJob), [visibleActivityParts, isJob]);
  const jobAnchors = useMemo(() => new Set(jobParts.flatMap(partAnchors)), [jobParts]);
  const hasTrail = visibleActivityParts.length > jobParts.length;
  // BGUI-6: a job's file the answer's own file cards show is drawn once, there.
  const shownFiles = useMemo(
    () => new Set(effectiveArtifacts.flatMap((a) => [a.fileName, ...(a.recordId ? [a.recordId] : [])])),
    [effectiveArtifacts],
  );

  // UXINT-1/2: what the reply asks of the person, saved and live through one list, each at the row that raised
  // it, in the order the run asked; the first open one is the call to action, the rest wait their turn.
  const liveApprovals = isStreaming ? pendingApprovals : undefined;
  const approvalGroups = useMemo(
    () => groupApprovals(mergeApprovals(savedApprovals(effectiveParts), liveApprovals ?? [])),
    [effectiveParts, liveApprovals],
  );
  const rowOfAnchor = useMemo(() => {
    const rows = new Map<string, number>();
    visibleActivityParts.forEach((part, index) => {
      for (const id of partAnchors(part)) if (!rows.has(id)) rows.set(id, index);
    });
    return rows;
  }, [visibleActivityParts]);
  // A re-run with changed answers streams only the reply to them: its card shows the answers it runs with.
  const questionPayload =
    (askQuestionMatchesRow ? pendingAskUserQuestion?.payload : persistedAskUserQuestion) ?? rerunAnswers?.payload;
  const questionAnswers = useMemo(
    () => (askQuestionMatchesRow ? pendingAskUserQuestion?.answers : persistedAskUserQuestionAnswers) ?? {},
    [askQuestionMatchesRow, pendingAskUserQuestion?.answers, persistedAskUserQuestionAnswers],
  );
  const questionState: 'pending' | 'answered' | 'skipped' | null = !questionPayload
    ? null
    : rerunAnswers
      ? 'answered'
      : questionPending
      ? 'pending'
      : Object.keys(questionAnswers).length > 0 && pendingAskUserQuestion?.status !== 'skipped'
        ? 'answered'
        : 'skipped';
  // ANSWER-CONTRACT-FU-5: a question is not an answer. A row whose turn ended on its card (still open, skipped by a
  // new message, or replied to in the chat) shows no confidence; an answered card's row shows its answer's.
  const endedOnQuestion =
    questionState === 'pending' || questionState === 'skipped' || (questionState !== null && Boolean(questionPayload?.repliedInChat));
  const interactions = useMemo(() => {
    type Placed = PlacedInteraction & ({ kind: 'approval'; group: ApprovalGroup } | { kind: 'question' });
    const placed: Placed[] = approvalGroups.map((group) => ({
      kind: 'approval' as const,
      group,
      key: group.key,
      anchor: approvalAnchor(group.items[0]),
      open: isOpenGroup(group),
      seq: group.items[0].seq ?? Number.MAX_SAFE_INTEGER,
    }));
    if (questionState) {
      placed.push({ kind: 'question', key: 'question', anchor: questionAnchor(effectiveParts), open: questionState === 'pending', seq: -1 });
    }
    return orderInteractions(placed, (anchor) => rowOfAnchor.get(anchor));
  }, [approvalGroups, questionState, effectiveParts, rowOfAnchor]);
  // BGUI-4: a job's ask the card over the message box holds is answered there; the transcript keeps a pointer to it,
  // so it is not this reply's call to action and the root run's own ask stays answerable at its row.
  const floated = useFloatedAsks();
  const isFloated = (item: (typeof interactions)[number]): boolean => item.kind === 'approval' && floatsGroup(item.group, floated);
  const activeKey = activeInteractionKey(interactions.filter((item) => !isFloated(item)));
  const waitingCount = interactions.reduce(
    (sum, item) => sum + (!item.open ? 0 : item.kind === 'approval' ? openCalls(item.group) : 1),
    0,
  );
  // WP-64: one step waiting on a connect reads as a plain line in the steps' header, naming the service.
  const openConnects = interactions.flatMap((item) =>
    item.open && item.kind === 'approval' ? item.group.items.filter((card) => card.connect && card.status === 'pending') : [],
  );
  // WP-97: how long the oldest open card has waited, so a run held for hours says so.
  const waitingFrom = waitingSince(
    interactions.flatMap((item) => (item.open && item.kind === 'approval' ? item.group.items : [])),
  );
  const waitingLine =
    waitingCount === 1 && openConnects.length === 1 && openConnects[0].connect
      ? connectWaitingLine(t, openConnects[0].connect, isOwner !== false)
      : undefined;
  const waitingAnchors = useMemo(
    () => new Set(interactions.flatMap((item) => (item.open && item.anchor !== null ? [item.anchor] : []))),
    [interactions],
  );
  // One "Files changed" block for an engine's workspace diffs and a loop run's run_code outputs alike (CTL-4).
  const changedFiles = useMemo(() => {
    const fromEngine = changedFilesFromEngine((effectiveParts ?? []).flatMap((part) => (part.type === 'engine_task' ? (part.frames ?? []) : [])));
    const covered = new Set(fromEngine.map((file) => file.artifactId));
    return [...fromEngine, ...changedFilesFromArtifacts(effectiveArtifacts).filter((file) => !covered.has(file.artifactId))];
  }, [effectiveParts, effectiveArtifacts]);
  const openChangedFile = useCallback(
    (file: ChangedFile) => {
      const artifact = effectiveArtifacts.find((candidate) => candidate.recordId === file.artifactId);
      if (artifact) void loadArtifactPreview(artifact);
    },
    [effectiveArtifacts, loadArtifactPreview],
  );
  const messageRun = useMessageRun(runId, isLastMessage);
  // C10.05: the live cost beside the elapsed time in the steps' header, while the run works.
  const liveCost = useLiveRunCost(messageRun, isStreaming);
  const plan = useChatStore((s) => (activeSlotId ? (s.slots[activeSlotId]?.plan ?? null) : null));
  // The slot holds the plan of the run it streamed last; it belongs to this answer only if that run wrote it.
  const savedPlan = useMemo(() => planFromParts(effectiveParts), [effectiveParts]);
  const messagePlan = (plan && messageRun && plan.runId === messageRun.runId ? plan : null) ?? savedPlan;
  const agentDisplayName = useChatStore((s) => s.agentContextDisplayName);
  const agentVersion = useRunAgentVersion(messageRun, !isStreaming);
  const viewActivity = messageRun && !unanswered ? (
    <Flex as="span" display="inline-flex" align="center" gap="2" wrap="wrap">
      <RunVersionLabel agentName={agentDisplayName} version={agentVersion} />
      <ViewActivityButton run={messageRun} isMobile={isMobile} onClosePreview={clearPreview} />
    </Flex>
  ) : null;

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

  const decide = useCallback(
    (
      item: Parameters<typeof decideApproval>[1],
      decision: Parameters<typeof decideApproval>[2],
      scope?: Parameters<typeof decideApproval>[3],
      reason?: string,
    ) => {
      const sid = activeSlotId;
      if (sid) void decideApproval(sid, item, decision, scope ?? 'once', reason);
    },
    [activeSlotId],
  );
  const decideEdited = useCallback(
    (item: Parameters<typeof decideApproval>[1], edits: Record<string, unknown>) => {
      const sid = activeSlotId;
      if (sid) void decideApproval(sid, item, 'approved', 'once', undefined, edits);
    },
    [activeSlotId],
  );
  const decideAll = useCallback(
    (
      items: Parameters<typeof decideApprovals>[1],
      decision: Parameters<typeof decideApprovals>[2],
      scope?: Parameters<typeof decideApprovals>[3],
      reason?: string,
    ) => {
      const sid = activeSlotId;
      if (sid) void decideApprovals(sid, items, decision, scope ?? 'once', reason);
    },
    [activeSlotId],
  );

  const renderQuestion = (state: InteractionState) => {
    if (!questionPayload) return null;
    if (rerunCard) return rerunCard;
    if (changingAnswers) return changeAnswersEditor;
    if (questionState !== 'pending' && questionPayload.repliedInChat) {
      return <InteractionReceipt tone="muted" status="replied" text={t('askUserQuestion.repliedInChat')} />;
    }
    if (questionState === 'skipped') {
      return <InteractionReceipt tone="muted" status="skipped" text={t('interaction.receipt.skipped')} />;
    }
    if (questionState === 'answered') {
      const answer = answerSummary(
        questionPayload, questionAnswers, t('askUserQuestion.somethingElse'), t('askUserQuestion.noPreference'),
        cardWords(questionPayload, t).label,
      );
      return <InteractionReceipt tone="ok" status="answered" text={t('interaction.receipt.answered', { answer })} />;
    }
    if (state === 'queued') {
      const first = questionPayload.questions[0]?.question;
      return <QueuedInteraction title={first !== undefined ? cardWords(questionPayload, t).question(first) : t('askUserQuestion.quickQuestionSingular')} />;
    }
    return (
      <Flex direction="column" gap="2">
      <AskUserQuestionCard
        payload={questionPayload}
        initialAnswers={questionAnswers}
        status="pending"
        sending={questionRetrying}
        active={state === 'active' && !questionRetrying}
        sentAnswers={pendingAskUserQuestion?.sentAnswers}
        onAnswersChange={(nextAnswers) => {
          const sid = activeSlotId;
          const p = sid ? useChatStore.getState().slots[sid]?.pendingAskUserQuestion : null;
          if (!sid || !p) return;
          useChatStore.getState().updateSlot(sid, {
            pendingAskUserQuestion: { ...p, answers: nextAnswers },
          });
        }}
        onSubmit={(message, nextAnswers) => {
          if (activeSlotId) void answerQuestionCard(activeSlotId, message, nextAnswers);
        }}
      />
      <CardSendNotice sendKey={questionSendKeyOfRow} refusalText={(code) => admissionRefusalText(code ?? 'FAILED')} />
      </Flex>
    );
  };

  const anchoredInteractions = new Map<string, React.ReactNode[]>();
  const looseInteractions: React.ReactNode[] = [];
  const leadingInteractions: React.ReactNode[] = [];
  for (const item of interactions) {
    const state: InteractionState = !item.open ? 'done' : item.key === activeKey ? 'active' : 'queued';
    const node = (
      <Box key={item.key} data-testid="interaction" data-interaction-key={item.key} data-state={state}>
        {item.kind === 'question' ? (
          renderQuestion(state)
        ) : isFloated(item) ? (
          <FloatedAskPointer askKey={pointerKey(item.group) ?? item.key} />
        ) : (
          <ApprovalInteraction
            group={item.group}
            state={state}
            onDecide={decide}
            onDecideAll={decideAll}
            onApproveEdited={decideEdited}
            authority={approvalAuthority}
            conversationChoice
            subAgent={item.group.items[0].childRunId !== undefined}
            paused={paused}
            isOwner={isOwner !== false}
          />
        )}
      </Box>
    );
    if (item.anchor !== null && rowOfAnchor.has(item.anchor)) {
      anchoredInteractions.set(item.anchor, [...(anchoredInteractions.get(item.anchor) ?? []), node]);
    } else if (item.kind === 'question' && rerunAnswers) {
      // A re-run with changed answers streams only the work after them, so its card leads that work.
      leadingInteractions.push(node);
    } else looseInteractions.push(node);
  }
  // UXINT-5: someone the conversation is shared with sees that the run waits on its owner, never what for.
  if (isStreaming && isOwner === false && waitingCount === 0 && (interactionWaiting?.count ?? 0) > 0) {
    looseInteractions.push(
      <Box key="waiting-owner" data-testid="interaction-waiting-owner">
        <InteractionReceipt tone="muted" status="waiting" text={t('interaction.waitingForOwner', { count: interactionWaiting?.count ?? 0 })} />
      </Box>,
    );
  }
  // UXINT-5: a message sent to the running task shows at once, queued, until the run reads it (`user_input`).
  const read = new Set((effectiveParts ?? []).filter((part) => part.type === 'user_input').map((part) => part.messageSeq));
  for (const steer of (isStreaming ? queuedSteers : undefined) ?? []) {
    if (read.has(steer.messageSeq)) continue;
    looseInteractions.push(
      <SteeringMessage
        key={`steer-${steer.messageSeq}`}
        part={{ type: 'user_input', content: steer.text }}
        queuedNote={t(waitingCount > 0 ? 'interaction.steerQueuedWaiting' : 'interaction.steerQueued')}
      />,
    );
  }
  // A job's asks are drawn on its card, after the steps: only the others keep the steps open for themselves.
  const trailInteractions = interactions.filter((item) => item.anchor === null || !jobAnchors.has(item.anchor));
  const showTimeline =
    effectiveParts !== undefined &&
    (hasTrail || (isStreaming && multiStep) || (messagePlan?.steps.length ?? 0) >= 2 || trailInteractions.length > 0);
  const renderTabContent = () => {
    switch (activeTab) {
      case 'answer':
        return (
          <Box style={{ padding: 'var(--space-4) 0' }}>
            {/* Confidence only with a finished answer, never on a question */}
            {!isStreaming && confidence && !endedOnQuestion && <ConfidenceIndicator confidence={confidence} />}

            {/* Agent activity timeline — thinking / tool calls / sub-agents,
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
                produced a visible part yet. Shown on ask_user_question
                rows too — the card is the question UI, not a reason to
                hide the tools that ran before it. */}
            <ContextCompactedRow parts={effectiveParts} run={messageRun} />
            <RunNotices parts={effectiveParts} />
            {leadingInteractions.length > 0 ? <Box mb="3">{leadingInteractions}</Box> : null}
            {showTimeline && effectiveParts && (
              <CollapsibleActivitySection
                parts={effectiveParts}
                isStreaming={isStreaming}
                plan={messagePlan}
                trailing={viewActivity}
                startedAt={createdAt}
                files={changedFiles}
                waiting={waitingCount}
                waitingLine={waitingLine}
                waitingSince={waitingFrom}
                paused={paused}
                defaultOpen={approvalGroups.some((group) => decidedRuns.has(group.items[0].runId ?? ''))}
                cost={liveCost}
              >
                <AgentActivityTimeline
                  parts={effectiveParts}
                  liftJobs
                  isStreaming={isStreaming}
                  // non-multiStep: null prevents duplicate status with StatusMessageComponent below
                  currentStatus={multiStep && !paused ? streamingStatusToShow : null}
                  citationMaps={effectiveCitationMaps}
                  citationCallbacks={wrappedCallbacks}
                  plan={messagePlan}
                  anchored={anchoredInteractions}
                  waiting={waitingAnchors}
                  unanchored={looseInteractions.length > 0 ? looseInteractions : null}
                />
                <FilesChanged files={changedFiles} onOpen={openChangedFile} />
                {!isStreaming ? <RunSummary run={messageRun} /> : null}
                {!isStreaming ? <OutsideAccess run={messageRun} /> : null}
              </CollapsibleActivitySection>
            )}
            <TurnJobs
              jobs={jobParts}
              anchored={anchoredInteractions}
              waiting={waitingAnchors}
              citationMaps={effectiveCitationMaps}
              citationCallbacks={wrappedCallbacks}
              shownFiles={shownFiles}
            />
            {/* An answer with no steps to fold still offers its run's detail (an engine run's commands). */}
            {!isStreaming && !showTimeline && viewActivity}
            {!isStreaming && messageRun ? <RunCommandActions question={question} answer={answer} run={messageRun} /> : null}
            {!isStreaming && isLastMessage && messageRun ? <CloneUnavailableCards run={messageRun} /> : null}

            {/* Separator marking the handoff from "here's the work" to
                "here's the answer" — only when there's activity actually
                visible above (not just a lone isFinal part that the
                timeline itself filtered out) and content to show below
                (answer text or the ask_user_question card). */}
            {hasVisibleActivity && (answerShown || toolApproval || approvalReceipts.length > 0) && (
              <Box
                className="agent-activity-enter"
                style={{
                  borderTop: '1px solid var(--slate-4)',
                  marginTop: 'var(--space-1)',
                  marginBottom: 'var(--space-3)',
                }}
              />
            )}

            {/* No timeline to anchor on (a message saved before parts were kept): the interactions in order. */}
            {!showTimeline && interactions.length > 0 ? (
              <Box mb="3">
                {[...anchoredInteractions.entries()].flatMap(([anchor, nodes]) => (jobAnchors.has(anchor) ? [] : nodes)).concat(looseInteractions)}
              </Box>
            ) : null}

            {/* A reply that failed because an MCP server isn't set up shows how to set it up instead. */}
            {mcpConnectDetails && (
              <McpConnectRequiredCard details={mcpConnectDetails} question={question} />
            )}

            {/* Continuation / final answer — below the question card so the
                turn reads tools → question → answer. Hidden while the card
                is still waiting for answers. */}
            {approvalReceipts.length > 0 && (
              <Box mb="3">
                <ToolApprovalReceipts items={approvalReceipts} />
              </Box>
            )}

            {notFound !== null && !questionPending && !mcpConnectDetails && (
              <Box mt={persistedAskUserQuestion || askQuestionMatchesRow ? '4' : undefined}>
                <NotFoundNotice fact={notFound} />
              </Box>
            )}

            {couldNotDo !== null && !questionPending && !mcpConnectDetails && (
              <Box mt={notFound === null && (persistedAskUserQuestion || askQuestionMatchesRow) ? '4' : undefined}>
                <CouldNotDoNotice fact={couldNotDo} />
              </Box>
            )}

            {answerShown && !questionPending && !mcpConnectDetails && (
              <Box
                data-testid="chat-answer"
                style={{
                  marginTop:
                    persistedAskUserQuestion || askQuestionMatchesRow
                      ? 'var(--space-4)'
                      : undefined,
                }}
              >
                {answerFailed ? (
                  <Callout.Root color="red" variant="soft" size="1" role="status" data-testid="answer-failed">
                    <Callout.Icon aria-hidden>
                      <MaterialIcon name="error" size={16} />
                    </Callout.Icon>
                    <Callout.Text size="2" style={{ lineHeight: 1.45 }}>
                      {answerShown}
                    </Callout.Text>
                  </Callout.Root>
                ) : (
                  <QuoteSelectionLayer enabled={!isStreaming && isChatOwner}>
                    <AnswerContent
                      content={answerShown}
                      citationMaps={effectiveCitationMaps}
                      citationCallbacks={wrappedCallbacks}
                      isStreaming={isStreaming}
                    />
                  </QuoteSelectionLayer>
                )}
              </Box>
            )}

            {/* G-05: whether the answer's checks passed, under it; it opens to every check. */}
            {!isStreaming && answerShown ? <VerificationPanel parts={effectiveParts} /> : null}

            {!isStreaming && searchDegraded && <SearchDegradedNote />}
            {!isStreaming && <AccessNotices notices={accessNotices} />}

            {/* UX-06: a typed error's one-click next step, under its words; only the newest reply offers it. */}
            {!isStreaming && isLastMessage && !mcpConnectDetails && (
              <ErrorNextStep error={streamError} question={question} />
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
                />
              </Box>
            )}

            {signInServers.length > 0 && (
              <Box mt="3">
                <McpSignInCard servers={signInServers} />
              </Box>
            )}

            {/* WP-89: the run's outcome in the reader's language, with the action that fits; it may be the whole
                answer (a file the run did not write). */}
            {!isStreaming && settledOutcome !== null && !questionPending && (
              <OutcomeNotice outcome={settledOutcome} budgetScope={sharedBudget} actionable={isLastMessage} />
            )}
            {/* BUDGETEND: a limit cut the run's work short; the answer is what it gathered. An answer saved with the
                outcome's English words already says so. */}
            {!isStreaming && settledOutcome === null && cutShort !== null && answerShown && !questionPending &&
              !hasLegacyOutcomeWords(answerShown) && <CutShortNotice reason={cutShort} budgetScope={sharedBudget} />}

            {/* Subtle marker for a response cut short by Stop. Only shown once
                settled (never while still streaming/regenerating) — regenerate
                remains available via MessageActions below, same as any other
                completed response. */}
            {showsStoppedMarker({
              status,
              isStreaming,
              hasQuestionCard,
              hasAnswerText: visibleAnswer.trim() !== '',
              repliedInChat: Boolean(persistedAskUserQuestion?.repliedInChat),
            }) && (
              <Flex
                align="center"
                gap="1"
                data-testid="chat-stopped-marker"
                style={{ marginTop: 'var(--space-2)' }}
              >
                <MaterialIcon name="stop_circle" size={14} color="var(--slate-11)" />
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {t('chat.stoppedMarker', { defaultValue: 'Stopped' })}
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

            <MemoryChip parts={effectiveParts} />

            {/* Artifacts generated by sandbox tools */}
            {effectiveArtifacts.length > 0 && !questionPending && (
              <ArtifactsPanel
                artifacts={effectiveArtifacts}
                latestArtifactVersions={latestArtifactVersions}
                onPreview={loadArtifactPreview}
                onViewSource={async (codeArtifactId) => {
                  // The code artifact is registered through the same pipeline as any
                  // other record, so it streams/previews via the standard KB record
                  // APIs exactly like the `recordId` path above — no separate endpoint.
                  try {
                    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
                    const [details, blob] = await Promise.all([
                      KnowledgeBaseApi.getRecordDetails(codeArtifactId, { suppressErrorToast: isArtifactGone }),
                      KnowledgeBaseApi.streamRecord(codeArtifactId, { suppressErrorToast: isArtifactGone }),
                    ]);
                    const objectUrl = URL.createObjectURL(blob);
                    useChatStore.getState().setPreviewFile({
                      id: codeArtifactId,
                      url: objectUrl,
                      name: details.record.recordName,
                      type: details.record.mimeType || 'text/plain',
                      size: details.record.fileRecord?.sizeInBytes ?? blob.size,
                      hideFileDetails: true,
                      showDownload: true,
                    });
                  } catch (error) {
                    // The card hides "view source" once the source is gone.
                    if (isArtifactGone(error)) throw error;
                  }
                }}
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
  const canEditQuestion =
    isChatOwner &&
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
    });
  }, [messageId, question, isStreaming, appliedFilters, attachments]);

  // While this row's question card waits for an answer, read aloud and copy the
  // question and its options. Once answered, the reply written after it is the content.
  const speakContent = questionPending && pendingAskUserQuestion
    ? buildQuestionCardReadAloudText(pendingAskUserQuestion?.payload, cardWords(pendingAskUserQuestion?.payload, t))
    : visibleAnswer;

  const appliedNodes = mentionedNodes(appliedFilters);

  const shell = (
    <Box style={{ width: '100%' }}>
      {!columnMode && (<>
      <Box
        style={{
          marginBottom:
            (collections && collections.length > 0) ||
            hasAnyFilter(appliedFilters)
              ? 'var(--space-3)'
              : 'var(--space-4)',
        }}
      >
        <ExpandableUserQuery
          question={question}
          isMobile={isMobile}
          messageId={messageId}
          isStreaming={isStreaming}
          onEdit={canEditQuestion ? handleEditQuery : undefined}
          command={command}
          mentions={appliedNodes}
        />
        {createdAt && (
          <Text
            size="1"
            style={{
              color: 'var(--slate-11)',
              marginTop: 'var(--space-1)',
              display: 'block',
            }}
          >
            {formatMessageTime(createdAt)}
          </Text>
        )}
      </Box>

      {/* Applied filter chips — shown when connector/KB filters were scoped on this query */}
      {appliedFilters && hasAnyFilter(appliedFilters) && (
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
          {attachments.map((att) => (
            <AttachmentChip
              key={att.virtualRecordId || att.recordId}
              attachment={att}
              onOpen={(opened) =>
                isPastedTextAttachment(opened) ? setTextPreviewAttachment(opened) : handleAttachmentPreview(opened)
              }
            />
          ))}
        </Flex>
      )}
      </>)}

      {/* Tabs */}
      {/* Tabs — hide Sources/Citations counts when the ask_user_question card
          (streaming or persisted) owns this row; those tabs reflect answer
          chunks that are suppressed. */}
      {!unanswered && !answerHidden && (
        <ResponseTabs
          activeTab={activeTab}
          onTabChange={setActiveTab}
          idBase={tabIdBase}
          sourcesCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : sourcesCount}
          citationCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : citationCount}
        />
      )}

      {/* Tab Content */}
      {!unanswered && !answerHidden && (
        <div role="tabpanel" id={responseTabIds(tabIdBase, activeTab).panel} aria-labelledby={responseTabIds(tabIdBase, activeTab).tab}>
          {renderTabContent()}
        </div>
      )}

      {/* The answer is withheld (see `answerHidden`): say why, and offer to ask again with today's access. */}
      {!unanswered && answerHidden && (
        <Flex
          data-testid="answer-hidden"
          align="center"
          justify="between"
          gap="3"
          wrap="wrap"
          style={{
            padding: 'var(--space-3)',
            border: '1px solid var(--slate-5)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-a2)',
          }}
        >
          <Text size="2" color="gray">
            {answerHidden.reason === 'access_check_unavailable'
              ? t('chat.answerHidden.unconfirmed')
              : t('chat.answerHidden.lost')}
          </Text>
          {isChatOwner && !columnMode && (
            <Button size="1" variant="soft" onClick={askAgain}>
              {t('chat.answerHidden.askAgain')}
            </Button>
          )}
        </Flex>
      )}

      {/* Message Actions (feedback, copy, regenerate, model info) */}
      {!unanswered && !answerHidden && !columnMode && activeTab === 'answer' && (
        <MessageActions
          content={speakContent}
          citationMaps={effectiveCitationMaps}
          // A /run answer is the command's exit status: no model ran, so none is named.
          modelInfo={sentRunLine(question) === null ? modelInfo : undefined}
          isStreaming={isStreaming}
          messageId={messageId}
          question={question}
          isLastMessage={isLastMessage && !questionPending}
          appliedFilters={appliedFilters}
          feedbackInfo={feedbackInfo}
          canRegenerate={!ranApprovedCall}
          runLinked={runId !== undefined}
          parts={effectiveParts}
        />
      )}

      {/* Pasted-text attachment preview — read-only (this message already sent) */}
      {textPreviewAttachment && (
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
      )}
    </Box>
  );

  if (citationMessageRowKey) {
    return (
      <CitationMessageRowKeyContext.Provider value={citationMessageRowKey}>
        {shell}
      </CitationMessageRowKeyContext.Provider>
    );
  }
  return shell;
});

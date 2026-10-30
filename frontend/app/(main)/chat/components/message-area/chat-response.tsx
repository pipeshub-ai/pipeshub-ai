'use client';

import React, { useState, useMemo, useRef, useCallback, useEffect } from 'react';
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
import { ArtifactsPanel, isArtifactGone } from './artifacts-panel';
import {
  AskUserQuestionCard,
  askUserQuestionOwnsRow,
  showsStoppedMarker,
  buildResumeMessage,
  isAskUserQuestionExpired,
} from './ask-user-question-card';
import { McpConnectRequiredCard } from './mcp-connect-required-card';
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
import { mcpConfigMissingDetails, type ChatStreamErrorInfo } from '../../stream-error';
import { AgentActivityTimeline, CollapsibleActivitySection, getVisibleRootParts, hasMultiStepActivity } from './agent-activity';
import { ExpandableUserQuery } from './expandable-user-query';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useCommandStore } from '@/lib/store/command-store';
import { useChatStore } from '../../store';
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
import { extractAnswerMarkers, isSignedUrl, isTrustedApiUrl } from '../../utils/parse-download-markers';
import { isAskUserQuestionTool, splitPartsAtQuestion } from '../../utils/tool-display';
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

interface ChatResponseProps {
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
  /** Persisted ask_user_question payload from a historical tool_call — renders read-only question card */
  persistedAskUserQuestion?: AskUserQuestionPayload;
  persistedAskUserQuestionAnswers?: Record<string, AskUserQuestionAnswer>;
  /** Persisted feedback value from the backend — initialises the like/dislike button state */
  feedbackInfo?: { value?: 'like' | 'dislike' };
  /** Set when this response was cut short by a user-initiated Stop (see `IMessage.status`, Node). */
  status?: 'stopped';
  /** The server's error code and details when this reply is a failed run. */
  streamError?: ChatStreamErrorInfo;
  /**
   * No assistant row exists for this question (see `buildMessagePairs`). The
   * question is drawn on its own: an answer area, tabs and message actions
   * would all be empty controls over a reply that was never produced.
   */
  unanswered?: boolean;
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
  unanswered = false,
}: ChatResponseProps) {
  debugLog.tick('[chat] [ChatResponse]');
  const { t } = useTranslation();
  const mcpConnectDetails = isStreaming ? null : mcpConfigMissingDetails(streamError);
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
  const activeExpandedMessageId = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.activeExpandedMessageId ?? null : null
  );
  const updateSlot = useChatStore((s) => s.updateSlot);
  const activeSlotId = useChatStore((s) => s.activeSlotId);
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
          const streamAsPdf =
            isPresentationFile(artifact.mimeType, artifact.fileName) ||
            isLegacyWordDocFile(artifact.mimeType, artifact.fileName);
          const blob = await KnowledgeBaseApi.streamRecord(recordId, {
            ...(streamAsPdf ? { convertTo: 'application/pdf' } : {}),
            ...(version !== undefined ? { version } : {}),
            // The card shows a deleted artifact itself; a version switch has no card to tell.
            ...(isSwitch ? {} : { suppressErrorToast: isArtifactGone }),
          });
          const resolvedType = resolvePreviewMimeAfterStream(
            artifact.mimeType,
            artifact.fileName,
            blob,
            streamAsPdf,
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
    s.activeSlotId ? s.slots[s.activeSlotId]?.pendingAskUserQuestion ?? null : null
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
  // Collapse control is offered once narration/answer has started (or for
  // completed messages). While only thinking/tools are streaming, keep the
  // timeline always-visible with no collapse chrome.
  const canCollapseActivity =
    !isStreaming ||
    !!displayContent ||
    visibleActivityParts.some((p) => p.type === 'text' && !!p.settled && !p.isFinal);

  // With a question card on the row, the work done after it was answered renders
  // under the card instead of above it.
  const showPersistedCard = Boolean(persistedAskUserQuestion) && !askQuestionMatchesRow;
  const showPendingCard = askQuestionMatchesRow && Boolean(pendingAskUserQuestion);
  const questionSplit = useMemo(() => {
    if (!rerunAnswers && !showPersistedCard && !showPendingCard) return null;
    const split = splitPartsAtQuestion(effectiveParts);
    // A re-run with changed answers streams only the reply to them, so all of it comes after the card.
    if (!split && rerunAnswers && effectiveParts) return { before: [], after: effectiveParts };
    return split;
  }, [effectiveParts, rerunAnswers, showPersistedCard, showPendingCard]);
  const partsAboveCard = questionSplit ? questionSplit.before : effectiveParts;
  const partsBelowCard = questionSplit?.after ?? [];
  // Once the card is answered the run carries on below it, so the live status goes there too.
  const activityContinuesBelowCard = Boolean(questionSplit) && !questionPending;
  const visiblePartsAboveCard = useMemo(
    () => (questionSplit ? getVisibleRootParts(questionSplit.before, isStreaming) : visibleActivityParts),
    [questionSplit, isStreaming, visibleActivityParts],
  );
  const visiblePartsBelowCard = useMemo(
    () => (questionSplit ? getVisibleRootParts(questionSplit.after, isStreaming) : []),
    [questionSplit, isStreaming],
  );
  const hasVisibleActivityBelowCard = visiblePartsBelowCard.length > 0;
  const canCollapseActivityBelowCard =
    !isStreaming ||
    !!displayContent ||
    visiblePartsBelowCard.some((p) => p.type === 'text' && !!p.settled && !p.isFinal);

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
      <CollapsibleActivitySection parts={parts} isStreaming={isStreaming}>
        {timeline}
      </CollapsibleActivitySection>
    ) : (
      timeline
    );
  };

  const renderTabContent = () => {
    switch (activeTab) {
      case 'answer':
        return (
          <Box style={{ padding: 'var(--space-4) 0' }}>
            {/* Show confidence only when not streaming and has answer */}
            {!isStreaming && confidence && <ConfidenceIndicator confidence={confidence} />}

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
                hide the tools that ran before it. Only the work up to the
                question renders here; what ran after it was answered renders
                under the card. */}
            {partsAboveCard && (visiblePartsAboveCard.length > 0 || (isStreaming && multiStep && !activityContinuesBelowCard)) &&
              renderActivity(
                partsAboveCard,
                canCollapseActivity || activityContinuesBelowCard,
                // non-multiStep: null prevents duplicate status with StatusMessageComponent below
                multiStep && !activityContinuesBelowCard ? streamingStatusToShow : null,
              )}

            {/* Separator marking the handoff from "here's the work" to
                "here's the answer" — only when there's activity actually
                visible above (not just a lone isFinal part that the
                timeline itself filtered out) and content to show below
                (answer text or the ask_user_question card). */}
            {visiblePartsAboveCard.length > 0 && (questionSplit || answerShown || questionPending || persistedAskUserQuestion || toolApproval || approvalReceipts.length > 0) && (
              <Box
                className="agent-activity-enter"
                style={{
                  borderTop: '1px solid var(--slate-4)',
                  marginTop: 'var(--space-1)',
                  marginBottom: 'var(--space-3)',
                }}
              />
            )}

            {/* Persisted ask_user_question from historical tool_call — read-only display */}
            {rerunCard ? (
              rerunCard
            ) : persistedAskUserQuestion && !askQuestionMatchesRow ? (
              changingAnswers ? (
                changeAnswersEditor
              ) : (
                <AskUserQuestionCard
                  payload={persistedAskUserQuestion}
                  initialAnswers={persistedAskUserQuestionAnswers ?? {}}
                  status="persisted"
                />
              )
            ) : null}

            {/* Active (streaming/pending) ask_user_question card */}
            {rerunCard ? null : askQuestionMatchesRow && pendingAskUserQuestion && changingAnswers ? (
              changeAnswersEditor
            ) : askQuestionMatchesRow && pendingAskUserQuestion ? (
              <AskUserQuestionCard
                payload={pendingAskUserQuestion.payload}
                initialAnswers={pendingAskUserQuestion.answers}
                status={pendingAskUserQuestion.status}
                sentAnswers={pendingAskUserQuestion.sentAnswers}
                onAnswersChange={(nextAnswers) => {
                  const sid = useChatStore.getState().activeSlotId;
                  const p = sid ? useChatStore.getState().slots[sid]?.pendingAskUserQuestion : null;
                  if (!sid || !p) return;
                  useChatStore.getState().updateSlot(sid, {
                    pendingAskUserQuestion: { ...p, answers: nextAnswers },
                  });
                }}
                onSubmit={(message, nextAnswers) => {
                  const sid = useChatStore.getState().activeSlotId;
                  const p = sid ? useChatStore.getState().slots[sid]?.pendingAskUserQuestion : null;
                  if (!sid || !p) return;
                  useChatStore.getState().updateSlot(sid, {
                    pendingAskUserQuestion: { ...p, answers: nextAnswers, status: 'submitted' },
                  });
                  const request = buildStreamChatRequestForSlot(sid, message);
                  if (request) {
                    void streamMessageForSlot(sid, message, request, {
                      resumeAskUserQuestion: true,
                    });
                  }
                }}
              />
            ) : null}

            {/* Work done after the question was answered — under the card, Claude-style. */}
            {questionSplit && (hasVisibleActivityBelowCard || (isStreaming && multiStep && activityContinuesBelowCard)) && (
              <Box data-testid="activity-below-question-card" style={{ marginTop: 'var(--space-4)' }}>
                {renderActivity(
                  partsBelowCard,
                  canCollapseActivityBelowCard && hasVisibleActivityBelowCard,
                  multiStep ? streamingStatusToShow : null,
                )}
              </Box>
            )}
            {hasVisibleActivityBelowCard && (answerShown || toolApproval || approvalReceipts.length > 0) && (
              <Box
                className="agent-activity-enter"
                style={{
                  borderTop: '1px solid var(--slate-4)',
                  marginTop: 'var(--space-1)',
                  marginBottom: 'var(--space-3)',
                }}
              />
            )}

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

            {answerShown && !questionPending && !mcpConnectDetails && (
              <Box
                style={{
                  marginTop:
                    (persistedAskUserQuestion || askQuestionMatchesRow) && !hasVisibleActivityBelowCard
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
                <MaterialIcon name="stop_circle" size={14} color="var(--slate-9)" />
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
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
  // Someone a chat was shared with reads it; the server only lets its owner edit.
  const isChatOwner = useChatStore((st) =>
    st.activeSlotId ? st.slots[st.activeSlotId]?.isOwner !== false : true,
  );
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
    ? buildQuestionCardReadAloudText(pendingAskUserQuestion?.payload)
    : visibleAnswer;

  const appliedNodes = mentionedNodes(appliedFilters);

  const shell = (
    <Box style={{ width: '100%' }}>
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
          mentions={appliedNodes}
        />
        {createdAt && (
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

      {/* Tabs */}
      {/* Tabs — hide Sources/Citations counts when the ask_user_question card
          (streaming or persisted) owns this row; those tabs reflect answer
          chunks that are suppressed. */}
      {!unanswered && (
        <ResponseTabs
          activeTab={activeTab}
          onTabChange={setActiveTab}
          sourcesCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : sourcesCount}
          citationCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : citationCount}
        />
      )}

      {/* Tab Content */}
      {!unanswered && renderTabContent()}

      {/* Message Actions (feedback, copy, regenerate, model info) */}
      {!unanswered && activeTab === 'answer' && (
        <MessageActions
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

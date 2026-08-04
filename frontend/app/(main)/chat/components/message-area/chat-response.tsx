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
import { ArtifactsPanel } from './artifacts-panel';
import { AskUserQuestionCard } from './ask-user-question-card';
import { AgentActivityTimeline, CollapsibleActivitySection, getVisibleRootParts, hasMultiStepActivity } from './agent-activity';
import { ExpandableUserQuery } from './expandable-user-query';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useCommandStore } from '@/lib/store/command-store';
import { useChatStore } from '../../store';
import { debugLog } from '../../debug-logger';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { AskUserQuestionPayload, AttachmentRef, ConfidenceLevel, ModelInfo, StatusMessage, ResponseTab, ChatArtifact, AppliedFilters as AppliedFiltersData, MessagePart } from '../../types';
import { FileIcon } from '@/app/components/ui/file-icon';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { emptyCitationMaps } from './response-tabs/citations';
import { repairStreamingMarkdown } from '../../utils/repair-streaming-markdown';
import { processMarkdownContent } from '../../utils/process-markdown-content';
import { parseDownloadMarkers, parseArtifactMarkers } from '../../utils/parse-download-markers';
import { DownloadTasks } from './download-tasks';
import {
  isPresentationFile,
  isDocxFile,
  isLegacyWordDocFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
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
  /** Persisted feedback value from the backend — initialises the like/dislike button state */
  feedbackInfo?: { value?: 'like' | 'dislike' };
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
  feedbackInfo,
}: ChatResponseProps) {
  debugLog.tick('[chat] [ChatResponse]');
  const { t } = useTranslation();
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
    streamingCitationMaps, streamingParts, persistedParts, createdAt, persistedAskUserQuestion,
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
          const streamOptions = {
            ...(streamAsPdf ? { convertTo: 'application/pdf' } : {}),
            ...(version !== undefined ? { version } : {}),
          };
          const blob = await KnowledgeBaseApi.streamRecord(
            recordId,
            Object.keys(streamOptions).length > 0 ? streamOptions : undefined,
          );
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
        } catch {
          if (isSwitch) {
            // Keep showing the last successfully loaded version rather than
            // falling back to a stale/foreign URL below.
            const current = useChatStore.getState().previewFile;
            if (current?.id === recordId) setPreviewFile({ ...current, isSwitchingVersion: false });
            return;
          }
          // Initial-open failure — fall through to the URL-classification
          // fallback below (never trusts an arbitrary marker URL, see
          // `ArtifactsPanel`'s `handleDownload` docstring for the same rule).
        }
      }

      setPreviewFile({
        id: artifact.id,
        url: artifact.downloadUrl,
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
    [latestArtifactVersions, setPreviewFile],
  );

  const pendingAskUserQuestion = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.pendingAskUserQuestion ?? null : null
  );

  const askQuestionMatchesRow =
    Boolean(
      pendingAskUserQuestion &&
      citationMessageRowKey &&
      pendingAskUserQuestion.assistantMessageId === citationMessageRowKey
    );

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

  // Use streaming content when streaming, otherwise use the final answer.
  // Apply structural repair to in-progress content only — the final message
  // from the server is always complete and must not be patched.
  // Always strip backend citation links → `[N]` so `AnswerContent` can render chips.
  const processedContent = processMarkdownContent(
    isStreaming && streamingContent
      ? repairStreamingMarkdown(streamingContent)
      : answer,
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

  // Wrap citation callbacks so that onPreview always receives this message's
  // citationMaps — the panel needs all citations for the previewed record.
  const wrappedCallbacks = useMemo<CitationCallbacks | undefined>(() => {
    if (!citationCallbacks) return undefined;
    return {
      ...citationCallbacks,
      onPreview: citationCallbacks.onPreview
        ? (citation) => citationCallbacks.onPreview!(citation, effectiveCitationMaps)
        : undefined,
    };
  }, [citationCallbacks, effectiveCitationMaps]);

  // Derive counts from citation maps
  const sourcesCount = effectiveCitationMaps.sourcesOrder.length;
  const citationCount = Object.keys(effectiveCitationMaps.citationsOrder).length;

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
                AgentActivityTimeline's `currentStatus` prop. Once the
                response is complete, a multi-step transcript collapses
                behind a one-line summary so the reader's eye lands on the
                answer rather than the full ReAct trace on every reload.
                Gated on `hasVisibleActivity` (not raw part count) — a
                completed simple response's `effectiveParts` is just the one
                `isFinal` text part, which the timeline itself filters out;
                rendering `CollapsibleActivitySection` around that would show
                an expand chevron over a summary ("Worked on this") that
                reveals nothing on click. `isStreaming && multiStep` is
                OR'd in so the timeline (and its status entry) stays
                reachable while a multi-step run is active but hasn't
                produced a visible part yet (e.g. right after
                TEXT_MESSAGE_START, before the first token lands). */}
            {effectiveParts && (hasVisibleActivity || (isStreaming && multiStep)) && !askQuestionMatchesRow && !persistedAskUserQuestion && (
              isStreaming ? (
                <AgentActivityTimeline
                  parts={effectiveParts}
                  isStreaming
                  // Only the multi-step timeline owns the status entry — until a
                  // tool call/reasoning block proves this, the trailing part is
                  // just a placeholder (e.g. an empty text part from
                  // TEXT_MESSAGE_START) that gets filtered out of `visible`,
                  // which would otherwise leave the timeline showing ONLY the
                  // status while `StatusMessageComponent` below (gated on
                  // `!multiStep`) shows the exact same status a second time.
                  currentStatus={multiStep ? streamingStatusToShow : null}
                  citationMaps={effectiveCitationMaps}
                  citationCallbacks={wrappedCallbacks}
                />
              ) : (
                <CollapsibleActivitySection parts={effectiveParts}>
                  <AgentActivityTimeline
                    parts={effectiveParts}
                    isStreaming={false}
                    citationMaps={effectiveCitationMaps}
                    citationCallbacks={wrappedCallbacks}
                  />
                </CollapsibleActivitySection>
              )
            )}

            {/* Separator marking the handoff from "here's the work" to
                "here's the answer" — only when there's activity actually
                visible above (not just a lone isFinal part that the
                timeline itself filtered out) and content to show below. */}
            {hasVisibleActivity && displayContent && !askQuestionMatchesRow && !persistedAskUserQuestion && (
              <Box
                style={{
                  borderTop: '1px solid var(--slate-4)',
                  marginTop: 'var(--space-1)',
                  marginBottom: 'var(--space-3)',
                }}
              />
            )}

            {/* Show content - either streaming or final. Suppressed only
                when an ask_user_question card (streaming or persisted) owns
                this row so partial/final answer chunks aren't shown above
                the question card. The trailing text streams straight in
                here even for multi-step responses — see the `multiStep`
                comment above. */}
            {displayContent && !askQuestionMatchesRow && !persistedAskUserQuestion && (
              <AnswerContent
                content={displayContent}
                citationMaps={effectiveCitationMaps}
                citationCallbacks={wrappedCallbacks}
              />
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
            {downloadTasks.length > 0 && !askQuestionMatchesRow && !persistedAskUserQuestion && (
              <DownloadTasks tasks={downloadTasks} />
            )}

            {/* Artifacts generated by sandbox tools */}
            {effectiveArtifacts.length > 0 && !askQuestionMatchesRow && !persistedAskUserQuestion && (
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
                      KnowledgeBaseApi.getRecordDetails(codeArtifactId),
                      KnowledgeBaseApi.streamRecord(codeArtifactId),
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
                  } catch {
                    // Source artifact may have been deleted/permission-revoked since —
                    // fail silently, there is nothing actionable for the user here.
                  }
                }}
              />
            )}

            {/* Persisted ask_user_question from historical tool_call — read-only display */}
            {persistedAskUserQuestion && !askQuestionMatchesRow ? (
              <AskUserQuestionCard
                payload={persistedAskUserQuestion}
                initialAnswers={{}}
                status="persisted"
              />
            ) : null}

            {/* Active (streaming/pending) ask_user_question card */}
            {askQuestionMatchesRow && pendingAskUserQuestion ? (
              <AskUserQuestionCard
                payload={pendingAskUserQuestion.payload}
                initialAnswers={pendingAskUserQuestion.answers}
                status={pendingAskUserQuestion.status}
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
                  if (request) void streamMessageForSlot(sid, message, request);
                }}
              />
            ) : null}
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

  const handleEditQuery = useCallback(() => {
    if (!messageId || isStreaming) return;
    useCommandStore.getState().dispatch('showEditQuery', {
      messageId,
      text: question,
    });
  }, [messageId, question, isStreaming]);

  // When the active question card owns this row, read aloud the question text
  // and its options instead of the hidden bot response.
  const speakContent = askQuestionMatchesRow && pendingAskUserQuestion
    ? buildQuestionCardReadAloudText(pendingAskUserQuestion?.payload)
    : displayContent;

  const shell = (
    <Box style={{ width: '100%' }}>
      <Box
        style={{
          marginBottom:
            (collections && collections.length > 0) ||
            (appliedFilters &&
              (appliedFilters.apps.length > 0 || appliedFilters.kb.length > 0))
              ? 'var(--space-3)'
              : 'var(--space-4)',
        }}
      >
        <ExpandableUserQuery
          question={question}
          isMobile={isMobile}
          messageId={messageId}
          isStreaming={isStreaming}
          onEdit={handleEditQuery}
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
      {appliedFilters && (appliedFilters.apps.length > 0 || appliedFilters.kb.length > 0) && (
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
            <Flex
              key={att.virtualRecordId || att.recordId}
              align="center"
              gap="1"
              role="button"
              title={att.recordName}
              onClick={() => handleAttachmentPreview(att)}
              style={{
                flexShrink: 0,
                padding: 'var(--space-1) var(--space-2)',
                backgroundColor: 'var(--olive-a2)',
                border: '1px solid var(--olive-3)',
                borderRadius: 'var(--radius-1)',
                maxWidth: '200px',
                cursor: 'pointer',
                transition: 'background-color 0.15s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--olive-a4)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--olive-a2)';
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
                  color: 'var(--slate-11)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {att.recordName}
              </Text>
            </Flex>
          ))}
        </Flex>
      )}

      {/* Tabs */}
      {/* Tabs — hide Sources/Citations counts when the ask_user_question card
          (streaming or persisted) owns this row; those tabs reflect answer
          chunks that are suppressed. */}
      <ResponseTabs
        activeTab={activeTab}
        onTabChange={setActiveTab}
        sourcesCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : sourcesCount}
        citationCount={(askQuestionMatchesRow || persistedAskUserQuestion) ? 0 : citationCount}
      />

      {/* Tab Content */}
      {renderTabContent()}

      {/* Message Actions (feedback, copy, regenerate, model info) */}
      {activeTab === 'answer' && (
        <MessageActions
          content={speakContent}
          citationMaps={effectiveCitationMaps}
          modelInfo={modelInfo}
          isStreaming={isStreaming}
          messageId={messageId}
          question={question}
          isLastMessage={isLastMessage && !askQuestionMatchesRow}
          appliedFilters={appliedFilters}
          feedbackInfo={feedbackInfo}
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

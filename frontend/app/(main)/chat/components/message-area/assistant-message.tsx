'use client';

import React, { useState, useMemo, useRef, useCallback } from 'react';
import { Box } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ConfidenceIndicator } from './confidence-indicator';
import { AnswerContent } from './answer-content';
import { StatusMessageComponent } from './status-message';
import { MessageActions } from './message-actions';
import { ResponseTabs } from './response-tabs';
import { SourcesTab } from './response-tabs/citations/sources-tab';
import { CitationsTab } from './response-tabs/citations/citations-tab';
import { ArtifactsPanel } from './artifacts-panel';
import { AskUserQuestionCard } from './ask-user-question-card';
import { AgentActivityTimeline } from './agent-activity';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useChatStore } from '../../store';
import { debugLog } from '../../debug-logger';
import type { AskUserQuestionPayload, ConfidenceLevel, ModelInfo, StatusMessage, ChatArtifact, MessagePart, ResponseTab } from '../../types';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { emptyCitationMaps } from './response-tabs/citations';
import { repairStreamingMarkdown } from '../../utils/repair-streaming-markdown';
import { processMarkdownContent } from '../../utils/process-markdown-content';
import { parseDownloadMarkers, parseArtifactMarkers } from '../../utils/parse-download-markers';
import { DownloadTasks } from './download-tasks';
import {
  isPresentationFile,
  isLegacyWordDocFile,
  isDocxFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';
import { CitationMessageRowKeyContext } from './response-tabs/citations/citation-popover-control';

// Stable empty reference — avoids creating new objects in default params
const EMPTY_CITATION_MAPS: CitationMaps = emptyCitationMaps();

function buildQuestionCardReadAloudText(payload: AskUserQuestionPayload): string {
  const parts: string[] = [];
  if (payload.userIntent) parts.push(payload.userIntent);
  payload.questions.forEach((q, i) => {
    parts.push(`Question ${i + 1}: ${q.question}`);
    q.options.forEach((opt) => parts.push(`Option: ${opt.label}`));
  });
  return parts.join('. ');
}

interface AssistantMessageProps {
  answer: string;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
  confidence?: ConfidenceLevel;
  isStreaming?: boolean;
  modelInfo?: ModelInfo;
  /** The user's question that produced this answer — only used for regenerate + read-aloud fallback. */
  question: string;
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
  /** Applied filters at send-time — forwarded to the regenerate command bar. */
  appliedFilters?: import('../../types').AppliedFilters;
  /** Persisted ask_user_question payload from a historical tool_call — renders read-only question card */
  persistedAskUserQuestion?: AskUserQuestionPayload;
  /** Persisted feedback value from the backend — initialises the like/dislike button state */
  feedbackInfo?: { value?: 'like' | 'dislike' };
}

export const AssistantMessage = React.memo(function AssistantMessage({
  answer,
  citationMaps = EMPTY_CITATION_MAPS,
  citationCallbacks,
  confidence,
  isStreaming = false,
  modelInfo,
  question,
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
  appliedFilters,
  persistedAskUserQuestion,
  feedbackInfo,
}: AssistantMessageProps) {
  debugLog.tick('[chat] [AssistantMessage]');
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<ResponseTab>('answer');

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
  const prevAMRef = useRef<Record<string, unknown>>({});
  const currentAMVals: Record<string, unknown> = {
    answer, citationMaps, citationCallbacks, confidence,
    isStreaming, modelInfo, messageId,
    isLastMessage, streamingContent, currentStatusMessage: currentStatusMessageProp,
    streamingCitationMaps, streamingParts, persistedParts, persistedAskUserQuestion,
  };
  const amReasons: string[] = [];
  for (const [k, v] of Object.entries(currentAMVals)) {
    // eslint-disable-next-line react-hooks/refs -- intentional: debug render-reason tracking
    if (!Object.is(v, prevAMRef.current[k])) amReasons.push(k);
  }
  if (amReasons.length > 0) {
    debugLog.reason('[chat] [AssistantMessage]', amReasons);
  }
  // eslint-disable-next-line react-hooks/refs -- intentional: update previous-props snapshot for next render diff
  prevAMRef.current = currentAMVals;

  const setPreviewFile = useChatStore((s) => s.setPreviewFile);

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

  // Suppress answer/sources/artifacts when an ask_user_question card (streaming
  // or persisted) owns this row — the question card takes over the whole message.
  const suppressAnswerBody = askQuestionMatchesRow || !!persistedAskUserQuestion;

  // When the active question card owns this row, read aloud the question text
  // and its options instead of the hidden bot response.
  const speakContent = askQuestionMatchesRow && pendingAskUserQuestion
    ? buildQuestionCardReadAloudText(pendingAskUserQuestion?.payload)
    : displayContent;

  const sourcesCount = effectiveCitationMaps.sourcesOrder.length;
  const citationCount = Object.keys(effectiveCitationMaps.citationsOrder).length;

  const renderTabContent = () => {
    switch (activeTab) {
      case 'answer':
        return (
          <Box style={{ padding: 'var(--space-4) 0' }}>
            {!isStreaming && confidence && <ConfidenceIndicator confidence={confidence} />}

            {effectiveParts && effectiveParts.length > 0 && !suppressAnswerBody && (
              <AgentActivityTimeline parts={effectiveParts} isStreaming={isStreaming} citationMaps={effectiveCitationMaps} citationCallbacks={wrappedCallbacks} />
            )}

            {displayContent && !suppressAnswerBody && (
              <AnswerContent
                content={displayContent}
                citationMaps={effectiveCitationMaps}
                citationCallbacks={wrappedCallbacks}
              />
            )}

            {isStreaming && streamingStatusToShow && (
              <StatusMessageComponent status={streamingStatusToShow} />
            )}

            {downloadTasks.length > 0 && !suppressAnswerBody && (
              <DownloadTasks tasks={downloadTasks} />
            )}

            {effectiveArtifacts.length > 0 && !suppressAnswerBody && (
              <ArtifactsPanel
                artifacts={effectiveArtifacts}
                latestArtifactVersions={latestArtifactVersions}
                onPreview={loadArtifactPreview}
                onViewSource={async (codeArtifactId) => {
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
                    /* fail silently */
                  }
                }}
              />
            )}

            {persistedAskUserQuestion && !askQuestionMatchesRow ? (
              <AskUserQuestionCard
                payload={persistedAskUserQuestion}
                initialAnswers={{}}
                status="persisted"
              />
            ) : null}

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

  const shell = (
    <Box style={{ width: '100%' }}>
      <ResponseTabs
        activeTab={activeTab}
        onTabChange={setActiveTab}
        sourcesCount={sourcesCount}
        citationCount={citationCount}
      />
      {renderTabContent()}

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

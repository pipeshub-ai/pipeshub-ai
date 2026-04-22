'use client';

import React, { useState, useMemo, useRef, useCallback, useEffect } from 'react';
import { Box, Flex, Heading, IconButton } from '@radix-ui/themes';
import { SelectedCollections } from '../selected-collections';
import { ResponseTabs } from './response-tabs';
import { ConfidenceIndicator } from './confidence-indicator';
import { AnswerContent } from './answer-content';
import { StatusMessageComponent } from './status-message';
import { MessageActions } from './message-actions';
import { SourcesTab } from './response-tabs/citations/sources-tab';
import { CitationsTab } from './response-tabs/citations/citations-tab';
import { ArtifactsPanel } from './artifacts-panel';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useCommandStore } from '@/lib/store/command-store';
import { useChatStore } from '../../store';
import { debugLog } from '../../debug-logger';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { ConfidenceLevel, ModelInfo, StatusMessage, ResponseTab, ChatArtifact } from '../../types';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { emptyCitationMaps } from './response-tabs/citations';
import { repairStreamingMarkdown } from '../../utils/repair-streaming-markdown';
import { processMarkdownContent } from '../../utils/process-markdown-content';
import { parseDownloadMarkers, parseArtifactMarkers } from '../../utils/parse-download-markers';
import { DownloadTasks } from './download-tasks';
import { useTranslation } from 'react-i18next';

// Stable empty reference — avoids creating new objects in default params
const EMPTY_CITATION_MAPS: CitationMaps = emptyCitationMaps();

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
  feedbackInfo?: FeedbackInfo;
  /** Collections attached to this message (e.g. KB filters the user selected) */
  collections?: Array<{ id: string; name: string }>;
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
}

export const ChatResponse = React.memo(function ChatResponse({
  question,
  answer,
  citationMaps = EMPTY_CITATION_MAPS,
  citationCallbacks,
  confidence,
  isStreaming = false,
  modelInfo,
  feedbackInfo,
  collections,
  messageId,
  isLastMessage = false,
  streamingContent = '',
  currentStatusMessage: currentStatusMessageProp = null,
  streamingCitationMaps = null,
  streamingArtifacts,
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
    isStreaming, modelInfo, feedbackInfo, collections, messageId,
    isLastMessage, streamingContent, currentStatusMessage: currentStatusMessageProp,
    streamingCitationMaps,
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
    currentStatusMessage ??
    (isStreaming && !displayContent.trim() ? streamingFallbackStatus : null);

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
            {/* Status indicator — always above content, same slot as ConfidenceIndicator */}
            {isStreaming && streamingStatusToShow && (
              <StatusMessageComponent status={streamingStatusToShow} />
            )}

            {/* Show confidence only when not streaming and has answer */}
            {!isStreaming && confidence && <ConfidenceIndicator confidence={confidence} />}

            {/* Show content - either streaming or final */}
            {displayContent && (
              <AnswerContent
                content={displayContent}
                citationMaps={effectiveCitationMaps}
                citationCallbacks={wrappedCallbacks}
              />
            )}

            {/* Legacy download buttons for ::download_conversation_task markers
                (e.g. "CSV of full query results"). Works for both S3 presigned
                URLs and local storage endpoints — see DownloadTasks for the
                auth-handling split. */}
            {downloadTasks.length > 0 && <DownloadTasks tasks={downloadTasks} />}

            {/* Artifacts generated by sandbox tools — streamed live via SSE
                during generation, then persisted as markers in the saved
                answer content so they keep rendering on historical loads. */}
            {effectiveArtifacts.length > 0 && (
              <ArtifactsPanel
                artifacts={effectiveArtifacts}
                onPreview={async (artifact) => {
                  if (artifact.recordId) {
                    try {
                      const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
                      const blob = await KnowledgeBaseApi.streamRecord(artifact.recordId);
                      const objectUrl = URL.createObjectURL(blob);
                      useChatStore.getState().setPreviewFile({
                        id: artifact.recordId,
                        url: objectUrl,
                        name: artifact.fileName,
                        type: artifact.mimeType,
                        size: artifact.sizeBytes,
                      });
                      return;
                    } catch {
                      // Fall back to raw URL
                    }
                  }
                  useChatStore.getState().setPreviewFile({
                    id: artifact.id,
                    url: artifact.downloadUrl,
                    name: artifact.fileName,
                    type: artifact.mimeType,
                    size: artifact.sizeBytes,
                  });
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

  const [isQuestionHovered, setIsQuestionHovered] = useState(false);

  const handleEditQuery = useCallback(() => {
    if (!messageId || isStreaming) return;
    useCommandStore.getState().dispatch('showEditQuery', {
      messageId,
      text: question,
    });
  }, [messageId, question, isStreaming]);

  return (
    <Box style={{ width: '100%' }}>
      {/* Question Header with hover edit icon */}
      <Flex
        align="center"
        gap="2"
        onMouseEnter={() => setIsQuestionHovered(true)}
        onMouseLeave={() => setIsQuestionHovered(false)}
        style={{
          marginBottom: collections && collections.length > 0 ? 'var(--space-3)' : 'var(--space-4)',
        }}
      >
        <Heading
          size={isMobile ? '5' : '7'}
          weight="medium"
          style={{
            color: 'var(--slate-12)',
            lineHeight: 1.3,
            paddingTop: 'var(--space-3)',
          }}
        >
          {question}
          {/* Edit pencil icon — appears on hover, only when not streaming */}
          {!isStreaming && messageId && (
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              onClick={handleEditQuery}
              style={{
                margin: '0 0 0 var(--space-2)',
                cursor: 'pointer',
                flexShrink: 0,
                opacity: isQuestionHovered ? 1 : 0,
                transition: 'opacity 0.15s ease',
                pointerEvents: isQuestionHovered ? 'auto' : 'none',
                verticalAlign: 'middle',
              }}
            >
              <MaterialIcon
                name="edit"
                size={ICON_SIZES.PRIMARY}
                color="var(--slate-11)"
              />
            </IconButton>
          )}
        </Heading>
      </Flex>

      {/* Collection cards — shown when KBs were attached to this message */}
      {collections && collections.length > 0 && (
        <Box style={{ marginBottom: 'var(--space-4)' }}>
          <SelectedCollections collections={collections} />
        </Box>
      )}

      {/* Tabs */}
      <ResponseTabs
        activeTab={activeTab}
        onTabChange={setActiveTab}
        sourcesCount={sourcesCount}
        citationCount={citationCount}
      />

      {/* Tab Content */}
      {renderTabContent()}

      {/* Message Actions (feedback, copy, regenerate, model info) */}
      {activeTab === 'answer' && (
        <MessageActions
          content={displayContent}
          citationMaps={effectiveCitationMaps}
          modelInfo={modelInfo}
          feedbackInfo={feedbackInfo}
          isStreaming={isStreaming}
          messageId={messageId}
          question={question}
          isLastMessage={isLastMessage}
        />
      )}
    </Box>
  );
});

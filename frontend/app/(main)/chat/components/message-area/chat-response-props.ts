'use client';

import { useMemo } from 'react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { useChatStore } from '../../store';
import type { ChatResponseLayout } from '../../chat-extension';
import type { ChatArtifact, MessagePart, StatusMessage } from '../../types';
import { parseArtifactMarkers } from '../../utils/parse-download-markers';
import type { ChatResponseProps } from './chat-response';
import type { CitationCallbacks, CitationMaps } from './response-tabs/citations/types';
import { extractTextContent } from './message-pairs';
import type { MessagePair, RetryTurn } from './message-pairs';

export type { ChatResponseProps };

/** A slot's live answer fields; only the answer that streams gets them. */
export interface SlotStreamingProps {
  streamingContent: string;
  currentStatusMessage: StatusMessage | null;
  streamingCitationMaps: CitationMaps | null;
  streamingArtifacts: ChatArtifact[];
  streamingParts: MessagePart[];
}

/** What every answer of one list shares. */
export interface ChatResponseContext {
  citationCallbacks: CitationCallbacks;
  streaming: SlotStreamingProps;
  latestArtifactVersions: ReadonlyMap<string, number>;
  /** The viewer can send in this chat: a failed last answer offers Retry, and Regenerate shows. */
  canSend: boolean;
  onRetry(turn: RetryTurn): void;
}

// Stable fallbacks: `?? []` in a selector would make a new array on every call.
const EMPTY_STRING = '';
const NO_ARTIFACTS: ChatArtifact[] = [];
const NO_PARTS: MessagePart[] = [];

/** The live answer fields of `slotId`, each read with its own selector. */
export function useSlotStreamingProps(slotId: string | null): SlotStreamingProps {
  const streamingContent = useChatStore((s) =>
    slotId ? s.slots[slotId]?.streamingContent || EMPTY_STRING : EMPTY_STRING
  );
  const currentStatusMessage = useChatStore((s) => (slotId ? s.slots[slotId]?.currentStatusMessage ?? null : null));
  const streamingCitationMaps = useChatStore((s) => (slotId ? s.slots[slotId]?.streamingCitationMaps ?? null : null));
  const streamingArtifacts = useChatStore((s) =>
    slotId ? s.slots[slotId]?.artifacts ?? NO_ARTIFACTS : NO_ARTIFACTS
  );
  const streamingParts = useChatStore((s) => (slotId ? s.slots[slotId]?.streamingParts ?? NO_PARTS : NO_PARTS));
  return useMemo(
    () => ({ streamingContent, currentStatusMessage, streamingCitationMaps, streamingArtifacts, streamingParts }),
    [streamingContent, currentStatusMessage, streamingCitationMaps, streamingArtifacts, streamingParts],
  );
}

/**
 * The highest version any answer shows for each artifact `recordId`. Every version bump re-registers the
 * same recordId, so an older card compares its own `version` with this to know a later turn made a newer
 * copy. Read from the markers saved on each answer; the streaming turn's are not saved yet, so its live
 * `streamingArtifacts` (null when nothing streams) count too.
 */
function artifactVersionEntries(
  messages: readonly ThreadMessageLike[],
  streamingArtifacts: ChatArtifact[] | null,
): [string, number][] {
  const versions = new Map<string, number>();
  const record = (recordId: string | undefined, version: number | undefined) => {
    if (!recordId || version === undefined) return;
    const current = versions.get(recordId);
    if (current === undefined || version > current) versions.set(recordId, version);
  };
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue;
    const content = extractTextContent(msg.content as { type: string; text?: string }[]);
    if (!content.includes('::artifact[')) continue;
    const { artifacts } = parseArtifactMarkers(content);
    for (const artifact of artifacts) record(artifact.recordId, artifact.version);
  }
  for (const artifact of streamingArtifacts ?? []) record(artifact.recordId, artifact.version);
  return [...versions];
}

/** Latest artifact versions (see `artifactVersionEntries`); the same Map until a version changes. */
export function useLatestArtifactVersions(
  messages: readonly ThreadMessageLike[],
  streamingArtifacts: ChatArtifact[] | null,
): ReadonlyMap<string, number> {
  // Keyed by the entries' text, so a message change that moves no version keeps the Map, and the answers.
  const key = useMemo(
    () => JSON.stringify(artifactVersionEntries(messages, streamingArtifacts)),
    [messages, streamingArtifacts],
  );
  return useMemo(() => new Map(JSON.parse(key) as [string, number][]), [key]);
}

/** The `ChatResponse` props of one pair, the same for every list that draws answers. */
export function chatResponseProps(
  pair: MessagePair,
  isLast: boolean,
  ctx: ChatResponseContext,
  layout?: ChatResponseLayout,
): ChatResponseProps {
  const live = pair.isStreaming ? ctx.streaming : null;
  return {
    question: pair.question,
    answer: pair.answer,
    citationMaps: pair.citationMaps,
    citationCallbacks: ctx.citationCallbacks,
    confidence: pair.confidence,
    isStreaming: pair.isStreaming,
    modelInfo: pair.modelInfo,
    collections: pair.collections,
    appliedFilters: pair.appliedFilters,
    attachments: pair.attachments,
    messageId: pair.messageId,
    isLastMessage: isLast,
    rowId: pair.key,
    citationMessageRowKey: pair.key,
    createdAt: pair.createdAt,
    streamingContent: live?.streamingContent,
    currentStatusMessage: live?.currentStatusMessage,
    streamingCitationMaps: live?.streamingCitationMaps,
    streamingArtifacts: live?.streamingArtifacts,
    streamingParts: live?.streamingParts,
    latestArtifactVersions: ctx.latestArtifactVersions,
    persistedParts: pair.persistedParts,
    persistedAskUserQuestion: pair.persistedAskUserQuestion,
    persistedAskUserQuestionAnswers: pair.persistedAskUserQuestionAnswers,
    feedbackInfo: pair.feedbackInfo,
    status: pair.status,
    streamError: pair.streamError,
    unavailableTools: pair.unavailableTools,
    failed: pair.failed,
    onRetry: ctx.canSend ? ctx.onRetry : undefined,
    canSend: ctx.canSend,
    unanswered: pair.unanswered,
    pair,
    layout,
  };
}

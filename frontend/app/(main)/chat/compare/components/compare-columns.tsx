'use client';

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Grid, Spinner, Text } from '@radix-ui/themes';
import { ChatResponse } from '../../components/message-area/chat-response';
import { RenderSlotContext } from '../../components/message-area/render-slot';
import { StatusMessageComponent } from '../../components/message-area/status-message';
import { buildMessagePairs } from '../../components/message-area/message-pairs';
import { emptyCitationMaps } from '../../components/message-area/response-tabs/citations/utils';
import type { CitationCallbacks } from '../../components/message-area/response-tabs/citations';
import { useChatStore } from '../../store';
import { cancelStreamForSlot } from '../../streaming';
import { columnIsThinking, columnStatus } from '../column-state';
import { chooseAnswer, retryBranch } from '../compare-controller';
import type { ColumnStatus, CompareTurn } from '../types';
import type { ChatSlot, ModelInfo } from '../../types';

const EMPTY_CITATION_MAPS = emptyCitationMaps();
const NO_VERSIONS = new Map<string, number>();
const NO_ARTIFACTS: never[] = [];

const STATUS_KEYS: Record<ColumnStatus, string> = {
  starting: 'chat.compare.status.starting',
  streaming: 'chat.compare.status.streaming',
  waiting: 'chat.compare.status.waiting',
  error: 'chat.compare.status.error',
  empty: 'chat.compare.status.done',
  done: 'chat.compare.status.done',
};

function slotModelInfo(slot: ChatSlot | undefined): ModelInfo | undefined {
  const last = slot?.messages[slot.messages.length - 1];
  return last?.role === 'assistant'
    ? (last.metadata?.custom as { modelInfo?: ModelInfo } | undefined)?.modelInfo
    : undefined;
}

interface ShellProps {
  label: string;
  status: ColumnStatus;
  thinking?: boolean;
  onStop?: () => void;
  /** A run the page did not start (after a reload) has no stream here to abort. */
  stopDisabled?: boolean;
  onRetry?: () => void;
  onChoose?: () => void;
  children: React.ReactNode;
}

function ColumnShell({ label, status, thinking, onStop, stopDisabled, onRetry, onChoose, children }: ShellProps) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      gap="2"
      data-testid="compare-column"
      style={{
        minWidth: 0,
        padding: 'var(--space-3)',
        border: '1px solid var(--olive-4)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--olive-2)',
      }}
    >
      <section aria-label={t('chat.compare.columnLabel', { model: label })} style={{ minWidth: 0 }}>
        <Flex align="center" justify="between" gap="2" style={{ marginBottom: 'var(--space-2)' }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {label}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-10)' }} aria-live="polite">
            {t(STATUS_KEYS[status])}
          </Text>
        </Flex>
        <Box style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
          {thinking ? (
            // Nothing has been answered yet, so the reply's own (empty) tabs would only make the two columns differ.
            <StatusMessageComponent
              status={{ id: 'compare-thinking', status: 'thinking', message: t('chatStream.thinkingFallback'), timestamp: '' }}
            />
          ) : (
            children
          )}
        </Box>
        <Flex gap="2" wrap="wrap" align="center" style={{ marginTop: 'var(--space-2)' }}>
          {onStop && !stopDisabled && (
            <Button size="1" variant="soft" color="gray" onClick={onStop}>
              {t('chat.compare.stop')}
            </Button>
          )}
          {onRetry && (
            <Button size="1" variant="soft" onClick={onRetry}>
              {t('chat.compare.retry')}
            </Button>
          )}
          {onChoose && (
            <Button size="1" onClick={onChoose} aria-label={t('chat.compare.useAnswerLabel', { model: label })}>
              {t('chat.compare.useAnswer')}
            </Button>
          )}
        </Flex>
      </section>
    </Flex>
  );
}

function FailedLine() {
  const { t } = useTranslation();
  return (
    <Text size="2" role="alert" style={{ color: 'var(--slate-11)' }}>
      {t('chat.compare.failed')}
    </Text>
  );
}

function PendingLine() {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="2" style={{ color: 'var(--slate-11)' }}>
      <Spinner size="1" />
      <Text size="2">{t('chat.compare.starting')}</Text>
    </Flex>
  );
}

/** The hidden branch's reply to the compared question, drawn with the same response component as any chat. */
function BranchReply({
  branchSlotId,
  citationCallbacks,
}: {
  branchSlotId: string;
  citationCallbacks?: CitationCallbacks;
}) {
  const slot = useChatStore((s) => s.slots[branchSlotId]);
  const pair = useMemo(() => {
    if (!slot) return undefined;
    const pairs = buildMessagePairs(slot.messages, {
      isStreaming: slot.isStreaming,
      streamingQuestion: slot.streamingQuestion,
      pendingCollections: slot.pendingCollections,
      regenerateMessageId: null,
      emptyCitationMaps: EMPTY_CITATION_MAPS,
    });
    return pairs[pairs.length - 1];
  }, [slot]);
  if (!slot || !pair) return <PendingLine />;

  return (
    <RenderSlotContext.Provider value={branchSlotId}>
      <ChatResponse
        columnMode
        question={pair.question}
        answer={pair.answer}
        citationMaps={pair.citationMaps}
        citationCallbacks={citationCallbacks}
        confidence={pair.confidence}
        searchDegraded={pair.searchDegraded}
        isStreaming={pair.isStreaming}
        modelInfo={pair.modelInfo}
        messageId={pair.messageId}
        isLastMessage
        rowId={pair.key}
        citationMessageRowKey={pair.key}
        streamingContent={pair.isStreaming ? slot.streamingContent : undefined}
        currentStatusMessage={pair.isStreaming ? slot.currentStatusMessage : undefined}
        streamingCitationMaps={pair.isStreaming ? slot.streamingCitationMaps : undefined}
        streamingArtifacts={pair.isStreaming ? slot.artifacts : NO_ARTIFACTS}
        streamingParts={pair.isStreaming ? slot.streamingParts : undefined}
        latestArtifactVersions={NO_VERSIONS}
        persistedParts={pair.persistedParts}
        persistedAskUserQuestion={pair.persistedAskUserQuestion}
        persistedAskUserQuestionAnswers={pair.persistedAskUserQuestionAnswers}
        status={pair.status}
        streamError={pair.streamError}
      />
    </RenderSlotContext.Provider>
  );
}

/**
 * The compared turn: the question once, then the primary's reply (left) and the
 * branch's reply (right). Each side keeps its own cards, approvals and Stop; a
 * side that waits on a person does not hold the other back.
 */
export function CompareColumns({
  slotId,
  turn,
  question,
  primary,
  citationCallbacks,
}: {
  slotId: string;
  turn: CompareTurn;
  question: React.ReactNode;
  primary: React.ReactNode;
  citationCallbacks?: CitationCallbacks;
}) {
  const { t } = useTranslation();
  const primarySlot = useChatStore((s) => s.slots[slotId]);
  const branchSlot = useChatStore((s) => (turn.branchSlotId ? s.slots[turn.branchSlotId] : undefined));

  const leftStatus = columnStatus(primarySlot);
  const rightStatus: ColumnStatus =
    turn.phase === 'failed' ? 'error' : columnStatus(turn.branchSlotId ? branchSlot : undefined);

  const fallback = t('chat.compare.primaryFallback');
  const nameOf = (m: { modelFriendlyName?: string; modelName?: string } | null | undefined) =>
    m?.modelFriendlyName || m?.modelName || '';
  const leftLabel = nameOf(turn.models?.[0]) || nameOf(slotModelInfo(primarySlot)) || fallback;
  const rightLabel = nameOf(turn.models?.[1]) || nameOf(slotModelInfo(branchSlot)) || fallback;

  const canChoose = !turn.choosing && Boolean(turn.userMessageId);
  const chooseOf = (which: 'primary' | 'branch', status: ColumnStatus) =>
    canChoose && status === 'done' ? () => void chooseAnswer(slotId, which) : undefined;

  const error =
    turn.chooseError === 'conflict'
      ? t('chat.compare.chooseConflict')
      : turn.chooseError
        ? t('chat.compare.chooseFailed')
        : null;

  return (
    <Box data-testid="compare-columns">
      {question}
      <Grid columns={{ initial: '1', md: '2' }} gap="4" style={{ marginTop: 'var(--space-3)' }}>
        <ColumnShell
          label={leftLabel}
          status={leftStatus}
          thinking={columnIsThinking(primarySlot) && !primarySlot?.isStreaming}
          stopDisabled={!primarySlot?.isStreaming}
          onStop={leftStatus === 'streaming' ? () => cancelStreamForSlot(slotId) : undefined}
          onChoose={chooseOf('primary', leftStatus)}
        >
          {leftStatus === 'error' ? <FailedLine /> : primary}
        </ColumnShell>
        <ColumnShell
          label={rightLabel}
          status={rightStatus}
          thinking={columnIsThinking(branchSlot) && !branchSlot?.isStreaming}
          stopDisabled={!branchSlot?.isStreaming}
          onStop={
            rightStatus === 'streaming' && turn.branchSlotId
              ? () => cancelStreamForSlot(turn.branchSlotId!)
              : undefined
          }
          onRetry={rightStatus === 'error' && turn.template ? () => retryBranch(slotId) : undefined}
          onChoose={chooseOf('branch', rightStatus)}
        >
          {rightStatus === 'error' ? (
            <FailedLine />
          ) : turn.branchSlotId ? (
            <BranchReply branchSlotId={turn.branchSlotId} citationCallbacks={citationCallbacks} />
          ) : (
            <PendingLine />
          )}
        </ColumnShell>
      </Grid>
      {error && (
        <Text size="2" role="alert" style={{ color: 'var(--red-11)', display: 'block', marginTop: 'var(--space-2)' }}>
          {error}
        </Text>
      )}
    </Box>
  );
}

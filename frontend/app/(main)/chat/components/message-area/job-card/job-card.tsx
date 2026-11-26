'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AgentTile, StatusGlyph } from '@/app/components/harness-jobs';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { agentName } from '@/lib/harness-jobs/engine-worker';
import { engineName } from '@/app/(main)/workspace/ai-accounts/access-copy';
import { useRowState } from '../../virtual-rows';
import { jobDetailsId, openJob, useActivityPanelStore } from '../../activity-panel/activity-panel-store';
import { usePendingAsk } from '../../../hooks/use-interaction-focus';
import { useMessageRun } from '../../activity-panel/use-activity-run';
import { useRunModelLabel } from '../../../hooks/use-run-model-label';
import type { DurableSessionRoute } from '../../../hooks/use-run-timeline';
import { latestSignIn, signInReasonKey } from '../../../engine-signin';
import { toRunModel } from '../../../utils/run-model';
import type { MessagePart, RunModelRef } from '../../../types';
import { ChildRunPanel, stepsOf } from '../child-run-panel';
import { isLive, stopJob, useJobsStore } from './data';
import { clockWords, elapsedMs, jobCardState, secondLine, statusWord } from './job-card-state';
import { useJobStream, useNow, useOnScreen } from './use-job-stream';

const VISUALLY_HIDDEN: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
};

const ONE_LINE: React.CSSProperties = { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 };

/** A plain button: the card's own look, and the focus ring of `.job-card-control` (globals.css). */
const CONTROL_RESET: React.CSSProperties = { appearance: 'none', background: 'none', border: 'none', margin: 0, font: 'inherit', color: 'inherit' };

/** A card hugs its words up to this, as the design's pill does (280px) plus the stop square beside it. */
const CARD_MAX = 340;

/** `card`: one of up to three, its own bordered pill. `row`: a row of the board past three (`TurnJobs`). */
export type JobCardVariant = 'card' | 'row';

export interface JobCardProps {
  part: MessagePart;
  session: DurableSessionRoute;
  /** The job's name: what it was asked (F10). */
  title: string;
  variant?: JobCardVariant;
  /** The chat has an ask of this job open. */
  waiting?: boolean;
  /** The job's asks the chat drew for this card: answered on it (D3) until they float over the composer (BGUI-4). */
  asks?: React.ReactNode[];
  /** What it is doing now, from its steps. */
  nowOf: (parts: MessagePart[]) => string | null;
  /** Its steps, drawn by the chat's activity timeline. */
  renderSteps: (parts: MessagePart[]) => React.ReactNode;
}

/**
 * A background job in the chat (design `sandbox-thread.tsx` JobCard and board row, §6B.42; CLASSIC-JOBS D2), drawn
 * in classic's look. The agent's logo in its tile; the job's name over one status line, "Working · 4s · Claude Code",
 * whose glyph spins while it goes and is red only when it couldn't finish. The card itself opens the job's panel, the
 * stop square ends the job alone in one click, and the fold under it opens its steps, with what it does now beside.
 */
export function JobCard({ part, session, title, variant = 'card', waiting = false, asks = [], nowOf, renderSteps }: JobCardProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const runId = part.runId as string;
  const [expanded, setExpanded] = useRowState(`subAgent.expanded:${runId}`, false);
  const [hover, setHover] = useState(false);
  // The Stop's outcome is the job's (ADR-CJ-01): a card drawn again when the answer settles still says it (F9).
  const stop = useJobsStore((s) => s.stops[runId]) ?? 'idle';
  // Null for a shared viewer: the job's routes behind the panel and Stop are its owner's (D14).
  const handle = useMessageRun(runId, false);
  const owner = handle !== null;
  const partLive = part.status === 'running';
  const { stream, extra } = useJobStream(session, runId, {
    live: partLive,
    stoppedEarlier: part.status === 'failed' && part.errorType === 'cancelled',
    owner,
  });
  const state = jobCardState({ part, stream, waiting: waiting || asks.length > 0, operatorStop: extra?.operatorStop ?? null, isOwner: owner });
  const live = isLive(state.status);

  const frames = useMemo(() => stream?.frames ?? [], [stream]);
  // The job's own frames once its stream has any; the parent's copy of its steps until then.
  const liveParts = useMemo<MessagePart[]>(
    () => (frames.length > 0 ? [{ type: 'engine_task', runId, frames: [...frames] }] : (part.parts ?? [])),
    [frames, part.parts, runId],
  );
  const runEnded = !partLive || stream?.ended === true;
  const stepCount = useMemo(() => stepsOf(liveParts, runEnded).steps.length, [liveParts, runEnded]);
  const now = state.status === 'working' ? nowOf(liveParts) : null;
  const second = secondLine(state, { now, frames }, t);
  const word = statusWord(state, extra?.operatorStop, t);

  const [timelineModel, setTimelineModel] = useState<RunModelRef | null>(null);
  const keepModel = useCallback(
    (model: RunModelRef) => setTimelineModel((held) => (held?.provider === model.provider && held.model === model.model ? held : model)),
    [],
  );
  const partModel = useMemo(() => toRunModel(part.model), [part.model]);
  const modelLabel = useRunModelLabel(timelineModel ?? partModel);
  const engine = part.codeTaskHandoff?.engine ?? extra?.engine ?? null;
  const agentLabel = engine ? engineName(engine) : modelLabel;
  const mark = agentName(engine);

  const [onScreenRef, onScreen] = useOnScreen<HTMLDivElement>();
  const clock = useNow(live && onScreen && stream?.startedAt !== undefined);
  const ms = elapsedMs(stream, part, clock);

  // SIGNIN: a job waiting for the person to sign in opens itself, so its sign-in card is seen.
  const waitsForSignIn = latestSignIn(frames)?.state === 'requested';
  useEffect(() => {
    if (waitsForSignIn) setExpanded(true);
  }, [waitsForSignIn]);

  // A Needs you link naming one of this job's asks opens its steps, where the ask's card is drawn.
  const pendingAsk = usePendingAsk();
  const holdsPending = !!pendingAsk && (stream?.asks ?? []).some((a) => a.interactionId === pendingAsk);
  useEffect(() => {
    if (holdsPending && asks.length === 0) setExpanded(true);
  }, [holdsPending, asks.length]);

  const panelOpen = useActivityPanelStore((s) => (s.open || s.sheetOpen) && s.run?.runId === runId);
  const details = () => {
    if (handle) openJob(runId);
  };
  const canStop = owner && live && stop !== 'gone';
  const signInRefusal = signInReasonKey(part.errorType);
  const stepsLabel = stepCount > 0 ? t('jobCard.steps', { count: stepCount }) : t('jobCard.showSteps');
  const row = variant === 'row';
  const raised = (owner && hover) || panelOpen;
  const tap = isMobile ? 44 : undefined;
  // Open steps and an ask need the column's width; on a phone the cards stack at one width, never a ragged column.
  const wide = expanded || asks.length > 0 || isMobile;

  const identity = (
    <>
      <AgentTile agent={mark} size={row ? 28 : 36} raised={raised} />
      <Flex direction="column" style={{ gap: 2, minWidth: 0, flex: 1, textAlign: 'left' }}>
        <Text size="2" weight="medium" style={{ ...ONE_LINE, color: 'var(--gray-12)', lineHeight: '18px' }}>
          {title}
        </Text>
        <Flex align="center" gap="1" style={{ minWidth: 0, height: 16 }}>
          <Flex align="center" justify="center" style={{ width: 12, height: 12, flexShrink: 0 }}>
            <StatusGlyph status={state.status} />
          </Flex>
          <Text size="1" style={{ ...ONE_LINE, color: 'var(--gray-11)', lineHeight: '16px' }}>
            <span data-testid="job-card-status" style={state.status === 'couldntFinish' ? { color: 'var(--red-11)' } : undefined}>
              {word}
            </span>
            {ms !== null || agentLabel ? (
              <span data-testid="job-card-meta">
                {ms !== null ? (
                  <>
                    {' · '}
                    <span data-testid="job-card-elapsed">{clockWords(ms, t)}</span>
                  </>
                ) : null}
                {agentLabel ? (
                  <>
                    {' · '}
                    <span data-testid="sub-agent-model">{agentLabel}</span>
                  </>
                ) : null}
              </span>
            ) : null}
          </Text>
        </Flex>
      </Flex>
    </>
  );

  const pillStyle: React.CSSProperties = {
    ...CONTROL_RESET,
    boxSizing: 'border-box',
    display: 'flex',
    alignItems: 'center',
    gap: row ? 'var(--space-3)' : 10,
    flex: 1,
    minWidth: 0,
    minHeight: row ? 48 : 50,
    padding: row ? 'var(--space-2) 10px' : '7px 12px 7px 7px',
    cursor: owner ? 'pointer' : 'default',
    borderRadius: row ? 'var(--radius-2)' : 'var(--radius-3)',
  };

  return (
    <Box
      ref={onScreenRef}
      className="agent-activity-enter"
      role="group"
      aria-label={t('jobCard.groupLabel', { title, status: word })}
      data-testid="job-card"
      data-variant={variant}
      data-run-id={runId}
      data-status={state.status}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={
        row
          ? { borderRadius: 'var(--radius-2)', backgroundColor: raised ? 'var(--gray-a3)' : undefined, minWidth: 0 }
          : {
              width: wide ? '100%' : 'fit-content',
              maxWidth: wide ? '100%' : `min(100%, ${CARD_MAX}px)`,
              minWidth: 0,
              boxSizing: 'border-box',
              border: `1px solid ${panelOpen ? 'var(--accent-8)' : raised ? 'var(--gray-a6)' : 'var(--gray-a5)'}`,
              borderRadius: 'var(--radius-3)',
              backgroundColor: raised ? 'var(--gray-a3)' : 'var(--gray-a2)',
              overflow: 'hidden',
            }
      }
    >
      <span aria-live="polite" style={VISUALLY_HIDDEN}>
        {word}
      </span>
      {state.status === 'needsYou' ? (
        <span role="alert" style={VISUALLY_HIDDEN}>
          {t('jobCard.needsYouAlert', { title })}
        </span>
      ) : null}
      <Flex align="center" data-testid="job-card-header" style={{ minWidth: 0 }}>
        {owner ? (
          <button
            type="button"
            id={jobDetailsId(runId)}
            data-testid="job-card-details"
            className="job-card-control"
            aria-pressed={panelOpen}
            title={title}
            onClick={details}
            style={pillStyle}
          >
            {identity}
          </button>
        ) : (
          <div style={pillStyle}>{identity}</div>
        )}
        {canStop ? (
          <IconButton
            size={isMobile ? '3' : '1'}
            variant="ghost"
            color="gray"
            disabled={stop === 'sending' || stop === 'sent'}
            onClick={() => void stopJob(session, runId)}
            aria-label={t('jobCard.stopAria', { title })}
            data-testid="job-card-stop"
            style={{ minWidth: tap ?? 28, minHeight: tap ?? 28, margin: isMobile ? '0 var(--space-1) 0 0' : '0 var(--space-2) 0 0', flexShrink: 0 }}
          >
            <MaterialIcon name="stop" variant="filled" size={ICON_SIZES.PRIMARY} color="var(--gray-11)" />
          </IconButton>
        ) : null}
      </Flex>
      <Flex
        align="center"
        gap="2"
        style={{
          minWidth: 0,
          minHeight: tap ?? 28,
          padding: row ? '0 10px var(--space-1) 48px' : '0 var(--space-3) 0 var(--space-2)',
          borderTop: row ? undefined : '1px solid var(--gray-a4)',
        }}
      >
        <button
          type="button"
          aria-expanded={expanded}
          data-testid="sub-agent-toggle"
          className="job-card-control"
          onClick={() => setExpanded((open) => !open)}
          style={{
            ...CONTROL_RESET,
            boxSizing: 'border-box',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 2,
            flexShrink: 0,
            minHeight: tap ?? 28,
            cursor: 'pointer',
            color: 'var(--gray-11)',
          }}
        >
          <MaterialIcon name={expanded ? 'expand_more' : 'chevron_right'} size={ICON_SIZES.SECONDARY} color="var(--gray-11)" />
          <Text size="1">{stepsLabel}</Text>
        </button>
        {second ? (
          <Text size="1" data-testid="job-card-now" title={second} style={{ ...ONE_LINE, flex: 1, color: 'var(--gray-11)' }}>
            {second}
          </Text>
        ) : (
          <Box style={{ flex: 1 }} />
        )}
        {stop === 'gone' ? (
          <Text size="1" color="gray" role="status" data-testid="job-card-stop-gone" style={{ flexShrink: 0 }}>
            {t('jobCard.stopGone')}
          </Text>
        ) : null}
        {stop === 'failed' ? (
          <Text size="1" color="red" role="status" style={{ flexShrink: 0 }}>
            {t('jobCard.stopFailed')}
          </Text>
        ) : null}
      </Flex>
      {signInRefusal ? (
        <Text as="div" size="1" color="red" style={{ padding: '0 var(--space-3) var(--space-2)' }}>
          {t(signInRefusal)}
        </Text>
      ) : null}
      {asks.length > 0 ? (
        <Flex direction="column" gap="2" data-testid="job-card-asks" style={{ padding: 'var(--space-2) var(--space-3)' }}>
          {asks}
        </Flex>
      ) : null}
      {expanded ? (
        <Box style={{ padding: '0 var(--space-3) var(--space-3)' }}>
          <ChildRunPanel
            session={session}
            runId={runId}
            liveParts={liveParts}
            ended={!live}
            readTimeline={owner}
            showAsks={asks.length === 0}
            askKey={(stream?.asks ?? []).map((a) => `${a.interactionId}:${a.status}`).join(',')}
            streamAsks={stream?.asks}
            onModel={keepModel}
            renderParts={renderSteps}
          />
        </Box>
      ) : null}
    </Box>
  );
}

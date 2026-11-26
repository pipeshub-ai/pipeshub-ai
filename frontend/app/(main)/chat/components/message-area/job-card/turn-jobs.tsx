'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { StatusGlyph } from '@/app/components/harness-jobs';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { CARDS_UP_TO } from '@/lib/harness-jobs/jobs';
import { jobTitle as sharedJobTitle } from '@/lib/harness-jobs/engine-worker';
import { handoffLabel } from '../../../handoff';
import { partAnchors } from '../../../interactions';
import type { MessagePart } from '../../../types';
import type { CitationCallbacks, CitationMaps } from '../response-tabs/citations';
import { SubAgentGroup } from '../agent-activity';
import { JobReports } from './job-report';

/** A job's name, as every other surface names it: what it was asked (F10), as `lib/harness-jobs` words it. */
export function jobTitle(part: MessagePart, untitled: string): string {
  return sharedJobTitle(part.codeTaskGoal || part.goal, () => untitled);
}

export interface TurnJobsProps {
  /** The turn's jobs (`useIsJobPart`), in hand-out order. */
  jobs: MessagePart[];
  /** The chat's interaction cards by anchor: a job's asks are drawn on its card (D3). */
  anchored?: ReadonlyMap<string, React.ReactNode>;
  /** Anchors with an open ask. */
  waiting?: ReadonlySet<string>;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
  /** Files the turn's answer shows, by record id or name: a job's report leaves them to it. */
  shownFiles?: ReadonlySet<string>;
}

/**
 * The jobs a turn handed out, drawn once, after the turn's steps (design `afterRun`, §6B.42): never inside the steps,
 * which fold away when the answer settles. A hand-off the run made on its own says so first, in one plain line. Up
 * to three jobs are a card each; past three they are one board, a row a job, that folds but is never folded for you
 * (BEHAVIORUI §7). Under them, the jobs that finished report back in the order they finished (BGUI-6).
 */
export function TurnJobs({ jobs, anchored, waiting, citationMaps, citationCallbacks, shownFiles }: TurnJobsProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(true);
  if (jobs.length === 0) return null;

  const handoffs = [...new Set(jobs.flatMap((part) => (part.codeTaskHandoff ? [handoffLabel(t, part.codeTaskHandoff)] : [])))];
  const asksOf = (part: MessagePart): React.ReactNode[] =>
    partAnchors(part).flatMap((id) => (anchored?.has(id) ? [<React.Fragment key={id}>{anchored.get(id)}</React.Fragment>] : []));
  const draw = (part: MessagePart, variant: 'card' | 'row') => (
    <SubAgentGroup
      key={part.runId}
      part={part}
      variant={variant}
      citationMaps={citationMaps}
      citationCallbacks={citationCallbacks}
      waiting={!!part.runId && !!waiting?.has(`run:${part.runId}`)}
      asks={asksOf(part)}
    />
  );
  const board = jobs.length > CARDS_UP_TO;
  const live = jobs.some((part) => part.status === 'running');

  return (
    <Flex direction="column" gap="2" role="group" aria-label={t('jobCard.region')} data-testid="turn-jobs" style={{ marginBottom: 'var(--space-3)', minWidth: 0 }}>
      {handoffs.map((line) => (
        <Text key={line} as="p" size="2" data-testid="job-handoff-line" style={{ margin: 0, color: 'var(--gray-11)' }}>
          {line}
        </Text>
      ))}
      {board ? (
        <Box
          data-testid="jobs-board"
          style={{ border: '1px solid var(--gray-a5)', borderRadius: 'var(--radius-3)', backgroundColor: 'var(--gray-a2)', overflow: 'hidden', minWidth: 0 }}
        >
          <Flex align="center" gap="2" style={{ minHeight: isMobile ? 44 : 40, padding: '0 var(--space-2) 0 14px' }}>
            <StatusGlyph status={live ? 'working' : 'done'} />
            <Text size="2" weight="medium" data-testid="jobs-board-count" style={{ color: 'var(--gray-12)', flex: 1 }}>
              {t('jobCard.board.count', { count: jobs.length })}
            </Text>
            <IconButton
              size={isMobile ? '3' : '1'}
              variant="ghost"
              color="gray"
              aria-label={open ? t('jobCard.board.hide') : t('jobCard.board.show')}
              aria-expanded={open}
              data-testid="jobs-board-toggle"
              onClick={() => setOpen((v) => !v)}
              style={{ margin: 0, minWidth: isMobile ? 44 : 28, minHeight: isMobile ? 44 : 28 }}
            >
              <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={ICON_SIZES.SECONDARY} color="var(--gray-11)" />
            </IconButton>
          </Flex>
          {open ? (
            <Flex direction="column" style={{ borderTop: '1px solid var(--gray-a4)', padding: 'var(--space-1)' }}>
              {jobs.map((part) => draw(part, 'row'))}
            </Flex>
          ) : null}
        </Box>
      ) : (
        <Flex gap="2" wrap="wrap" style={{ minWidth: 0 }}>
          {jobs.map((part) => draw(part, 'card'))}
        </Flex>
      )}
      <JobReports jobs={jobs} shownFiles={shownFiles} titleOf={(part) => jobTitle(part, t('jobCard.untitled'))} />
    </Flex>
  );
}

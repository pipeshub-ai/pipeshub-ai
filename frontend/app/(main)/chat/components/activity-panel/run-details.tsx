'use client';

import React from 'react';
import { Flex } from '@radix-ui/themes';
import type { DurableAgentRunHandle, RunBudgetView, RunTimelineView } from '../../types';
import { RunCost } from '../message-area/run-cost';
import { WorkspaceVersionsPanel } from '../message-area/workspace-versions';
import { OutsideAccessList } from '../message-area/outside-access';
import { SecretsHiddenLine } from '../message-area/secrets-hidden';
import { useRunTimeline } from '../../hooks/use-run-timeline';
import { runStillActive } from '../../durable-run-ui';

/** A run's spend and, once an engine run ends, its workspace versions with restore (moved from the code-task card). */
// `finished` (from the activity stream) only re-reads the timeline; whether the run ended is the timeline's own word.
export function RunDetails({ run, finished }: { run: DurableAgentRunHandle; finished: boolean }) {
  const { timeline } = useRunTimeline(run, run.runId, { enabled: true, refreshKey: finished });
  return <RunDetailsView run={run} timeline={timeline} />;
}

/**
 * Whether a job's Brief says what it cost (BGUI-10): once it has spent something or hit its cap. "Spent $0.00 · 0
 * tokens" on a job that has not started spending told the person nothing.
 */
export function costWorthShowing(budget: RunBudgetView | null | undefined): boolean {
  if (!budget) return false;
  const spent = budget.committed.usdMicros + budget.reserved.usdMicros + budget.committed.tokens + budget.reserved.tokens;
  return spent > 0 || budget.exhausted;
}

/**
 * `part`: a job's panel splits these between its tabs (BGUI-3): what it reached, what was hidden and what it cost
 * (`spend`) under the Brief's "How it came out"; its workspace versions (`versions`) with its terminal on Machine.
 */
export function RunDetailsView({
  run,
  timeline,
  part = 'all',
}: {
  run: DurableAgentRunHandle;
  timeline: RunTimelineView | null;
  part?: 'all' | 'spend' | 'versions';
}) {
  if (!timeline) return null;
  const spend = part !== 'versions';
  // An engine run, or a turn whose children (its code_task hand-off, CTL-6) worked in the session workspace.
  const versions = part !== 'spend' && (timeline.engine !== null || timeline.children.length > 0) && !runStillActive(timeline.status);
  const sites = spend && (timeline.sites?.length ?? 0) > 0;
  const hidden = spend && (timeline.secretsHidden?.count ?? 0) > 0;
  // The chat's own run details keep the line whatever it says; a job's Brief only once it says something.
  const budget = spend && (part === 'spend' ? costWorthShowing(timeline.budget) : Boolean(timeline.budget));
  if (!sites && !hidden && !budget && !versions) return null;
  return (
    <Flex direction="column" gap="2" style={{ paddingTop: 'var(--space-3)' }} data-testid={part === 'all' ? undefined : `run-details-${part}`}>
      {sites ? <OutsideAccessList sites={timeline.sites} /> : null}
      {hidden ? <SecretsHiddenLine hidden={timeline.secretsHidden} /> : null}
      {budget && timeline.budget ? <RunCost budget={timeline.budget} /> : null}
      {versions ? <WorkspaceVersionsPanel session={run} /> : null}
    </Flex>
  );
}

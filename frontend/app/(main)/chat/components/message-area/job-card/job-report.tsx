'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AgentMark } from '@/app/components/harness-jobs';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { agentName } from '@/lib/harness-jobs/engine-worker';
import { useJobTimeline } from '@/lib/harness-jobs/use-job-timeline';
import { CUT_SHORT_KEYS } from '../../../cut-short';
import { useDurableSession, type DurableSessionRoute } from '../../../hooks/use-run-timeline';
import type { ChatArtifact, MessagePart } from '../../../types';
import { openJob, type JobPanelSection } from '../../activity-panel/activity-panel-store';
import { useMessageRun } from '../../activity-panel/use-activity-run';
import { ArtifactCard } from '../artifacts-panel';
import { JobsApi, useJobsStore } from './data';
import { failureLine, inFinishOrder, jobReportFacts, reportOutcome, type JobReportFacts } from './job-report-state';
import { previewJobFile } from './preview-job-file';
import { useOnScreen } from './use-job-stream';

/** A plain button: the chat's own look, and the focus ring of `.job-card-control` (globals.css). */
const CONTROL_RESET: React.CSSProperties = { appearance: 'none', background: 'none', border: 'none', margin: 0, padding: 0, font: 'inherit', color: 'inherit' };

/** The files it only worked with, folded into one line that opens its Outputs tab, where they are listed. */
function WorkingFiles({ count, onOpen, tap }: { count: number; onOpen?: () => void; tap?: number }) {
  const { t } = useTranslation();
  const label = (
    <>
      <MaterialIcon name="folder_open" size={ICON_SIZES.SECONDARY} color="var(--gray-11)" />
      <Text size="2" style={{ flex: 1, minWidth: 0, textAlign: 'left', color: onOpen ? 'var(--gray-12)' : 'var(--gray-11)' }}>
        {t('jobReport.workingFiles', { count })}
      </Text>
      {onOpen ? <MaterialIcon name="chevron_right" size={ICON_SIZES.SECONDARY} color="var(--gray-11)" /> : null}
    </>
  );
  const style: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minHeight: tap ?? 32, minWidth: 0, padding: '0 var(--space-1)' };
  return onOpen ? (
    <button type="button" className="job-card-control" data-testid="job-report-working" onClick={onOpen} style={{ ...CONTROL_RESET, ...style, cursor: 'pointer', borderRadius: 'var(--radius-2)' }}>
      {label}
    </button>
  ) : (
    <div data-testid="job-report-working" style={style}>
      {label}
    </div>
  );
}

export interface JobReportProps {
  part: MessagePart;
  facts: JobReportFacts;
  /** The job's name, as its card names it. */
  title: string;
  /** Files the turn's answer already shows, by record id or name: drawn once, there. */
  shownFiles: ReadonlySet<string>;
}

const shownIn = (shownFiles: ReadonlySet<string>) => (file: ChatArtifact) =>
  Boolean((file.recordId && shownFiles.has(file.recordId)) || shownFiles.has(file.fileName));

/**
 * One finished job reporting in the chat (design `JobReport`, §6B.42; BGUI-11): a byline (its agent's mark, its
 * name, which opens it, and how it ended, red only when it couldn't finish; "Partial" when a limit cut it short),
 * why it couldn't finish, the files it delivered as the answer's own file cards, and its working files folded into
 * one line that opens its Outputs tab. Never its raw reply: the chat's answer below sums it up for the person, and
 * its own words stay in its panel. Its own card stays just above: this is a byline, not a second card.
 */
export function JobReport({ part, facts, title, shownFiles }: JobReportProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const runId = facts.runId;
  // Null for a shared viewer: the job's panel is its owner's (D14), so nothing here opens it.
  const owner = useMessageRun(runId, false) !== null;
  const operatorStop = useJobsStore((s) => s.extras[runId]?.operatorStop ?? null);
  const storedEngine = useJobsStore((s) => s.extras[runId]?.engine ?? null);
  const engine = part.codeTaskHandoff?.engine ?? storedEngine;
  const tap = isMobile ? 44 : undefined;
  const open = (section: JobPanelSection) => (owner ? () => void openJob(runId, section) : undefined);
  const done = facts.state.status === 'done';
  const failure = failureLine(facts.state);
  const outcome = reportOutcome(facts, operatorStop, t);
  const delivered = facts.delivered.filter((file) => !shownIn(shownFiles)(file));
  const nameOpens = open(done ? 'brief' : 'activity');

  return (
    <Flex
      direction="column"
      gap="2"
      className="agent-activity-enter"
      role="group"
      aria-label={t('jobReport.groupLabel', { title, outcome })}
      data-testid="job-report"
      data-run-id={runId}
      data-status={facts.state.status}
      {...(facts.partial ? { 'data-partial': facts.partial } : {})}
      style={{ minWidth: 0 }}
    >
      {/* No wrap: on a phone a byline longer than the line wrapped whole, leaving the agent's mark alone above it. */}
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Flex align="center" style={{ flexShrink: 0 }} title={agentName(engine)}>
          <AgentMark agent={agentName(engine)} size={14} />
        </Flex>
        <Text size="2" data-testid="job-report-byline" style={{ flex: '0 1 auto', minWidth: 0, color: 'var(--gray-11)' }}>
          {nameOpens ? (
            <button
              type="button"
              className="job-card-control"
              data-testid="job-report-open"
              onClick={nameOpens}
              style={{ ...CONTROL_RESET, cursor: 'pointer', fontWeight: 500, color: 'var(--gray-12)', minHeight: tap, textAlign: 'left', borderRadius: 'var(--radius-1)' }}
            >
              {title}
            </button>
          ) : (
            <span style={{ fontWeight: 500, color: 'var(--gray-12)' }}>{title}</span>
          )}
          <span data-testid="job-report-outcome" style={facts.state.status === 'couldntFinish' ? { color: 'var(--red-11)' } : undefined}>
            {` · ${outcome}`}
          </span>
        </Text>
        {facts.partial ? (
          <Badge size="1" color="gray" variant="soft" data-testid="job-report-partial" style={{ flexShrink: 0 }}>
            {t('jobReport.partial')}
          </Badge>
        ) : null}
      </Flex>
      {facts.partial && done ? (
        <Text size="1" color="gray" data-testid="job-report-cut">
          {t(CUT_SHORT_KEYS[facts.partial])}
        </Text>
      ) : null}
      {failure ? (
        <Text size="2" data-testid="job-report-failure" style={{ color: 'var(--gray-11)' }}>
          {failure}
        </Text>
      ) : null}
      {delivered.length > 0 ? (
        <Flex direction="column" gap="2" data-testid="job-report-delivered" style={{ minWidth: 0 }}>
          {delivered.map((file) => (
            <ArtifactCard key={file.id} artifact={file} onPreview={file.recordId ? previewJobFile : undefined} touch={isMobile} />
          ))}
        </Flex>
      ) : null}
      {facts.working ? <WorkingFiles count={facts.working} onOpen={open('outputs')} tap={tap} /> : null}
    </Flex>
  );
}

/** One job's report facts, reading its timeline once it ended and is on screen (its owner only, D14). */
function useReportFacts(part: MessagePart, session: DurableSessionRoute | null, onScreen: boolean, endedAt: number | null | undefined): JobReportFacts | null {
  const runId = part.runId;
  const owner = useMessageRun(runId ?? '', false) !== null;
  const stream = useJobsStore((s) => (runId ? s.streams[runId] : undefined));
  const operatorStop = useJobsStore((s) => (runId ? (s.extras[runId]?.operatorStop ?? null) : null));
  const partEnded = part.status !== 'running';
  const { timeline } = useJobTimeline(session, runId, { enabled: owner && partEnded && onScreen, ended: true });
  return useMemo(
    () => jobReportFacts({ part, stream, timeline, operatorStop, isOwner: owner, endedAt }),
    [part, stream, timeline, operatorStop, owner, endedAt],
  );
}

/** Each job's report as a hook per job: a row of hooks needs a component per job. */
function ReportSlot({
  part,
  session,
  endedAt,
  onFacts,
}: {
  part: MessagePart;
  session: DurableSessionRoute | null;
  endedAt: number | null | undefined;
  onFacts: (runId: string, facts: JobReportFacts | null) => void;
}) {
  const [ref, onScreen] = useOnScreen<HTMLSpanElement>();
  const facts = useReportFacts(part, session, onScreen, endedAt);
  const runId = part.runId ?? '';
  useEffect(() => onFacts(runId, facts), [runId, facts, onFacts]);
  return <span ref={ref} aria-hidden="true" style={{ display: 'block', height: 0 }} />;
}

/** When each of a request's jobs ended, from one read of the request (UI-B5): a reload's finish order. */
function useEndedAt(session: DurableSessionRoute | null, requestRunId: string | undefined, enabled: boolean): ReadonlyMap<string, number> {
  const [ends, setEnds] = useState<ReadonlyMap<string, number>>(new Map());
  const agentId = session?.agentId;
  const sessionId = session?.sessionId;
  useEffect(() => {
    if (!enabled || !sessionId || !requestRunId) return undefined;
    let current = true;
    JobsApi.requestJobs({ ...(agentId ? { agentId } : {}), sessionId }, requestRunId)
      .then((req) => {
        if (!current) return;
        setEnds(new Map(req.jobs.flatMap((j) => (j.endedAt !== null ? [[j.runId, j.endedAt] as [string, number]] : []))));
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [enabled, agentId, sessionId, requestRunId]);
  return ends;
}

export interface JobReportsProps {
  /** The turn's jobs, in hand-out order, as `TurnJobs` draws their cards. */
  jobs: MessagePart[];
  shownFiles?: ReadonlySet<string>;
  titleOf: (part: MessagePart) => string;
}

const NO_FILES: ReadonlySet<string> = new Set();

/**
 * A turn's finished jobs reporting back, in the order they finished (design §6B.42), after their cards and before
 * the turn's answer, which is PipesHub's one summary for the person (BGUI-11: a job's raw reply repeated it, process
 * talk and file directives included). Each says what the answer cannot: how it ended, and what it delivered and worked
 * with. One job always says how it ended ("finished in 6m 27s"); of several, one done with nothing to show has no
 * report: its card already says Done.
 */
export function JobReports({ jobs, shownFiles = NO_FILES, titleOf }: JobReportsProps) {
  const { t } = useTranslation();
  const session = useDurableSession();
  const [byRun, setByRun] = useState<Readonly<Record<string, JobReportFacts | null>>>({});
  const onFacts = React.useCallback((runId: string, facts: JobReportFacts | null) => {
    setByRun((held) => (held[runId] === facts ? held : { ...held, [runId]: facts }));
  }, []);
  const single = jobs.length === 1;
  const owner = useMessageRun(jobs[0]?.runId ?? '', false) !== null;
  const endedNoClock = jobs.filter((p) => byRun[p.runId ?? '']?.finishedAt === undefined && p.status !== 'running').length;
  const ended = jobs.filter((p) => p.status !== 'running').length;
  const ends = useEndedAt(session, jobs[0]?.parentRunId, owner && !single && ended > 1 && endedNoClock > 0);

  const reports = useMemo(() => {
    const list: Array<{ part: MessagePart; facts: JobReportFacts }> = [];
    for (const part of jobs) {
      const facts = byRun[part.runId ?? ''];
      if (!facts) continue;
      const files = facts.delivered.some((file) => !shownIn(shownFiles)(file)) || (facts.working ?? 0) > 0;
      if (!single && facts.state.status === 'done' && !facts.partial && !files) continue;
      list.push({ part, facts });
    }
    return inFinishOrder(list.map((r) => ({ ...r, finishedAt: r.facts.finishedAt })));
  }, [jobs, byRun, single, shownFiles]);

  if (jobs.length === 0) return null;
  return (
    <>
      {jobs.map((part) =>
        part.runId ? <ReportSlot key={part.runId} part={part} session={session} endedAt={ends.get(part.runId)} onFacts={onFacts} /> : null,
      )}
      {reports.length > 0 ? (
        <Flex direction="column" gap="5" role="group" aria-label={t('jobReport.region')} data-testid="job-reports" style={{ marginTop: 'var(--space-3)', minWidth: 0 }}>
          {reports.map(({ part, facts }) => (
            <JobReport key={facts.runId} part={part} facts={facts} title={titleOf(part)} shownFiles={shownFiles} />
          ))}
        </Flex>
      ) : null}
    </>
  );
}

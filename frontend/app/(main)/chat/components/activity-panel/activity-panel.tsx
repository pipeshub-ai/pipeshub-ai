'use client';

import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Badge, Box, Button, Flex, IconButton, Spinner, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { activityAnnouncements, isRunFinished, type ActivityAnnouncement } from '../../activity';
import { useRunActivity, type RunActivityState } from '../../hooks/use-run-activity';
import { useRunModelLabel } from '../../hooks/use-run-model-label';
import type { ActivityCommand, ActivityTool, ActivityView, DurableAgentRunHandle } from '../../types';
import { withEngineSteps, withoutHelpers } from '../../engine-steps';
import { useJobTimeline, useJobsStore } from './job-data';
import {
  ACTIVITY_PANEL_ID,
  useActivityPanelStore,
  type ActivitySection,
  type JobPanelSection,
  type PanelRun,
} from './activity-panel-store';
import { useChatStore } from '../../store';
import type { RunTimelineView } from '../../types';
import { JobHeader } from './job-header';
import { JobTabs } from './job-panel-tabs';
import { usePanelJob, usePartEnded, type PanelJob } from './use-panel-job';
import { CommandBlock } from './command-block';
import { WorkspaceTree } from './workspace-tree';
import { FullLogDialog } from './full-log-dialog';
import { useFollowTail } from './use-follow-tail';
import { StepList, useRunSteps } from './step-row';
import { engineRunTools } from './activity-steps';
import {
  COMMANDS_VIRTUALIZE_AFTER,
  FileRow,
  Section,
  TaskRow,
  commandKey,
  estimateCommand,
  estimateFile,
  estimateTask,
  fileKey,
  taskKey,
} from './activity-rows';
import { RunDetails } from './run-details';
import { VirtualRows } from '../virtual-rows';

export { RunDetails } from './run-details';

function Icon({ name, size = 16, color }: { name: string; size?: number; color?: string }) {
  return (
    <span aria-hidden="true" style={{ display: 'inline-flex' }}>
      <MaterialIcon name={name} size={size} color={color} />
    </span>
  );
}

/** Literal keys per case, so `i18n:keys` checks each one. */
function announcementText(t: TFunction, item: ActivityAnnouncement): string {
  switch (item.kind) {
    case 'commandStarted':
      return t('activityPanel.announce.started', { command: item.command });
    case 'commandFinished':
      if (item.status === 'timed_out') return t('activityPanel.announce.timedOut', { command: item.command });
      if (item.status === 'cancelled') return t('activityPanel.announce.cancelled', { command: item.command });
      if (item.exitCode !== null) {
        return t('activityPanel.announce.finishedExit', { command: item.command, code: item.exitCode });
      }
      return item.status === 'failed'
        ? t('activityPanel.announce.failed', { command: item.command })
        : t('activityPanel.announce.finished', { command: item.command });
    case 'approvalNeeded':
      return t('activityPanel.announce.approval', { tool: item.tool });
    case 'runFinished':
      if (item.status === 'failed') return t('activityPanel.announce.runFailed');
      if (item.status === 'cancelled') return t('activityPanel.announce.runCancelled');
      return t('activityPanel.announce.runCompleted');
    default:
      return '';
  }
}

function RunStatus({ view }: { view: ActivityView }) {
  const { t } = useTranslation();
  switch (view.status) {
    case 'queued':
      return <Badge color="gray" variant="soft" highContrast>{t('activityPanel.status.queued')}</Badge>;
    case 'running':
      return (
        <Badge color="blue" variant="soft" highContrast>
          <Spinner size="1" />
          {t('activityPanel.status.running')}
        </Badge>
      );
    case 'waiting':
      return <Badge color="amber" variant="soft" highContrast>{t('activityPanel.status.waiting')}</Badge>;
    case 'completed':
      return <Badge color="green" variant="soft" highContrast>{t('activityPanel.status.completed')}</Badge>;
    case 'failed':
      return <Badge color="red" variant="soft" highContrast>{t('activityPanel.status.failed')}</Badge>;
    default:
      return <Badge color="gray" variant="soft" highContrast>{t('activityPanel.status.cancelled')}</Badge>;
  }
}

function isEmpty(view: ActivityView, tools: readonly ActivityTool[]): boolean {
  return !view.commands.length && !tools.length && !view.tasks.length && !view.files.length;
}

export interface ActivityPanelViewProps {
  run: DurableAgentRunHandle;
  state: RunActivityState;
  /** `pane`: beside the chat with its own header; `sheet`: inside the phone's bottom sheet, which has one. */
  variant: 'pane' | 'sheet';
  onClose: () => void;
  /** Below the run's activity: what it cost and, for an engine run, its workspace versions (CTL-4). */
  footer?: React.ReactNode;
  /**
   * A background job's panel (D4, BGUI-3): its header with Stop, then the Brief, Activity and Machine tabs. `owner`:
   * the person on screen started it; `viewer`: they did not (a shared chat). Neither while the conversation has not
   * said yet. `timeline`: the job's own, read once while it runs and once when it ended (D13).
   */
  job?: { view: PanelJob; owner: boolean; viewer: boolean; timeline?: RunTimelineView | null };
}

function Reconnecting({ inset }: { inset: boolean }) {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      gap="2"
      data-testid="activity-reconnecting"
      style={{ margin: inset ? 'var(--space-2) var(--space-4) 0' : 0, padding: 'var(--space-1) var(--space-2)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--amber-a3)' }}
    >
      <Spinner size="1" />
      <Text size="1" style={{ color: 'var(--amber-11)' }}>
        {t('activityPanel.reconnecting')}
      </Text>
    </Flex>
  );
}

/** The panel as it renders a given state; read-only: there is no input anywhere in it (D-2). */
export function ActivityPanelView({ run, state, variant, onClose, footer, job }: ActivityPanelViewProps) {
  const { t } = useTranslation();
  const { view, connection } = state;
  const [logCommand, setLogCommand] = useState<ActivityCommand | null>(null);
  const focusSection = useActivityPanelStore((s) => s.focusSection);
  const sectionFocused = useActivityPanelStore((s) => s.sectionFocused);
  const filesHeader = useRef<HTMLButtonElement>(null);
  const briefTab = useRef<HTMLButtonElement>(null);
  const activityTab = useRef<HTMLButtonElement>(null);
  const outputsTab = useRef<HTMLButtonElement>(null);
  const machineTab = useRef<HTMLButtonElement>(null);
  const tabRefs = useMemo<Record<JobPanelSection, React.RefObject<HTMLButtonElement | null>>>(
    () => ({ brief: briefTab, activity: activityTab, outputs: outputsTab, machine: machineTab }),
    [],
  );
  const headingId = useId();

  const lastCommand = view?.commands[view.commands.length - 1];
  const { ref: bodyRef, onScroll: onBodyScroll, hasNew, following, jumpToLatest } = useFollowTail<HTMLDivElement>(
    view ? `${view.commands.length}:${lastCommand?.status ?? ''}:${view.tools.length}:${view.tools[view.tools.length - 1]?.status ?? ''}:${view.tasks.length}:${view.files.length}` : '',
    { stickOnResize: true },
  );

  const [announcement, setAnnouncement] = useState('');
  const previous = useRef<ActivityView | null>(null);
  useEffect(() => {
    const items = activityAnnouncements(previous.current, view);
    previous.current = view;
    if (items.length) setAnnouncement(items.map((item) => announcementText(t, item)).join(' '));
  }, [view, t]);

  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focusSection === 'panel') {
      // The panel slides in from the right: a scrolling focus would pull the chat's row sideways under the sidebar.
      root.current?.focus({ preventScroll: true });
      sectionFocused();
      return;
    }
    // A job's sections are its tabs: a request for one lands on its tab, which the tab row has already selected.
    const headers: Partial<Record<ActivitySection, React.RefObject<HTMLButtonElement | null>>> = job
      ? { brief: briefTab, tools: activityTab, files: outputsTab, commands: machineTab }
      : { files: filesHeader };
    const header = focusSection ? headers[focusSection]?.current : null;
    if (header) {
      if (!job) header.scrollIntoView?.({ block: 'start' });
      header.focus(job ? { preventScroll: true } : undefined);
      sectionFocused();
      return;
    }
    // A section that is not drawn yet waits for the view; once it is there and the section is not, nothing to do.
    if (focusSection && view) sectionFocused();
  }, [focusSection, view, sectionFocused, job]);

  const running = view ? !isRunFinished(view.status) : false;
  const modelLabel = useRunModelLabel(view?.model);
  // WP115-FU-1: an engine run's tool steps are its progress lines, which the activity does not list; read as a child's.
  const engineRun = view?.engine === true;
  // A job's steps are its own stream's frames, shared with its card (D13): no timeline read for them.
  const answerEngine = engineRun && !job;
  const { parts: readSteps } = useRunSteps(run, answerEngine ? view?.runId : undefined, {
    enabled: answerEngine,
    follow: running,
    refreshKey: view ? `${view.status}:${view.commands.length}:${view.files.length}:${view.tools.length}` : '',
  });
  const jobFrames = job?.view.frames;
  const jobEnded = job ? !job.view.live : false;
  const ownSteps = useMemo(
    () =>
      job
        ? engineRun && jobFrames?.length
          ? withoutHelpers(withEngineSteps([{ type: 'engine_task', runId: run.runId, frames: [...jobFrames] }], jobEnded))
          : []
        : readSteps,
    [job, engineRun, jobFrames, jobEnded, run.runId, readSteps],
  );
  const tools = useMemo(
    () => (!view ? [] : engineRun ? engineRunTools(ownSteps, view.tools) : view.tools),
    [view, engineRun, ownSteps],
  );

  return (
    <Flex
      ref={root}
      id={ACTIVITY_PANEL_ID}
      role="complementary"
      aria-labelledby={headingId}
      tabIndex={-1}
      direction="column"
      data-testid="activity-panel"
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || logCommand) return;
        event.stopPropagation();
        onClose();
      }}
      style={{ height: '100%', minHeight: 0, outline: 'none' }}
    >
      {job ? (
        <Flex direction="column" style={{ flexShrink: 0 }}>
          <JobHeader run={run} job={job.view} owner={job.owner} variant={variant} headingId={headingId} onClose={onClose} />
          {connection === 'reconnecting' ? <Reconnecting inset={variant === 'pane'} /> : null}
        </Flex>
      ) : null}
      {job?.owner ? (
        <JobTabs
          run={run}
          view={view}
          connection={connection}
          tools={tools}
          job={job.view}
          engine={engineRun}
          timeline={job.timeline ?? null}
          variant={variant}
          tabRefs={tabRefs}
          onOpenLog={setLogCommand}
          onClose={onClose}
        />
      ) : null}
      {job?.viewer ? (
        <Flex direction="column" gap="1" data-testid="job-panel-owner-only" style={{ padding: variant === 'pane' ? 'var(--space-4)' : 'var(--space-4) 0' }}>
          <Text size="2" color="gray">
            {t('jobPanel.ownerOnly')}
          </Text>
        </Flex>
      ) : null}
      {job ? null : (
      <>
      <Flex direction="column" gap="1" style={{ padding: variant === 'pane' ? 'var(--space-3) var(--space-4)' : '0 0 var(--space-2)', flexShrink: 0 }}>
        <Flex align="center" gap="2">
          <Icon name="terminal" size={18} color="var(--gray-11)" />
          <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
            <Text id={headingId} size="3" weight="medium">
              {variant === 'pane' ? t('activityPanel.title') : <VisuallyHidden>{t('activityPanel.title')}</VisuallyHidden>}
            </Text>
            {variant === 'pane' ? <BetaBadge /> : null}
          </Flex>
          {view ? <RunStatus view={view} /> : null}
          {variant === 'pane' ? (
            <IconButton variant="ghost" color="gray" size="2" onClick={onClose} aria-label={t('activityPanel.close')}>
              <Icon name="close" size={18} />
            </IconButton>
          ) : null}
        </Flex>
        <Text size="1" color="gray" data-testid="activity-subtitle">
          {t(view?.personRan ? 'activityPanel.subtitlePersonRan' : 'activityPanel.subtitle')}
        </Text>
        {modelLabel && !view?.personRan ? (
          <Text size="1" color="gray" data-testid="activity-model">
            {t('activityPanel.model', { model: modelLabel })}
          </Text>
        ) : null}
        {view?.slashCommand ? (
          <Text size="1" color="gray" data-testid="activity-slash-command">
            {t('activityPanel.slashCommand', {
              name: view.slashCommand.name,
              version: view.slashCommand.version,
              chars: view.slashCommand.expandedChars,
            })}
          </Text>
        ) : null}
        {connection === 'reconnecting' ? <Reconnecting inset={false} /> : null}
      </Flex>

      <Box style={{ position: 'relative', flex: 1, minHeight: 0 }}>
        <Box
          ref={bodyRef}
          onScroll={onBodyScroll}
          style={{ height: '100%', overflowY: 'auto', padding: variant === 'pane' ? '0 var(--space-4) var(--space-4)' : 0 }}
        >
          <div>
            {connection === 'unavailable' ? (
              <Flex direction="column" gap="1" data-testid="activity-unavailable" style={{ padding: 'var(--space-4) 0' }}>
                <Text size="2" weight="medium">
                  {t('activityPanel.unavailable')}
                </Text>
                <Text size="1" color="gray">
                  {t('activityPanel.unavailableHint')}
                </Text>
              </Flex>
            ) : null}
            {connection !== 'unavailable' && !view ? (
              <Flex align="center" gap="2" style={{ padding: 'var(--space-4) 0' }}>
                <Spinner size="2" />
                <Text size="2" color="gray">
                  {t('activityPanel.loading')}
                </Text>
              </Flex>
            ) : null}
            {view && connection !== 'unavailable' && isEmpty(view, tools) ? (
              <Flex direction="column" gap="1" data-testid="activity-empty" style={{ padding: 'var(--space-6) 0', alignItems: 'center', textAlign: 'center' }}>
                <Icon name="terminal" size={28} color="var(--gray-11)" />
                <Text size="2" weight="medium">
                  {t('activityPanel.empty')}
                </Text>
                <Text size="1" color="gray">
                  {running ? t('activityPanel.emptyRunning') : t('activityPanel.emptyDone')}
                </Text>
              </Flex>
            ) : null}
            {view && connection !== 'unavailable' && !isEmpty(view, tools) ? (
              <Flex direction="column" gap="1">
                {view.tasks.length ? (
                  <Section section="plan" title={t('activityPanel.section.plan')} count={view.tasks.length} caption={running ? t('activityPanel.planCaption') : undefined}>
                    <VirtualRows
                      items={view.tasks}
                      getKey={taskKey}
                      estimateSize={estimateTask}
                      gap="var(--space-2)"
                      renderRow={(task) => <TaskRow task={task} />}
                    />
                  </Section>
                ) : null}
                {view.commands.length ? (
                  <Section section="commands" title={t('activityPanel.section.commands')} count={view.commands.length + view.omittedCommands}>
                    {view.omittedCommands > 0 ? (
                      <Text size="1" color="gray">
                        {t('activityPanel.omittedCommands', { count: view.omittedCommands })}
                      </Text>
                    ) : null}
                    <VirtualRows
                      items={view.commands}
                      getKey={commandKey}
                      estimateSize={estimateCommand}
                      gap="var(--space-2)"
                      threshold={COMMANDS_VIRTUALIZE_AFTER}
                      renderRow={(command) => <CommandBlock command={command} onOpenLog={() => setLogCommand(command)} />}
                    />
                  </Section>
                ) : null}
                {tools.length ? (
                  <Section section="tools" title={t('activityPanel.section.tools')} count={tools.length}>
                    <StepList tools={tools} run={run} />
                  </Section>
                ) : null}
                {view.files.length ? (
                  <Section section="files" title={t('activityPanel.section.files')} count={view.files.length} headerRef={filesHeader}>
                    <VirtualRows
                      items={view.files}
                      getKey={fileKey}
                      estimateSize={estimateFile}
                      gap="var(--space-2)"
                      renderRow={(file) => <FileRow file={file} />}
                    />
                  </Section>
                ) : null}
                <WorkspaceTree run={run} />
              </Flex>
            ) : null}
            {footer}
          </div>
        </Box>
        {hasNew && !following ? (
          <Button
            size="1"
            onClick={jumpToLatest}
            style={{ position: 'absolute', left: '50%', transform: 'translateX(-50%)', bottom: 'var(--space-3)' }}
          >
            <Icon name="arrow_downward" size={14} />
            {t('activityPanel.jumpToLatest')}
          </Button>
        ) : null}
      </Box>
      </>
      )}

      <VisuallyHidden aria-live="polite" data-testid="activity-live">
        {announcement}
      </VisuallyHidden>
      {logCommand ? <FullLogDialog run={run} command={logCommand} onClose={() => setLogCommand(null)} /> : null}
    </Flex>
  );
}

type PanelProps = Omit<ActivityPanelViewProps, 'state' | 'footer' | 'job' | 'run'> & { run: PanelRun };

/** The panel for one run; mounted only while open, so its stream runs only then (D-3). */
export function ActivityPanel({ run, variant, onClose }: PanelProps) {
  if (run.job) return <JobPanel run={run} variant={variant} onClose={onClose} />;
  return <AnswerPanel run={run} variant={variant} onClose={onClose} />;
}

function AnswerPanel({ run, variant, onClose }: PanelProps) {
  const state = useRunActivity(run, true);
  const finished = state.view ? isRunFinished(state.view.status) : false;
  return (
    <ActivityPanelView
      run={run}
      state={state}
      variant={variant}
      onClose={onClose}
      footer={<RunDetails run={run} finished={finished} />}
    />
  );
}

/**
 * A job's panel (D4). Its activity, timeline, brief, terminal and files are the owner's routes (D14): a viewer of a
 * shared chat gets the header from the job's stream and a plain line, and none of those reads is made.
 */
function JobPanel({ run, variant, onClose }: PanelProps) {
  const isOwner = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.isOwner ?? null) : null));
  // No owner route is read before the conversation says whose it is: a viewer's reads would only be refused.
  const owner = isOwner === true;
  const state = useRunActivity(owner ? run : null, owner);
  const stream = useJobsStore((s) => s.streams[run.runId]);
  const partEnded = usePartEnded(run.runId);
  // One read while it runs and one once it ended, shared with its card and its steps (D13, ADR-CJ-01). It ended
  // when its stream, its part in the chat or its activity says so; until one of them has spoken nothing is read.
  const known = stream !== undefined || partEnded !== null || state.view !== null;
  const ended = Boolean(stream?.ended) || partEnded === true || (state.view ? isRunFinished(state.view.status) : false);
  const { timeline } = useJobTimeline(run, run.runId, { enabled: owner && known, ended });
  const job = usePanelJob(run, state.view, owner ? timeline : null);
  return (
    <ActivityPanelView
      run={run}
      state={state}
      variant={variant}
      onClose={onClose}
      job={{ view: job, owner, viewer: isOwner === false, timeline: owner ? timeline : null }}
    />
  );
}


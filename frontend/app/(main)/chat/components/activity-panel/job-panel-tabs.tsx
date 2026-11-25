'use client';

import React from 'react';
import { Badge, Box, Button, Flex, Separator, Spinner, Tabs, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ActivityCommand, ActivityTool, ActivityView, DurableAgentRunHandle, RunTimelineView } from '../../types';
import type { RunActivityState } from '../../hooks/use-run-activity';
import { VirtualRows } from '../virtual-rows';
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
import { useActivityPanelStore, type JobPanelSection } from './activity-panel-store';
import { CommandBlock } from './command-block';
import { JobAskNotice } from './job-header';
import { JobBriefBody } from './job-brief';
import { JobReply } from '../message-area/job-card/job-reply';
import { JobOutputs } from './job-outputs';
import { RunDetailsView, costWorthShowing } from './run-details';
import { StepList } from './step-row';
import { useFollowTail } from './use-follow-tail';
import type { PanelJob } from './use-panel-job';
import { WorkspaceTree } from './workspace-tree';

/** Phone hit areas: 44 px (frontend/CLAUDE.md). */
const TOUCH_HEIGHT = 44;

/** The tab row's words, literal per key so `i18n:keys` checks each one. */
function tabTitle(t: TFunction, section: JobPanelSection): string {
  switch (section) {
    case 'brief':
      return t('jobPanel.brief');
    case 'activity':
      return t('jobPanel.activity');
    case 'outputs':
      return t('jobPanel.outputs');
    default:
      return t('jobPanel.machine');
  }
}

function Nothing({ text, testId }: { text: string; testId: string }) {
  return (
    <Text size="1" color="gray" data-testid={testId}>
      {text}
    </Text>
  );
}

/** A tab's body: it scrolls on its own, so the header and the tab row stay put. */
function Pane({
  value,
  shown,
  phone,
  children,
  paneRef,
  onScroll,
}: {
  value: JobPanelSection;
  shown: boolean;
  phone: boolean;
  children: React.ReactNode;
  paneRef?: React.Ref<HTMLDivElement>;
  onScroll?: () => void;
}) {
  return (
    // Every tab stays mounted (as the one column was), so a fold or a scroll position survives a tab change and
    // Activity keeps following its steps while another tab shows.
    <Tabs.Content
      value={value}
      forceMount
      ref={paneRef}
      onScroll={onScroll}
      data-testid={`job-tab-${value}`}
      style={{
        display: shown ? 'block' : 'none',
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        padding: phone ? 'var(--space-3) 0 var(--space-4)' : 'var(--space-3) var(--space-4) var(--space-5)',
        outline: 'none',
      }}
    >
      <div>{children}</div>
    </Tabs.Content>
  );
}

export interface JobTabsProps {
  run: DurableAgentRunHandle;
  view: ActivityView | null;
  connection: RunActivityState['connection'];
  tools: readonly ActivityTool[];
  job: PanelJob;
  engine: boolean;
  timeline: RunTimelineView | null;
  variant: 'pane' | 'sheet';
  /** Where a request for focus (a link, a tab chosen) lands: the tab itself. */
  tabRefs: Record<JobPanelSection, React.RefObject<HTMLButtonElement | null>>;
  onOpenLog: (command: ActivityCommand) => void;
  onClose: () => void;
}

/**
 * A job's tabs (design `worker-panel.tsx`, BGUI-0: one view at a time, `?section=` names the tab):
 *   Brief     what it was asked, a fold with what it was given, how it changed, then how it came out (its own
 *             reply, drawn as an answer is, what it reached, what it cost);
 *   Activity  its plan and steps in plain words, collapsed, the ask it waits on;
 *   Outputs   what it delivered, as file cards, then its working files folded, each with its diff (BGUI-11);
 *   Machine   its terminal, the commands it ran with their output, and its workspace with versions.
 * The design's live screen and Take control need a screen stream the sandbox does not have, so Machine is the
 * terminal and says nothing of a screen. A loop job has no machine: its tab is not drawn.
 */
export function JobTabs({ run, view, connection, tools, job, engine, timeline, variant, tabRefs, onOpenLog, onClose }: JobTabsProps) {
  const { t } = useTranslation();
  const phone = variant === 'sheet';
  const jobSection = useActivityPanelStore((s) => s.jobSection);
  const jumpTo = useActivityPanelStore((s) => s.jumpTo);
  // Until the activity says whether it is an engine job, a link to Machine keeps its tab rather than bounce off it.
  const machine = engine || (view === null && jobSection === 'machine');
  const sections: JobPanelSection[] = machine ? ['brief', 'activity', 'outputs', 'machine'] : ['brief', 'activity', 'outputs'];
  // A live job opens on what it is doing, an ended one on its brief (§4.2); a tab the URL names wins.
  const landed: JobPanelSection = job.live ? 'activity' : 'brief';
  const current = jobSection && sections.includes(jobSection) ? jobSection : landed;

  const follow = useFollowTail<HTMLDivElement>(
    `${current}:${view ? `${tools.length}:${tools[tools.length - 1]?.status ?? ''}:${view.tasks.length}:${view.files.length}:${job.asks.length}` : ''}`,
    { stickOnResize: true },
  );
  const loading = connection !== 'unavailable' && !view;
  const pending = (): React.ReactNode => {
    if (connection === 'unavailable') return <Nothing text={t('activityPanel.unavailable')} testId="activity-unavailable" />;
    if (loading) return <Spinner size="1" />;
    return null;
  };
  const commandCount = view ? view.commands.length + view.omittedCommands : 0;
  const cameOut = Boolean(job.reply) || costWorthShowing(timeline?.budget) || (timeline?.sites?.length ?? 0) > 0 || (timeline?.secretsHidden?.count ?? 0) > 0;

  return (
    <Tabs.Root
      value={current}
      onValueChange={(value) => jumpTo(value as JobPanelSection)}
      data-testid="job-panel-body"
      style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}
    >
      <Tabs.List aria-label={t('jobPanel.sections')} size="2" data-testid="job-panel-tabs" style={{ flexShrink: 0, margin: phone ? 0 : '0 var(--space-4)' }}>
        {sections.map((section) => (
          <Tabs.Trigger key={section} value={section} ref={tabRefs[section]} style={phone ? { minHeight: TOUCH_HEIGHT, flex: 1 } : undefined}>
            {tabTitle(t, section)}
            {section === 'outputs' && job.outputs.length > 0 ? (
              // The tab's name is its visible text ("Outputs 1"): the space keeps the count a word of its own.
              <>
                {' '}
                  <Badge size="1" color="gray" variant="soft" radius="full" data-testid="job-tab-outputs-count">
                  {job.outputs.length}
                </Badge>
              </>
            ) : null}
          </Tabs.Trigger>
        ))}
      </Tabs.List>

      <Pane value="brief" shown={current === 'brief'} phone={phone}>
        <Flex direction="column" gap="4" data-testid="job-panel-brief">
          <JobBriefBody run={run} touch={phone} />
          {cameOut ? (
            <>
              <Separator size="4" />
              <Flex direction="column" gap="2" data-testid="job-panel-came-out">
                <Text size="1" weight="medium" color="gray">
                  {t('jobPanel.cameOut')}
                </Text>
                {job.reply ? <JobReply text={job.reply.text} citations={job.reply.citations} testId="job-panel-reply" /> : null}
                <RunDetailsView run={run} timeline={timeline} part="spend" />
              </Flex>
            </>
          ) : null}
        </Flex>
      </Pane>

      <Pane value="activity" shown={current === 'activity'} phone={phone} paneRef={follow.ref} onScroll={follow.onScroll}>
        <Flex direction="column" gap="3" data-testid="job-panel-activity">
          {view?.tasks.length ? (
            <Section section="plan" title={t('activityPanel.section.plan')} count={view.tasks.length}>
              <VirtualRows items={view.tasks} getKey={taskKey} estimateSize={estimateTask} gap="var(--space-2)" renderRow={(task) => <TaskRow task={task} />} />
            </Section>
          ) : null}
          <Box data-testid="activity-section-tools">
            {pending() ??
              (tools.length ? (
                <StepList tools={tools} run={run} />
              ) : engine && view?.commands.length ? (
                // An engine job that only ran commands did run (F7): they are listed once, on Machine.
                <Flex align="center" gap="2" wrap="wrap" data-testid="job-panel-commands-only">
                  <Text size="1" color="gray">
                    {t('jobPanel.ranCommandsMachine', { count: commandCount })}
                  </Text>
                  <Button size="1" variant="ghost" onClick={() => jumpTo('machine')} style={phone ? { minHeight: TOUCH_HEIGHT } : undefined}>
                    {t('jobPanel.showMachine')}
                  </Button>
                </Flex>
              ) : (
                <Nothing text={t('jobPanel.noSteps')} testId="job-panel-no-steps" />
              ))}
          </Box>
          <JobAskNotice job={job} phone={phone} onClose={onClose} />
        </Flex>
      </Pane>
      {follow.hasNew && !follow.following && current === 'activity' ? (
        <Flex justify="center" style={{ position: 'relative', height: 0 }}>
          <Button size="1" onClick={follow.jumpToLatest} style={{ position: 'absolute', bottom: 'var(--space-3)' }}>
            <span aria-hidden="true" style={{ display: 'inline-flex' }}>
              <MaterialIcon name="arrow_downward" size={14} />
            </span>
            {t('activityPanel.jumpToLatest')}
          </Button>
        </Flex>
      ) : null}

      <Pane value="outputs" shown={current === 'outputs'} phone={phone}>
        {/* What it delivered shows at once; only its working files wait for its activity. */}
        {(job.outputs.length === 0 ? pending() : null) ?? (
          <JobOutputs
            outputs={job.outputs}
            files={view?.files ?? null}
            live={job.live}
            phone={phone}
            runId={run.runId}
            {...(machine ? { onMachine: () => jumpTo('machine') } : {})}
          />
        )}
      </Pane>

      {machine ? (
        <Pane value="machine" shown={current === 'machine'} phone={phone}>
          <Flex direction="column" gap="3" data-testid="job-panel-machine">
            <Flex direction="column" gap="2" data-testid="activity-section-commands">
              <Flex align="center" gap="2">
                <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                  <MaterialIcon name="terminal" size={16} color="var(--gray-11)" />
                </span>
                <Text size="2" weight="medium" style={{ flex: 1 }}>
                  {t('jobPanel.terminal')}
                </Text>
                {view ? (
                  <Text size="1" color="gray">
                    {commandCount}
                  </Text>
                ) : null}
              </Flex>
              {pending() ??
                (view?.commands.length ? (
                  <>
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
                      renderRow={(command) => <CommandBlock command={command} onOpenLog={() => onOpenLog(command)} />}
                    />
                  </>
                ) : (
                  <Nothing text={t('jobPanel.noCommands')} testId="job-panel-no-commands" />
                ))}
            </Flex>
            {engine ? <WorkspaceTree run={run} /> : null}
            <RunDetailsView run={run} timeline={timeline} part="versions" />
          </Flex>
        </Pane>
      ) : null}
    </Tabs.Root>
  );
}

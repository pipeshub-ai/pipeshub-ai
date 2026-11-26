'use client';

import { useMemo, useState } from 'react';
import { Box, Button, Flex, IconButton, Spinner, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { touchTarget } from '@/app/components/ui/responsive';
import { AgentTile, GroupLabel, StatusGlyph } from '@/app/components/harness-jobs';
import { toast } from '@/lib/store/toast-store';
import { isLive } from '@/lib/harness-jobs/job-status';
import { finishedFolds, rowMeta, type JobRow } from '@/lib/harness-jobs/jobs-list';
import type { JobView } from '@/lib/harness-jobs/jobs';
import type { SessionRoute } from '@/lib/harness-jobs/jobs-api';
import { stopJob, useJobsStore } from '@/lib/harness-jobs/jobs-store';
import type { Words } from '@/lib/harness-jobs/words';
import { chatJobSections, tasksOf, TASKS_SHOWN, type TaskLine } from './chat-jobs';
import { useNow } from '../message-area/job-card/use-job-stream';

const ONE_LINE = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 } as const;

/** A row's right-hand words: how a stopped job stopped, else its clock or the one word that matters. */
export function agentRowWords(row: JobRow, now: number, t: Words): string {
  if (row.status === 'stopped') return row.stoppedBy ?? t('ui.status.stopped');
  return rowMeta(row, now, t);
}

export interface BackgroundAgentsListProps {
  jobs: ReadonlyArray<JobView>;
  asks: ReadonlyMap<string, string | null>;
  session: SessionRoute;
  owner: boolean;
  isMobile: boolean;
  onOpenJob: (runId: string) => void;
  onOpenRequest: (requestRunId: string) => void;
}

/**
 * This chat's background agents (design `jobs-panel.tsx`, shot 04): a section for where each one stands (Needs you,
 * Working, Up next, Finished; Finished folded while anything still goes), the jobs one request handed out together
 * on one card under what was asked, each agent a row that opens it, with "▸ N tasks" under it: the steps it took,
 * ticked off as it goes. Only this chat's jobs: every job across chats stays on the Jobs page.
 */
export function BackgroundAgentsList({ jobs, asks, session, owner, isMobile, onOpenJob, onOpenRequest }: BackgroundAgentsListProps) {
  const { t } = useTranslation();
  const words = useMemo<Words>(() => (key, vars) => t(key, vars) as string, [t]);
  const cleared = useJobsStore((s) => s.cleared);
  const sections = useMemo(
    () => chatJobSections(jobs, asks, { sessionId: session.sessionId, ...(session.agentId ? { agentKey: session.agentId } : {}) }, words, new Set(cleared)),
    [jobs, asks, session.sessionId, session.agentId, words, cleared],
  );
  const byId = useMemo(() => new Map(jobs.map((j) => [j.runId, j])), [jobs]);
  const now = useNow(jobs.some((j) => isLive(j.status)));
  const [finishedOpen, setFinishedOpen] = useState<boolean | null>(null);
  const showFinished = finishedOpen ?? !finishedFolds(sections);

  if (sections.length === 0) {
    return (
      <Flex direction="column" gap="1" align="center" justify="center" data-testid="background-agents-empty" style={{ flex: 1, textAlign: 'center', padding: 'var(--space-6) var(--space-4)' }}>
        <MaterialIcon name="deployed_code" size={20} color="var(--gray-11)" />
        <Text size="2" style={{ color: 'var(--gray-11)' }}>
          {t('backgroundAgents.empty')}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-11)' }}>
          {t('backgroundAgents.emptyBody')}
        </Text>
      </Flex>
    );
  }

  return (
    <Flex direction="column" gap="5" data-testid="background-agents-list">
      {sections.map((section) => {
        const folds = section.id === 'finished';
        const open = !folds || showFinished;
        const labelId = `background-agents-section-${section.id}`;
        return (
          <Flex key={section.id} direction="column" gap="3" role="group" aria-labelledby={labelId} data-testid={`background-agents-section-${section.id}`}>
            {folds ? (
              <Button
                id={labelId}
                size="1"
                variant="ghost"
                color="gray"
                aria-expanded={open}
                onClick={() => setFinishedOpen(!open)}
                style={{ alignSelf: 'flex-start', margin: 0, gap: 'var(--space-1)', ...touchTarget(isMobile) }}
              >
                <MaterialIcon name={open ? 'arrow_drop_down' : 'arrow_right'} size={16} color="var(--gray-11)" />
                <GroupLabel count={section.count}>{section.label}</GroupLabel>
              </Button>
            ) : (
              <Box style={{ paddingInline: 'var(--space-2)' }}>
                <GroupLabel as="h3" id={labelId} count={section.count}>
                  {section.label}
                </GroupLabel>
              </Box>
            )}
            {open &&
              section.groups.map((group) => (
                <Box
                  key={group.key}
                  data-testid="background-agents-card"
                  style={{ background: 'var(--gray-a2)', border: '1px solid var(--gray-a3)', borderRadius: 'var(--radius-3)', padding: 'var(--space-1)' }}
                >
                  {group.title ? (
                    <RequestTitle title={group.title} count={group.rows.length} isMobile={isMobile} onOpen={() => onOpenRequest(group.key)} />
                  ) : null}
                  {group.rows.map((row) => {
                    const job = byId.get(row.runId);
                    if (!job) return null;
                    return (
                      <AgentRow
                        key={`${row.runId}-${isLive(row.status) ? 'live' : 'ended'}`}
                        row={row}
                        job={job}
                        now={now}
                        session={session}
                        canStop={owner}
                        nested={Boolean(group.title)}
                        isMobile={isMobile}
                        onOpen={() => onOpenJob(row.runId)}
                      />
                    );
                  })}
                </Box>
              ))}
          </Flex>
        );
      })}
    </Flex>
  );
}

/** A request that handed out several jobs: what was asked, how many, and the way into its own view. */
function RequestTitle({ title, count, isMobile, onOpen }: { title: string; count: number; isMobile: boolean; onOpen: () => void }) {
  const { t } = useTranslation();
  return (
    <Button
      variant="ghost"
      color="gray"
      size="2"
      data-testid="background-agents-request"
      onClick={onOpen}
      title={title}
      style={{ width: '100%', margin: 0, justifyContent: 'flex-start', gap: 'var(--space-2)', minHeight: isMobile ? 44 : 36, boxSizing: 'border-box', paddingInline: 'var(--space-2)' }}
    >
      <MaterialIcon name="forum" size={16} color="var(--gray-11)" />
      <Text size="2" style={{ ...ONE_LINE, color: 'var(--gray-12)', flex: 1, textAlign: 'left' }}>
        {title}
      </Text>
      <Text size="1" style={{ color: 'var(--gray-11)', flexShrink: 0 }}>
        {t('backgroundAgents.jobCount', { count })}
      </Text>
      <MaterialIcon name="chevron_right" size={16} color="var(--gray-11)" />
    </Button>
  );
}

/** One background agent: its tile, its name (it opens), where it stands, Stop while it runs, then its tasks. */
export function AgentRow({
  row,
  job,
  now,
  session,
  canStop,
  nested,
  isMobile,
  onOpen,
  showTasks = true,
}: {
  row: JobRow;
  job?: JobView;
  now: number;
  session: SessionRoute;
  canStop: boolean;
  nested: boolean;
  isMobile: boolean;
  onOpen: () => void;
  showTasks?: boolean;
}) {
  const { t } = useTranslation();
  const live = isLive(row.status);
  const stop = useJobsStore((s) => s.stops[row.runId]);
  const stopping = stop === 'sending' || stop === 'sent';
  const tasks = useMemo(() => (job && showTasks ? tasksOf(job) : []), [job, showTasks]);
  const words = (key: string, vars?: Record<string, unknown>) => t(key, vars) as string;

  const onStop = async () => {
    const outcome = await stopJob(session, row.runId);
    if (outcome === 'gone') toast.info(t('backgroundAgents.stopGone'));
    else if (outcome === 'failed') toast.error(t('backgroundAgents.stopFailed'));
  };

  return (
    <Flex direction="column" data-testid="background-agents-row" data-status={row.status} data-run-id={row.runId}>
      <Flex align="center" gap="2" style={{ paddingLeft: nested ? 'var(--space-4)' : 0, paddingRight: 'var(--space-1)' }}>
        <Button
          variant="ghost"
          color="gray"
          size="2"
          onClick={onOpen}
          title={row.title}
          style={{ flex: 1, minWidth: 0, margin: 0, justifyContent: 'flex-start', gap: 'var(--space-2)', minHeight: 44, boxSizing: 'border-box', paddingInline: 'var(--space-2)', color: 'var(--gray-12)' }}
        >
          <AgentTile agent={row.agent} size={28} status={row.status} />
          <Text size="2" style={{ ...ONE_LINE, textAlign: 'left' }}>
            {row.title}
          </Text>
          {/* The way in; on a phone the row's width goes to the name instead. */}
          {isMobile ? null : <MaterialIcon name="chevron_right" size={16} color="var(--gray-11)" />}
        </Button>
        <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
          <StatusGlyph status={row.status} />
          <Text size="1" data-testid="background-agents-meta" style={{ color: row.status === 'couldntFinish' ? 'var(--red-11)' : 'var(--gray-11)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
            {agentRowWords(row, now, words)}
          </Text>
        </Flex>
        {live && canStop ? (
          <Tooltip content={t('backgroundAgents.stop', { name: row.title })}>
            <IconButton
              size="1"
              variant="ghost"
              color="gray"
              aria-label={t('backgroundAgents.stop', { name: row.title })}
              disabled={stopping}
              onClick={() => void onStop()}
              style={{ margin: 0, flexShrink: 0, ...(isMobile ? touchTarget(true) : { width: 28, height: 28 }) }}
            >
              {stopping ? <Spinner size="1" /> : <MaterialIcon name="stop" size={16} variant="filled" color="var(--gray-12)" />}
            </IconButton>
          </Tooltip>
        ) : null}
      </Flex>
      {tasks.length > 0 ? <TaskFold tasks={tasks} initiallyOpen={live} inset={nested ? 52 : 36} isMobile={isMobile} /> : null}
    </Flex>
  );
}

/** "▸ N tasks": an agent's own steps, open while it works and folded once it ended; yours to flip either way. */
function TaskFold({ tasks, initiallyOpen, inset, isMobile }: { tasks: ReadonlyArray<TaskLine>; initiallyOpen: boolean; inset: number; isMobile: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(initiallyOpen);
  const [all, setAll] = useState(false);
  const hidden = all ? 0 : Math.max(0, tasks.length - TASKS_SHOWN);
  const shown = hidden ? tasks.slice(hidden) : tasks;
  return (
    <Flex direction="column" style={{ paddingLeft: inset, paddingRight: 'var(--space-2)', paddingBottom: open ? 'var(--space-1)' : 0 }}>
      <Button
        size="1"
        variant="ghost"
        color="gray"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        data-testid="background-agents-tasks"
        style={{ alignSelf: 'flex-start', margin: 0, gap: 'var(--space-1)', color: 'var(--gray-11)', ...touchTarget(isMobile) }}
      >
        <MaterialIcon name={open ? 'arrow_drop_down' : 'arrow_right'} size={16} color="var(--gray-11)" />
        {t('backgroundAgents.tasks', { count: tasks.length })}
      </Button>
      {open ? (
        <Flex direction="column" role="list" style={{ paddingLeft: 'var(--space-1)' }}>
          {hidden > 0 ? (
            <Button size="1" variant="ghost" color="gray" onClick={() => setAll(true)} style={{ alignSelf: 'flex-start', margin: 0, ...touchTarget(isMobile) }}>
              {t('backgroundAgents.showEarlier', { count: hidden })}
            </Button>
          ) : null}
          {shown.map((task) => (
            <Flex key={task.id} role="listitem" align="center" gap="2" data-mark={task.mark} style={{ minHeight: 24 }}>
              <Flex align="center" justify="center" style={{ width: 16, flexShrink: 0 }}>
                {task.mark === 'done' ? (
                  <MaterialIcon name="check" size={14} color="var(--gray-11)" />
                ) : task.mark === 'now' ? (
                  <Spinner size="1" />
                ) : (
                  <MaterialIcon name="radio_button_unchecked" size={14} color="var(--gray-11)" />
                )}
              </Flex>
              <Text size="1" style={{ ...ONE_LINE, color: task.mark === 'now' ? 'var(--gray-12)' : 'var(--gray-11)' }}>
                {task.mark === 'now' ? t('backgroundAgents.taskNow', { title: task.title }) : task.title}
              </Text>
            </Flex>
          ))}
        </Flex>
      ) : null}
    </Flex>
  );
}

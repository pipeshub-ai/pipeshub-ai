'use client';

import { useState } from 'react';
import { Button, Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { touchTarget } from '@/app/components/ui/responsive';
import { Link } from '@/lib/navigation';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { toast } from '@/lib/store/toast-store';
import { JobsApi } from '@/lib/harness-jobs/jobs-api';
import { isLive, type JobStatus } from '@/lib/harness-jobs/job-status';
import { rowMeta, type JobRow } from '@/lib/harness-jobs/jobs-list';
import { refreshJobLists } from '@/lib/harness-jobs/swr-keys';
import { chatAtHref } from '../url-params';

const STATUS_MARK: Record<JobStatus, { icon: string; color: string }> = {
  upNext: { icon: 'schedule', color: 'var(--gray-11)' },
  working: { icon: 'progress_activity', color: 'var(--accent-11)' },
  needsYou: { icon: 'pan_tool', color: 'var(--amber-11)' },
  done: { icon: 'check_circle', color: 'var(--accent-11)' },
  couldntFinish: { icon: 'error', color: 'var(--red-11)' },
  stopped: { icon: 'stop_circle', color: 'var(--gray-11)' },
};

/** What a row says on its right: how a stopped job stopped, else the adapter's clock or word. */
export function jobRowWords(row: JobRow, now: number, t: (key: string, vars?: Record<string, unknown>) => string): string {
  if (row.status === 'stopped') return row.stoppedBy ?? t('jobsPage.stoppedByYou');
  return rowMeta(row, now, t);
}

/**
 * One job on the Jobs page: its title, its chat and agent, where it stands, Open (its chat with its panel, D5) and,
 * while it can still be stopped, Stop (one click, D11). The caller's own jobs only, so Stop is theirs to press.
 */
export function JobRowItem({ row, now }: { row: JobRow; now: number }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [stopping, setStopping] = useState(false);
  const mark = STATUS_MARK[row.status];
  const meta = [row.chatTitle, row.agent].filter(Boolean).join(' · ');
  const href = chatAtHref({ conversationId: row.sessionId, agentKey: row.agentKey, jobRunId: row.runId });

  const stop = async () => {
    setStopping(true);
    try {
      const stopped = await JobsApi.stop({ sessionId: row.sessionId, ...(row.agentKey ? { agentId: row.agentKey } : {}) }, row.runId);
      if (!stopped) toast.info(t('jobsPage.stopGone'));
    } catch {
      toast.error(t('jobsPage.stopFailed'));
    } finally {
      setStopping(false);
      refreshJobLists();
    }
  };

  return (
    <Flex
      role="listitem"
      data-testid="job-row"
      data-status={row.status}
      data-run-id={row.runId}
      direction={isMobile ? 'column' : 'row'}
      align={isMobile ? 'stretch' : 'center'}
      gap={isMobile ? '2' : '3'}
      style={{
        minHeight: 44,
        padding: 'var(--space-2) var(--space-3)',
        borderRadius: 'var(--radius-2)',
        border: `1px solid ${row.status === 'needsYou' ? 'var(--amber-6)' : 'var(--olive-4)'}`,
        backgroundColor: 'var(--color-panel-solid)',
        boxSizing: 'border-box',
      }}
    >
      <Flex align="center" gap="3" style={{ flex: 1, minWidth: 0 }}>
        <MaterialIcon name={mark.icon} size={18} color={mark.color} />
        <Flex direction="column" style={{ minWidth: 0, flex: 1 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {row.title}
          </Text>
          {meta && (
            <Text size="1" color="gray" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {meta}
            </Text>
          )}
        </Flex>
        <Text size="1" color="gray" style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
          {jobRowWords(row, now, t)}
        </Text>
      </Flex>
      <Flex align="center" justify={isMobile ? 'between' : 'end'} gap="2" style={{ flexShrink: 0 }}>
        <Button asChild size="1" variant="soft" color="gray" style={{ margin: 0, ...touchTarget(isMobile) }}>
          <Link href={href} aria-label={t('jobsPage.openAria', { title: row.title })}>
            {t('jobsPage.open')}
          </Link>
        </Button>
        {isLive(row.status) &&
          (isMobile ? (
            <IconButton
              size="1"
              variant="soft"
              color="red"
              aria-label={t('jobsPage.stopAria', { title: row.title })}
              disabled={stopping}
              onClick={() => void stop()}
              style={{ margin: 0, ...touchTarget(true) }}
            >
              <MaterialIcon name="stop" size={16} color="var(--red-11)" />
            </IconButton>
          ) : (
            <Button
              size="1"
              variant="soft"
              color="red"
              aria-label={t('jobsPage.stopAria', { title: row.title })}
              disabled={stopping}
              onClick={() => void stop()}
              style={{ margin: 0 }}
            >
              <MaterialIcon name="stop" size={14} color="var(--red-11)" />
              {t('jobsPage.stop')}
            </Button>
          ))}
      </Flex>
    </Flex>
  );
}

'use client';

import React from 'react';
import Link from 'next/link';
import { Button, Flex, IconButton, Spinner, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AgentTile } from '@/app/components/harness-jobs';
import { JOBS_HREF } from '@/app/(main)/jobs/url-params';
import { requestAskFocus } from '../../hooks/use-interaction-focus';
import type { DurableAgentRunHandle } from '../../types';
import { stopJob, useJobsStore } from './job-data';
import { useActivityPanelStore } from './activity-panel-store';
import type { PanelJob } from './use-panel-job';

/** Phone hit areas: 44 px (frontend/CLAUDE.md). */
const TOUCH = { minWidth: 44, minHeight: 44 };

function Icon({ name, size = 16, color, filled }: { name: string; size?: number; color?: string; filled?: boolean }) {
  return (
    <span aria-hidden="true" style={{ display: 'inline-flex' }}>
      <MaterialIcon name={name} size={size} color={color} variant={filled ? 'filled' : 'outlined'} />
    </span>
  );
}

/**
 * Where you are (design `worker-panel.tsx`, §6B.32): "Background agents › job name", the crumb back to every job.
 * The job's name is the panel's accessible name, so it carries the heading id.
 */
function Breadcrumb({ title, headingId }: { title: string; headingId: string }) {
  const { t } = useTranslation();
  return (
    <nav aria-label={t('jobPanel.breadcrumb')} style={{ flex: 1, minWidth: 0 }}>
      <Flex align="center" gap="1" style={{ minWidth: 0 }}>
        <Link
          href={JOBS_HREF.all}
          data-testid="job-panel-crumb-jobs"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            flexShrink: 0,
            minHeight: 28,
            color: 'var(--gray-11)',
            fontSize: 'var(--font-size-2)',
            textDecoration: 'none',
            whiteSpace: 'nowrap',
          }}
        >
          {t('jobPanel.backgroundAgents')}
        </Link>
        <Icon name="chevron_right" size={14} color="var(--gray-11)" />
        <Text
          id={headingId}
          size="2"
          aria-current="page"
          data-testid="job-panel-title"
          title={title}
          style={{ flex: 1, minWidth: 0, color: 'var(--gray-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {title}
        </Text>
      </Flex>
    </nav>
  );
}

/**
 * A job panel's header (D4, design `worker-panel.tsx`): the breadcrumb as its top bar with widen and close, then the
 * agent's tile with the status on its corner, the agent over the status in words, and Stop (D11: one click, no
 * confirm, only while it runs and only for its owner). A 409 means it had already ended.
 */
export function JobHeader({
  run,
  job,
  owner,
  variant,
  headingId,
  onClose,
}: {
  run: DurableAgentRunHandle;
  job: PanelJob;
  owner: boolean;
  variant: 'pane' | 'sheet';
  headingId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  // The Stop's outcome is the job's (ADR-CJ-01), the same here as on its card.
  const sent = useJobsStore((s) => s.stops[run.runId]);
  const stop = { kind: sent === 'sending' || sent === 'sent' ? 'stopping' : sent === 'gone' || sent === 'failed' ? sent : 'idle' } as const;
  const wide = useActivityPanelStore((s) => s.wide);
  const setWide = useActivityPanelStore((s) => s.setWide);
  const phone = variant === 'sheet';
  const stopLabel = t('jobPanel.stopAria', { title: job.title });

  return (
    <Flex direction="column" data-testid="job-panel-header" data-status={job.status}>
      {phone ? (
        // The sheet's own bar names the panel and closes it (design: the compact drawer brings its header), so the
        // job's name leads the body instead of a second bar.
        <Text
          id={headingId}
          as="div"
          size="3"
          weight="medium"
          data-testid="job-panel-title"
          style={{ overflowWrap: 'anywhere', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
        >
          {job.title}
        </Text>
      ) : (
        <Flex align="center" gap="2" style={{ minHeight: 40, padding: '0 var(--space-3) 0 var(--space-4)', borderBottom: '1px solid var(--olive-3)' }}>
          <Breadcrumb title={job.title} headingId={headingId} />
          <Tooltip content={wide ? t('jobPanel.narrow') : t('jobPanel.widen')}>
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              aria-label={wide ? t('jobPanel.narrow') : t('jobPanel.widen')}
              aria-pressed={wide}
              data-testid="job-panel-widen"
              onClick={() => setWide(!wide)}
            >
              <Icon name={wide ? 'close_fullscreen' : 'open_in_full'} size={16} color="var(--gray-11)" />
            </IconButton>
          </Tooltip>
          <IconButton variant="ghost" color="gray" size="1" onClick={onClose} aria-label={t('jobPanel.close')}>
            <Icon name="close" size={18} color="var(--gray-11)" />
          </IconButton>
        </Flex>
      )}
      <Flex align="center" gap="3" style={{ padding: phone ? 'var(--space-3) 0 var(--space-2)' : 'var(--space-3) var(--space-3) var(--space-2) var(--space-4)' }}>
        <AgentTile agent={job.agent} size={36} status={job.status} />
        <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
          <Text size="2" weight="medium" data-testid="job-panel-agent">
            {job.agent}
          </Text>
          <Text
            size="1"
            color={job.status === 'couldntFinish' ? 'red' : 'gray'}
            data-testid="job-panel-status"
            style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {job.line}
          </Text>
        </Flex>
        {owner && job.live && stop.kind !== 'gone' ? (
          <Tooltip content={stopLabel}>
            <IconButton
              variant="ghost"
              color="gray"
              size={phone ? '3' : '2'}
              disabled={stop.kind === 'stopping'}
              onClick={() => void stopJob(run, run.runId)}
              aria-label={stopLabel}
              data-testid="job-panel-stop"
              style={phone ? TOUCH : undefined}
            >
              {stop.kind === 'stopping' ? <Spinner size="1" /> : <Icon name="stop" size={18} color="var(--gray-12)" filled />}
            </IconButton>
          </Tooltip>
        ) : null}
      </Flex>
      {stop.kind === 'gone' ? (
        <Text size="1" color="gray" role="status" data-testid="job-panel-stop-gone" style={{ padding: phone ? 0 : '0 var(--space-4)' }}>
          {t('jobPanel.stopGone')}
        </Text>
      ) : null}
      {stop.kind === 'failed' ? (
        <Text size="1" color="red" role="alert" data-testid="job-panel-stop-failed" style={{ padding: phone ? 0 : '0 var(--space-4)' }}>
          {t('jobPanel.stopFailed')}
        </Text>
      ) : null}
    </Flex>
  );
}

/**
 * The asks a job waits on (design: "Answer it in the card over the chat"): the ask lives only in its card in the
 * chat (D3), so Activity says it waits and hands focus to that card.
 */
export function JobAskNotice({ job, phone, onClose }: { job: PanelJob; phone: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  if (!job.asks.length) return null;
  return (
    <Flex
      align="center"
      gap="2"
      wrap="wrap"
      data-testid="job-panel-ask"
      style={{ padding: 'var(--space-2) var(--space-3)', borderRadius: 'var(--radius-2)', border: '1px solid var(--amber-a7)', backgroundColor: 'var(--amber-a2)' }}
    >
      <Icon name="pan_tool" size={16} color="var(--amber-11)" />
      <Text size="2" style={{ flex: 1, minWidth: 0 }}>
        {t('jobPanel.askWaiting', { count: job.asks.length })}
      </Text>
      <Button
        size="1"
        variant="soft"
        color="amber"
        // The soft amber text on the amber row reads under 4.5:1 in the light look (F5).
        highContrast
        style={phone ? TOUCH : undefined}
        onClick={() => {
          // On the phone the sheet covers the chat; the card the ask is answered on is behind it (D3).
          if (phone) onClose();
          requestAskFocus(job.asks[0]?.interactionId ?? null);
        }}
      >
        {t('jobPanel.answerInChat')}
      </Button>
    </Flex>
  );
}

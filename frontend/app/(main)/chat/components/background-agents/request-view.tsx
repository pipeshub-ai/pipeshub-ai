'use client';

import { useEffect, useMemo, useState } from 'react';
import { Box, Flex, Spinner, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { GroupLabel } from '@/app/components/harness-jobs';
import { isLive } from '@/lib/harness-jobs/job-status';
import { requestView, type RequestView } from '@/lib/harness-jobs/jobs-list';
import type { JobView } from '@/lib/harness-jobs/jobs';
import { JobsApi, type SessionRoute } from '@/lib/harness-jobs/jobs-api';
import type { Words } from '@/lib/harness-jobs/words';
import { AgentRow } from './background-agents-list';
import { useNow } from '../message-area/job-card/use-job-stream';

/**
 * A request that handed out several jobs, opened from its card in the list (design §6B.42, `GET …/runs/{id}/jobs`):
 * what you asked, "Ran N jobs" with each job a row that opens it (a replacement under the job it replaced), and
 * "N of M back" while they run. Read on open and again whenever one of its jobs moves on in this chat.
 */
export function RequestJobsView({
  session,
  requestRunId,
  jobs,
  owner,
  isMobile,
  onOpenJob,
}: {
  session: SessionRoute;
  requestRunId: string;
  jobs: ReadonlyArray<JobView>;
  owner: boolean;
  isMobile: boolean;
  onOpenJob: (runId: string) => void;
}) {
  const { t } = useTranslation();
  const words = useMemo<Words>(() => (key, vars) => t(key, vars) as string, [t]);
  const [view, setView] = useState<RequestView | 'loading' | 'missing'>('loading');
  const mine = jobs.filter((j) => j.parentRunId === requestRunId);
  // The chat's own live words for its jobs move the read on: the request's count is the server's.
  const refreshKey = mine.map((j) => `${j.runId}:${j.status}`).join();
  const byId = new Map(mine.map((j) => [j.runId, j]));
  const { agentId, sessionId } = session;

  useEffect(() => {
    let current = true;
    JobsApi.requestJobs({ ...(agentId ? { agentId } : {}), sessionId }, requestRunId)
      .then((req) => current && setView(requestView(req, words, agentId)))
      .catch(() => current && setView((v) => (typeof v === 'object' ? v : 'missing')));
    return () => {
      current = false;
    };
  }, [agentId, sessionId, requestRunId, refreshKey, words]);

  const rows = typeof view === 'object' ? view.rows : [];
  // Where the chat streams a job, its status is the live one; the read stands for the rest.
  const live = rows.map((r) => {
    const j = byId.get(r.runId);
    return j ? { ...r, status: j.status, ...(j.stoppedBy ? { stoppedBy: j.stoppedBy } : {}) } : r;
  });
  const now = useNow(live.some((r) => isLive(r.status)));

  if (view === 'loading') {
    return (
      <Flex align="center" gap="2" style={{ color: 'var(--gray-11)', padding: 'var(--space-2)' }}>
        <Spinner size="1" />
        <Text size="1">{t('backgroundAgents.requestLoading')}</Text>
      </Flex>
    );
  }
  if (view === 'missing') {
    return (
      <Text as="p" size="2" style={{ color: 'var(--gray-11)', padding: 'var(--space-2)', margin: 0 }}>
        {t('backgroundAgents.requestUnavailable')}
      </Text>
    );
  }
  return (
    <Flex direction="column" gap="4" data-testid="background-agents-request-view">
      <Flex direction="column" gap="2">
        <Box style={{ paddingInline: 'var(--space-2)' }}>
          <GroupLabel as="h3">{t('backgroundAgents.requestAsked')}</GroupLabel>
        </Box>
        <Box style={{ background: 'var(--gray-a2)', border: '1px solid var(--gray-a3)', borderRadius: 'var(--radius-3)', padding: 'var(--space-3)' }}>
          <Text as="p" size="2" style={{ margin: 0, color: view.ask ? 'var(--gray-12)' : 'var(--gray-11)', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>
            {view.ask ?? t('backgroundAgents.requestErased')}
          </Text>
        </Box>
      </Flex>
      <Flex direction="column" gap="2">
        <Flex align="center" justify="between" gap="2" style={{ paddingInline: 'var(--space-2)' }}>
          <Text size="1" style={{ color: 'var(--gray-11)' }}>
            {view.ran}
          </Text>
          <Text size="1" aria-live="polite" data-testid="background-agents-request-progress" style={{ color: 'var(--gray-11)', fontVariantNumeric: 'tabular-nums' }}>
            {view.progress}
          </Text>
        </Flex>
        <Box style={{ background: 'var(--gray-a2)', border: '1px solid var(--gray-a3)', borderRadius: 'var(--radius-3)', padding: 'var(--space-1)' }}>
          {live.map((row) => (
            <Box key={row.runId} style={{ paddingLeft: row.depth * 16 }}>
              <AgentRow
                row={row}
                job={byId.get(row.runId)}
                now={now}
                session={session}
                canStop={owner}
                nested={false}
                isMobile={isMobile}
                showTasks={false}
                onOpen={() => onOpenJob(row.runId)}
              />
            </Box>
          ))}
        </Box>
      </Flex>
    </Flex>
  );
}

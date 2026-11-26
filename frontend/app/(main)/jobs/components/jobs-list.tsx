'use client';

import { useId, useMemo } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { VirtualRows } from '@/chat/components/virtual-rows/virtual-rows';
import { toast } from '@/lib/store/toast-store';
import { touchTarget } from '@/app/components/ui/responsive';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useJobsStore } from '@/lib/harness-jobs/jobs-store';
import { isLive } from '@/lib/harness-jobs/job-status';
import { jobRowOfPersonRun, jobsSections, type JobRow, type JobsSection } from '@/lib/harness-jobs/jobs-list';
import { usePersonJobs } from '../use-person-jobs';
import { useNow } from '../use-now';
import { JobRowItem } from './job-row';
import { JobsMessage } from './jobs-message';

/** A section longer than this renders only the rows near the viewport (§6). */
export const VIRTUAL_ROWS_AFTER = 50;
const ROW_ESTIMATE_PX = 58;

function Section({ section, now, action }: { section: JobsSection; now: number; action?: React.ReactNode }) {
  const headingId = useId();
  const rows = section.groups.flatMap((g) => g.rows);
  return (
    <section aria-labelledby={headingId} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <Flex align="center" justify="between" gap="2" style={{ minHeight: 28 }}>
        <h2 id={headingId} style={{ margin: 0, fontSize: 'var(--font-size-2)', fontWeight: 'var(--font-weight-medium)', color: 'var(--slate-12)' }}>
          {section.label}
        </h2>
        {action}
      </Flex>
      <div role="list">
        <VirtualRows<JobRow>
          items={rows}
          getKey={(row) => row.runId}
          estimateSize={() => ROW_ESTIMATE_PX}
          gap="var(--space-2)"
          threshold={VIRTUAL_ROWS_AFTER}
          renderRow={(row) => <JobRowItem row={row} now={now} />}
        />
      </div>
    </section>
  );
}

/**
 * The Jobs tab: the caller's jobs across their chats in the plan's sections (Needs you, Working, Up next,
 * Finished). Clear finished hides finished rows on this device only, with Undo; their cards stay in the chats.
 */
export function JobsList() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const jobs = usePersonJobs();
  const cleared = useJobsStore((s) => s.cleared);
  const rows = useMemo(() => jobs.runs.map((run) => jobRowOfPersonRun(run, t, { whole: true })), [jobs.runs, t]);
  const sections = useMemo(() => jobsSections(rows, t, new Set(cleared)), [rows, t, cleared]);
  const now = useNow(rows.some((r) => r.status === 'working'), 1000);

  if (jobs.loading) return <JobsMessage text={t('jobsPage.loading')} busy />;
  if (jobs.failed) return <JobsMessage text={t('jobsPage.loadFailed')} action={{ label: t('jobsPage.retry'), onClick: jobs.retry }} />;
  if (rows.length === 0) return <JobsMessage text={t('jobsPage.empty')} />;

  const finished = rows.filter((r) => !isLive(r.status) && !cleared.includes(r.runId)).map((r) => r.runId);
  const clearFinished = () => {
    useJobsStore.getState().clear(finished);
    toast.success(t('jobsPage.cleared', { count: finished.length }), {
      action: { label: t('jobsPage.undo'), onClick: () => useJobsStore.getState().unclear(finished) },
    });
  };
  const nothingRunning = !rows.some((r) => isLive(r.status));

  return (
    <Flex direction="column" gap="5">
      {nothingRunning && (
        <Text size="2" color="gray">
          {t('jobsPage.nothingRunning')}
        </Text>
      )}
      {sections.map((section) => (
        <Section
          key={section.id}
          section={section}
          now={now}
          action={
            section.id === 'finished' && finished.length > 0 ? (
              <Button size="1" variant="ghost" color="gray" onClick={clearFinished} style={{ margin: 0, ...touchTarget(isMobile) }}>
                {t('jobsPage.clearFinished')}
              </Button>
            ) : undefined
          }
        />
      ))}
      {sections.length === 0 && <JobsMessage text={t('jobsPage.allCleared')} />}
      {jobs.hasMore && (
        <Flex justify="center">
          <Button size="2" variant="soft" color="gray" onClick={jobs.loadMore} loading={jobs.loadingMore}>
            {t('jobsPage.loadMore')}
          </Button>
        </Flex>
      )}
    </Flex>
  );
}

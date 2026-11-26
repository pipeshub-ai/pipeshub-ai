'use client';

import { useEffect, useId, useMemo } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { InboxItem } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-item';
import { closeInbox, openNeedsYou, reloadInbox, useInboxStore } from '@/lib/harness-jobs/inbox-store';
import { jobRowOfPersonRun } from '@/lib/harness-jobs/jobs-list';
import { JOBS_HREF } from '../url-params';
import { usePersonJobs } from '../use-person-jobs';
import { useNow } from '../use-now';
import { NeedsYouRow, type AskJob } from './needs-you-row';
import { JobsMessage } from './jobs-message';

/** Asks decided in a chat (a job's or the chat's own); every other waiting kind is a review. */
const FROM_CHATS = new Set<InboxItem['kind']>(['approval', 'question']);

/** Approvals and questions first, then reviews (D7), each newest first as the feed answered. */
export function needsYouGroups(items: ReadonlyArray<InboxItem>): { fromChats: InboxItem[]; reviews: InboxItem[] } {
  const waiting = items.filter((i) => i.waiting);
  return { fromChats: waiting.filter((i) => FROM_CHATS.has(i.kind)), reviews: waiting.filter((i) => !FROM_CHATS.has(i.kind)) };
}

function Group({ title, items, jobs, now }: { title: string; items: InboxItem[]; jobs: Map<string, AskJob>; now: number }) {
  const headingId = useId();
  if (items.length === 0) return null;
  return (
    <section aria-labelledby={headingId} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <h2 id={headingId} style={{ margin: 0, fontSize: 'var(--font-size-2)', fontWeight: 'var(--font-weight-medium)', color: 'var(--slate-12)' }}>
        {title}
      </h2>
      <Flex role="list" direction="column" gap="2">
        {items.map((item) => (
          <NeedsYouRow key={item.id} item={item} job={'runId' in item.pointer && item.pointer.runId ? jobs.get(item.pointer.runId) ?? null : null} now={now} />
        ))}
      </Flex>
    </section>
  );
}

/**
 * The Needs you tab: every ask the server counts (D7), read with `filter=waiting` when the tab opens and again on
 * each push that changes the count while it is open. Nothing is removed before the server says it is settled.
 */
export function NeedsYouList() {
  const { t } = useTranslation();
  const items = useInboxStore((s) => s.items);
  const loading = useInboxStore((s) => s.loading);
  const failed = useInboxStore((s) => s.failed);
  const partial = useInboxStore((s) => s.partial);
  const jobs = usePersonJobs();
  const now = useNow(true, 60_000);

  useEffect(() => {
    openNeedsYou();
    return () => closeInbox();
  }, []);

  const jobOf = useMemo(() => {
    const map = new Map<string, AskJob>();
    for (const run of jobs.runs) {
      if (!run.job) continue;
      const row = jobRowOfPersonRun(run, t, { whole: true });
      map.set(run.runId, { title: row.title, ...(row.chatTitle ? { chatTitle: row.chatTitle } : {}) });
    }
    return map;
  }, [jobs.runs, t]);

  const { fromChats, reviews } = needsYouGroups(items);

  if (loading && items.length === 0) return <JobsMessage text={t('needsYou.loading')} busy />;
  if (failed && items.length === 0) return <JobsMessage text={t('needsYou.loadFailed')} action={{ label: t('jobsPage.retry'), onClick: () => void reloadInbox() }} />;
  if (fromChats.length + reviews.length === 0)
    return <JobsMessage text={t('needsYou.empty')} link={{ label: t('needsYou.seeJobs'), href: JOBS_HREF.all }} />;

  return (
    <Flex direction="column" gap="5">
      {partial.length > 0 && (
        <Text size="1" color="gray" role="note">
          {t('needsYou.partial')}
        </Text>
      )}
      <Group title={t('needsYou.fromChats')} items={fromChats} jobs={jobOf} now={now} />
      <Group title={t('needsYou.reviews')} items={reviews} jobs={jobOf} now={now} />
    </Flex>
  );
}

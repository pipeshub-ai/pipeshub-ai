'use client';

import { Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { selectAgentHarnessOn, selectFeatureFlagsLoaded, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useNeedsYouCount } from '@/lib/harness-jobs/inbox-store';
import { JobsTabs } from './components';
import { JobsMessage } from './components/jobs-message';
import { jobsViewHref, jobsViewOf } from './url-params';

function JobsPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const params = useSearchParams();
  const waiting = useNeedsYouCount();
  const harness = useFeatureFlagsStore(selectAgentHarnessOn);
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const view = jobsViewOf(params, waiting);

  return (
    <div data-testid="jobs-page" style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      {/* Nothing of the jobs is read before the flags say the harness is on (ADR-OPS-06). */}
      {!flagsLoaded ? null : !harness ? (
        <JobsMessage text={t('jobsPage.harnessOff')} link={{ label: t('jobsPage.backToChat'), href: '/chat/' }} />
      ) : (
        <JobsTabs view={view} waiting={waiting} onViewChange={(next) => router.replace(jobsViewHref(next))} />
      )}
    </div>
  );
}

/** `/jobs/`: Needs you and the caller's jobs across their chats (classic jobs D6). */
export default function JobsPage() {
  return (
    <Suspense>
      <JobsPageContent />
    </Suspense>
  );
}

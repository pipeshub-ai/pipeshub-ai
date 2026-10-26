'use client';

import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useFeatureFlagGuard } from '@/lib/hooks/use-feature-flag-guard';
import { selectSoftDeleteEnabled } from '@/lib/store/feature-flags-store';
import { RecentlyDeletedView } from './components/recently-deleted-view';

const COLLECTIONS_URL = '/knowledge-base';

function RecentlyDeletedContent() {
  const router = useRouter();
  const kbId = useSearchParams().get('kbId');
  // Without the trash there is nothing to list, so the guard sends the visitor back to Collections.
  const { ready } = useFeatureFlagGuard(selectSoftDeleteEnabled, COLLECTIONS_URL);

  useEffect(() => {
    if (ready && !kbId) router.replace(COLLECTIONS_URL);
  }, [ready, kbId, router]);

  if (!ready || !kbId) return null;
  return <RecentlyDeletedView key={kbId} kbId={kbId} />;
}

export default function RecentlyDeletedPage() {
  return (
    <ServiceGate services={['connector']}>
      <Suspense>
        <RecentlyDeletedContent />
      </Suspense>
    </ServiceGate>
  );
}

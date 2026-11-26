import { Suspense } from 'react';

import { LoadingScreen } from '@/app/components/ui/auth-guard';

import { SlackLinkView } from './slack-link-view';

export default function SlackLinkPage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <SlackLinkView />
    </Suspense>
  );
}

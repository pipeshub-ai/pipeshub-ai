import { Suspense } from 'react';

import { LoadingScreen } from '@/app/components/ui/auth-guard';

import { AgentClaimView } from './agent-claim-view';

export default function AgentClaimPage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <AgentClaimView />
    </Suspense>
  );
}

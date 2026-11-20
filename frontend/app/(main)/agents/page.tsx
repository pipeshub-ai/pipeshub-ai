'use client';

import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { AgentsIndex } from './components/index/agents-index';

/** Old `/agents?agentId=…` links open that agent's chat, keeping every param; the bare URL is the Agents index. */
function AgentsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = searchParams.toString();
  const redirect = searchParams.has('agentId');

  useEffect(() => {
    if (redirect) router.replace(`/chat/?${query}`);
  }, [redirect, router, query]);

  if (redirect) return null;
  return (
    <ServiceGate services={['query']}>
      <AgentsIndex />
    </ServiceGate>
  );
}

export default function AgentsPage() {
  return (
    <Suspense fallback={null}>
      <AgentsPageContent />
    </Suspense>
  );
}

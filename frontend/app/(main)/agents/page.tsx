'use client';

import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

/** Agents live in chat ("My agents"); old `/agents` links land there, keeping `?agentId=`. */
function AgentsRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = searchParams.toString();

  useEffect(() => {
    router.replace(query ? `/chat/?${query}` : '/chat/');
  }, [router, query]);

  return null;
}

export default function AgentsPage() {
  return (
    <Suspense fallback={null}>
      <AgentsRedirect />
    </Suspense>
  );
}

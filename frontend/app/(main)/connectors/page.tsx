'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

// Connectors live under Workspace; this path is kept for the OAuth callback helpers beside it and old links.
export default function ConnectorsPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace('/workspace/connectors');
  }, [router]);

  return null;
}

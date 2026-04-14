'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

/**
 * /workspace/connectors — redirects to the Team tab by default.
 */
export default function ConnectorsPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace('/workspace/connectors/team');
  }, [router]);

  return null;
}

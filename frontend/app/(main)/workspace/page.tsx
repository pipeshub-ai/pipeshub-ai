'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

/**
 * /workspace root — redirects to /workspace/general
 */
export default function WorkspacePage() {
  const router = useRouter();

  useEffect(() => {
    router.replace('/workspace/general');
  }, [router]);

  return null;
}

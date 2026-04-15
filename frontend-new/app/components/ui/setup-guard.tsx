'use client';

import { useEffect, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { isTauri, hasStoredApiBaseUrl } from '@/lib/api/base-url';

/**
 * For public (unauthenticated) routes in the Tauri desktop app:
 * always redirect to /setup so the user confirms the server URL
 * before reaching login. The URL is pre-filled from the previous
 * session, so returning users just click "Connect".
 *
 * In web deployments this guard is a no-op.
 */
export function PublicSetupGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (pathname === '/setup' || pathname === '/setup/') {
      setChecked(true);
      return;
    }

    if (isTauri()) {
      router.replace('/setup');
      return;
    }

    setChecked(true);
  }, [pathname, router]);

  if (!checked) return null;

  return <>{children}</>;
}

/**
 * For authenticated (main) routes in the Tauri desktop app:
 * only redirect to /setup if no server URL has been configured.
 * Authenticated users are not interrupted.
 *
 * In web deployments this guard is a no-op.
 */
export function AuthenticatedSetupGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (pathname === '/setup' || pathname === '/setup/') {
      setChecked(true);
      return;
    }

    if (isTauri() && !hasStoredApiBaseUrl()) {
      router.replace('/setup');
      return;
    }

    setChecked(true);
  }, [pathname, router]);

  if (!checked) return null;

  return <>{children}</>;
}

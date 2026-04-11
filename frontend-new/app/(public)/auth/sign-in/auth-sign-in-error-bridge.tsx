'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect } from 'react';

/**
 * Backend SAML error callback (e.g. /auth/sign-in/?error=jit_Disabled).
 * Forwards to /login so the client can show a toast and strip sensitive query params.
 * Client-only so `output: 'export'` can prerender a static shell.
 */
export function AuthSignInErrorBridge() {
  const searchParams = useSearchParams();
  const router = useRouter();

  useEffect(() => {
    const raw = searchParams.get('error');
    const code =
      typeof raw === 'string' && raw.trim().length > 0 ? raw.trim() : 'unknown';
    router.replace(`/login?saml_error=${encodeURIComponent(code)}`);
  }, [searchParams, router]);

  return null;
}

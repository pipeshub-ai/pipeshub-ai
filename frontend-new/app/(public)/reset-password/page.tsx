'use client';

import React, { useEffect, useState, Suspense } from 'react';
import { useRouter } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useAuthWideLayout } from '@/lib/hooks/use-breakpoint';
import AuthHero from '../components/auth-hero';
import FormPanel from '../components/form-panel';
import PasswordResetSuccess from '../components/password-reset-success';
import PasswordResetFailure from '../components/password-reset-failure';
import ChangePassword from '../forms/change-password';
import { decodeJwtUser } from '@/lib/utils/auth-helpers';
import type { JwtUser } from '@/lib/utils/auth-helpers';
import { useAuthStore } from '@/lib/store/auth-store';

// ─── Inner component ─────────────────────────────────────────────────────────

function ResetPasswordContent() {
  const router = useRouter();
  const splitLayout = useAuthWideLayout();
  const { isAuthenticated, isHydrated } = useAuthStore();

  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<JwtUser>({});
  const [ready, setReady] = useState(false);
  const [success, setSuccess] = useState(false);

  // If the user is already logged in, send them to the app
  useEffect(() => {
    if (isHydrated && isAuthenticated) {
      router.replace('/chat');
    }
  }, [isHydrated, isAuthenticated, router]);

  useEffect(() => {
    // Token is read exclusively from the hash fragment (#token=) so it is
    // never stored in browser history or sent to the server as a query param.
    const hash = window.location.hash;
    const hashMatch = hash.match(/[#&?]?token=([^&]+)/);
    const t = hashMatch ? decodeURIComponent(hashMatch[1]) : null;
    if (t) {
      setToken(t);
      setUser(decodeJwtUser(t));
    }
    setReady(true);
  // window.location.hash is stable on mount; no reactive deps needed.
  }, []);

  // Don't render until the Zustand auth store has rehydrated from localStorage
  if (!isHydrated || !ready) return null;

  // Already authenticated — redirect effect will fire, render nothing
  if (isAuthenticated) return null;

  return (
    <Flex
      direction={splitLayout ? 'row' : 'column'}
      style={{
        minHeight: '100dvh',
        overflow: splitLayout ? 'hidden' : undefined,
      }}
    >
      <AuthHero splitLayout={splitLayout} />

      <FormPanel splitLayout={splitLayout}>
        {success ? (
          <PasswordResetSuccess />
        ) : !token ? (
          <PasswordResetFailure />
        ) : (
          <ChangePassword
            token={token}
            user={user}
            disabled={false}
            onSuccess={() => setSuccess(true)}
          />
        )}
      </FormPanel>
    </Flex>
  );
}

// ─── Page export ──────────────────────────────────────────────────────────────

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordContent />
    </Suspense>
  );
}


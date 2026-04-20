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
import {
  PASSWORD_RESET_FAILURE_VARIANT,
  INVITE_VS_RESET_LIFETIME_CUTOFF_SECONDS,
} from './constants';
import type { PasswordResetFailureVariant } from './types';

// ─── Inner component ─────────────────────────────────────────────────────────

function ResetPasswordContent() {
  const router = useRouter();
  const splitLayout = useAuthWideLayout();
  const { isAuthenticated, isHydrated } = useAuthStore();

  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<JwtUser>({});
  const [ready, setReady] = useState(false);
  const [success, setSuccess] = useState(false);
  // Set when the backend rejects the token because the user no longer exists
  // (cancelled invite or deleted account). JWTs are stateless, so expiry alone
  // can't tell us this — we learn about it on submit and lift it up here.
  const [tokenInvalidated, setTokenInvalidated] = useState(false);

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

  // Decide which screen to show: no token, expired token (invite vs reset),
  // server-invalidated token (cancelled invite / deleted account), or the
  // password form. We gate expiry before the form so users don't have to
  // submit a password just to discover the link is dead.
  const tokenLifetimeSeconds = user.iat && user.exp ? user.exp - user.iat : 0;
  const isInviteToken = tokenLifetimeSeconds > INVITE_VS_RESET_LIFETIME_CUTOFF_SECONDS;

  let failureVariant: PasswordResetFailureVariant | null = null;
  if (!token) {
    failureVariant = PASSWORD_RESET_FAILURE_VARIANT.MISSING_TOKEN;
  } else if (user.exp && user.exp * 1000 < Date.now()) {
    failureVariant = isInviteToken
      ? PASSWORD_RESET_FAILURE_VARIANT.INVITE_EXPIRED
      : PASSWORD_RESET_FAILURE_VARIANT.RESET_EXPIRED;
  } else if (tokenInvalidated) {
    failureVariant = isInviteToken
      ? PASSWORD_RESET_FAILURE_VARIANT.INVITE_EXPIRED
      : PASSWORD_RESET_FAILURE_VARIANT.RESET_EXPIRED;
  }

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
        ) : failureVariant ? (
          <PasswordResetFailure variant={failureVariant} />
        ) : (
          <ChangePassword
            token={token!}
            user={user}
            disabled={false}
            onSuccess={() => setSuccess(true)}
            onInvalidToken={() => setTokenInvalidated(true)}
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


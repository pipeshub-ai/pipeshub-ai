'use client';

import React, { useEffect, useRef, useState, Suspense } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Text } from '@radix-ui/themes';
import { useAuthStore } from '@/lib/store/auth-store';
import { useAuthWideLayout } from '@/lib/hooks/use-breakpoint';
import { getUserAccountApiErrorMessage } from '@/lib/api/user-account-api-error';
import AuthHero from '../components/auth-hero';
import FormPanel from '../components/form-panel';
import { AuthApi } from '../api';

function buildLoginWithVerifyParams(kind: 'success' | 'error', message?: string): string {
  if (kind === 'success') return '/login?email_verify=success';
  const msg = message?.trim() ?? '';
  const q = new URLSearchParams({ email_verify: 'error' });
  if (msg) q.set('email_verify_msg', msg.slice(0, 500));
  return `/login?${q.toString()}`;
}

function ResetEmailContent() {
  const router = useRouter();
  const splitLayout = useAuthWideLayout();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const verifyStartedRef = useRef(false);
  const [token, setToken] = useState<string | null>(null);
  const [urlReady, setUrlReady] = useState(false);

  useEffect(() => {
    // Token is read exclusively from the hash fragment (#token=) so it is
    // never stored in browser history or sent to the server as a query param.
    const hash = window.location.hash;
    const hashMatch = hash.match(/[#&?]?token=([^&]+)/);
    const t = hashMatch ? decodeURIComponent(hashMatch[1]) : null;
    console.log('[reset-email] token extracted from URL hash:', t);
    setToken(t);
    setUrlReady(true);
    // window.location.hash is stable on mount; no reactive deps needed.
  }, []);

  useEffect(() => {
    if (!isHydrated || !urlReady) return;
    if (verifyStartedRef.current) return;
    verifyStartedRef.current = true;

    if (!token) {
      router.replace(buildLoginWithVerifyParams('error', 'Verification link is missing a token.'));
      return;
    }

    AuthApi.validateEmailChange(token)
      .then(() => {
        useAuthStore.getState().logout();
        router.replace(buildLoginWithVerifyParams('success'));
      })
      .catch((err: unknown) => {
        const msg = getUserAccountApiErrorMessage(err);
        router.replace(buildLoginWithVerifyParams('error', msg));
      });
  }, [isHydrated, urlReady, token, router]);

  if (!isHydrated || !urlReady) return null;

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
        <Flex align="center" justify="center" style={{ flex: 1 }}>
          <Text size="4" weight="medium" color="gray" highContrast>
            Completing email verification…
          </Text>
        </Flex>
      </FormPanel>
    </Flex>
  );
}

export default function ResetEmailPage() {
  return (
    <Suspense fallback={null}>
      <ResetEmailContent />
    </Suspense>
  );
}

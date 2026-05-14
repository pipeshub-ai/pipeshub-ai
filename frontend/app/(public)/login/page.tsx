'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/lib/store/auth-store';
import { toast } from '@/lib/store/toast-store';
import { GuestGuard } from '@/app/components/ui/guest-guard';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import { useAuthWideLayout } from '@/lib/hooks/use-breakpoint';
import AuthHero from '../components/auth-hero';
import FormPanel from '../components/form-panel';
import { SingleProvider, MultipleProviders } from '../forms';
import { AuthApi, type AuthMethod } from '../api';
import { getOrgExists } from '@/lib/api/org-exists-public';

// --- Auth step state machine --------------------------------------------------

type AuthStep =
  | { type: 'loading' }
  | {
    type: 'single';
    method: AuthMethod;
    authProviders: Record<string, Record<string, string>>;
  }
  | {
    type: 'multiple';
    allowedMethods: AuthMethod[];
    authProviders: Record<string, Record<string, string>>;
  };

/** Backend SAML error codes → short user-facing descriptions. */
const SAML_ERROR_DESCRIPTIONS: Record<string, string> = {
  jit_Disabled: 'JIT Disabled',
};

function getSamlErrorDescription(code: string): string {
  return SAML_ERROR_DESCRIPTIONS[code] ?? code.replace(/_/g, ' ');
}

// --- Page ---------------------------------------------------------------------

/**
 * LoginPage - orchestrates the multi-step sign-in flow.
 *
 * On mount it calls initAuth automatically to fetch allowed methods for the
 * current session.
 *
 * Steps:
 *   1. 'loading'  - Auto-initAuth on mount.
 *   2. 'single'   - Exactly 1 allowed method  => SingleProvider (email entered in-form).
 *   3. 'multiple' - 2+ allowed methods         => MultipleProviders.
 */
export default function LoginPage() {
  const router = useRouter();
  const splitLayout = useAuthWideLayout();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const { t } = useTranslation();
  const [step, setStep] = useState<AuthStep>({ type: 'loading' });

  // Prevents the initAuth call from running twice in React Strict Mode
  // (where mount effects are intentionally run twice in development).
  const initAuthCalledRef = useRef(false);
  const samlErrorHandledRef = useRef(false);
  const emailVerifyHandledRef = useRef(false);

  useEffect(() => {
    if (!isHydrated) return;
    if (emailVerifyHandledRef.current) return;
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    const emailVerify = params.get('email_verify');
    if (emailVerify === 'success' || emailVerify === 'error') {
      emailVerifyHandledRef.current = true;
      if (emailVerify === 'success') {
        toast.success(t('auth.login.emailVerifiedTitle'), {
          description: t('auth.login.emailVerifiedDescription'),
        });
      } else {
        const detail = params.get('email_verify_msg');
        toast.error(t('auth.login.emailVerifyFailedTitle'), {
          description: detail?.trim() || t('auth.login.emailVerifyFailedDescription'),
        });
      }
      router.replace('/login');
      return;
    }
  }, [isHydrated, router]);

  useEffect(() => {
    if (!isHydrated) return;
    if (samlErrorHandledRef.current) return;
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);

    const samlErrorCode = params.get('saml_error');
    if (samlErrorCode) {
      samlErrorHandledRef.current = true;
      toast.error(t('auth.login.samlErrorTitle'), {
        description: getSamlErrorDescription(samlErrorCode),
      });
      router.replace('/login');
      return;
    }

    if (params.get('error') === 'saml_sso') {
      samlErrorHandledRef.current = true;
      toast.error(t('auth.login.samlSignInFailedTitle'), {
        description: t('auth.login.samlSignInFailedDescription'),
      });
      router.replace('/login');
    }
  }, [isHydrated, router]);

  useEffect(() => {
    if (!isHydrated) return;
    if (initAuthCalledRef.current) return;
    initAuthCalledRef.current = true;

    let cancelled = false;

    void getOrgExists()
      .then(({ exists }) => {
        if (!exists) {
          router.replace('/sign-up');
          return;
        }
        // if (cancelled) return;
        return AuthApi.initAuth();
      })
      .then((response) => {
        // if (cancelled || response === undefined) return;
        const methods = response.allowedMethods ?? [];
        const providers = response.authProviders ?? {};
        if (methods.length <= 1) {
          setStep({
            type: 'single',
            method: methods[0] ?? 'password',
            authProviders: providers,
          });
        } else {
          setStep({
            type: 'multiple',
            allowedMethods: methods,
            authProviders: providers,
          });
        }
      })
      .catch(() => {
        if (cancelled) return;
        setStep({
          type: 'single',
          method: 'password',
          authProviders: {},
        });
      });

    return () => {
      cancelled = true;
    };
  }, [isHydrated, router]);

  function renderForm() {
    switch (step.type) {
      case 'loading':
        // Avoid empty FormPanel (especially with narrow layout / AuthHero hidden === null).
        return <LoadingScreen />;

      case 'single':
        return (
          <SingleProvider
            method={step.method}
            authProviders={step.authProviders}
          />
        );

      case 'multiple':
        return (
          <MultipleProviders
            allowedMethods={step.allowedMethods}
            authProviders={step.authProviders}
          />
        );
    }
  }

  return (
    <GuestGuard>
      <Flex
        direction={splitLayout ? 'row' : 'column'}
        style={{
          minHeight: '100dvh',
          overflow: splitLayout ? 'hidden' : undefined,
        }}
      >
        <AuthHero splitLayout={splitLayout} />
        <FormPanel splitLayout={splitLayout}>{renderForm()}</FormPanel>
      </Flex>
    </GuestGuard>
  );
}

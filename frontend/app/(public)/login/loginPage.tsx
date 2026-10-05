'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/config';
import { toast } from '@/lib/store/toast-store';
import { GuestGuard } from '@/app/components/ui/guest-guard';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import { useAuthWideLayout } from '@/lib/hooks/use-breakpoint';
import AuthHero from '../components/auth-hero';
import FormPanel from '../components/form-panel';
import { SingleProvider, MultipleProviders } from '../forms';
import { AuthApi, type AuthMethod } from '../api';
import { getOrgExists, invalidateOrgExistsCache } from '@/lib/api/org-exists-public';
import { getSamlErrorDescription } from '@/lib/auth/saml-errors';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { isElectron } from '@/lib/electron';
import { requestElectronServerUrlChange } from '@/lib/store/auth-store';

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
  }
  | { type: 'error'; message: string };

function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="3" align="center" style={{ textAlign: 'center' }}>
      <Heading size="4">{t('auth.login.loadFailedTitle')}</Heading>
      <Text size="2" color="gray">
        {message}
      </Text>
      <Flex gap="2">
        <Button type="button" size="2" onClick={onRetry}>
          {t('auth.login.retry')}
        </Button>
        {isElectron() && (
          <Button
            type="button"
            size="2"
            variant="soft"
            onClick={() => requestElectronServerUrlChange()}
          >
            {t('electron.serverUrlSetup.changeServer')}
          </Button>
        )}
      </Flex>
    </Flex>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const splitLayout = useAuthWideLayout();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const { t } = useTranslation();
  const [step, setStep] = useState<AuthStep>({ type: 'loading' });

  // Prevents the initAuth call from running twice in React Strict Mode
  // (where mount effects are intentionally run twice in development).
  const initAuthCalledRef = useRef(-1);
  const [attempt, setAttempt] = useState(0);
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
    if (initAuthCalledRef.current === attempt) return;
    initAuthCalledRef.current = attempt;

    void getOrgExists()
      .then(({ exists }) => {
        if (!exists) {
          router.replace('/sign-up');
          return;
        }
        return AuthApi.initAuth();
      })
      .then((response) => {
        if (response === undefined) return;
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
      .catch((err: unknown) => {
        // Not a password-only fallback: that hides an unreachable server behind
        // a form that can only fail.
        setStep({
          type: 'error',
          message: getUserFacingErrorMessage(err, t('auth.login.loadFailedDescription')),
        });
      });
  }, [isHydrated, router, attempt, t]);

  const retry = () => {
    invalidateOrgExistsCache();
    setStep({ type: 'loading' });
    setAttempt((n) => n + 1);
  };

  function renderForm() {
    switch (step.type) {
      case 'error':
        return <LoadFailed message={step.message} onRetry={retry} />;

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

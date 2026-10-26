'use client';

import React, { useRef, useState } from 'react';
import { Box, Flex, Text, Callout } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { isValidEmail } from '@/lib/utils/validators';
import { LoadingButton } from '@/app/components/ui/loading-button';
import AuthTitleSection from '../components/auth-title-section';
import { EmailField, PasswordField } from './form-components';
import GoogleSignInButton from './form-components/google-sign-in-button';
import MicrosoftSignInButton from './form-components/microsoft-sign-in-button';
import { useAuthActions } from '../hooks/use-auth-actions';
import { getSafeReturnTo } from '@/lib/utils/safe-return-to';
import type { AuthMethod } from '../api';
import { useSearchParams } from 'next/navigation';
import OtpSignInFlow from './otp-sign-in-flow';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface SingleProviderProps {
  /** The single allowed auth method. */
  method: AuthMethod;
  /** Provider-specific config (redirect URLs etc.). */
  authProviders: Record<string, Record<string, string>>;
  /** Go back to the email step or leave an OTP sub-step. */
  onBack?: () => void;
  /** When true the AuthTitleSection (logo + heading) is not rendered. */
  hideTitle?: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * SingleProvider — layout for exactly ONE auth method.
 *
 * Renders different layouts depending on the method:
 *
 * | method   | Layout                                                    |
 * |----------|-----------------------------------------------------------|
 * | otp      | Title → Email → Send OTP → Verify code → Sign In (same layout as email step) |
 * | password | Title → Email → Password → Forgot → Sign In              |
 * | google   | Title → Google button → Go Back                          |
 * | microsoft| Title → Microsoft button → Go Back                       |
 */
export default function SingleProvider({
  method,
  authProviders,
  onBack,
  hideTitle = false,
}: SingleProviderProps) {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [emailError, setEmailError] = useState('');
  const [passwordRequiredError, setPasswordRequiredError] = useState('');
  const [providerError, setProviderError] = useState('');
  const searchParams = useSearchParams();
  const returnTo = getSafeReturnTo(searchParams.get('returnTo'));
  const auth = useAuthActions({ email, authProviders, redirectTo: returnTo ?? undefined });
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  const ensureValidEmail = () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setEmailError(t('auth.common.emailRequired'));
      emailRef.current?.focus();
      return false;
    }
    if (!isValidEmail(trimmedEmail)) {
      setEmailError(t('auth.common.emailInvalid'));
      emailRef.current?.focus();
      return false;
    }
    if (emailError) {
      setEmailError('');
    }
    return true;
  };

  const handlePasswordSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ensureValidEmail()) {
      return;
    }
    if (!password) {
      setPasswordRequiredError(t('auth.common.passwordRequired'));
      passwordRef.current?.focus();
      return;
    }
    setPasswordRequiredError('');
    auth.signInWithPassword(password);
  };

  const handleMicrosoftLoginSuccess = async (credentials: { accessToken: string; idToken: string }) => {
    try {
      await auth.signInWithMicrosoft(credentials);
    } catch {
      setProviderError(t('auth.common.authFailed'));
    }
  };

  // ── Email OTP only ───────────────────────────────────────────────────────

  if (method === 'otp') {
    return (
      <Box style={{ width: '100%', maxWidth: '440px' }}>
        {!hideTitle && <AuthTitleSection />}
        <OtpSignInFlow
          email={email}
          onEmailChange={setEmail}
          lockEmail={false}
          onBack={onBack}
          sendLoginOtp={auth.sendLoginOtp}
          signInWithOtp={auth.signInWithOtp}
          otpSendLoading={auth.otpSendLoading}
          otpVerifyLoading={auth.otpVerifyLoading}
          error={auth.error}
          clearError={auth.clearError}
        />
      </Box>
    );
  }

  // ── Password only ─────────────────────────────────────────────────────────

  if (method === 'password') {
    const inlinePasswordError =
      passwordRequiredError ||
      (auth.error?.type === 'wrongPassword'
        ? t('auth.common.incorrectPassword')
        : auth.error?.type === 'noPasswordSet'
          ? t('auth.common.noPasswordSet')
          : undefined);

    return (
      <Box style={{ width: '100%', maxWidth: '440px' }}>
        {!hideTitle && <AuthTitleSection />}

        <form onSubmit={handlePasswordSubmit}>
          <Flex direction="column" gap="4">
            <EmailField
              ref={emailRef}
              value={email}
              onChange={(value) => {
                setEmail(value);
                if (emailError) {
                  setEmailError('');
                }
                auth.clearError();
              }}
              error={emailError}
              autoFocus
            />

            <PasswordField
              ref={passwordRef}
              value={password}
              onChange={(v) => {
                setPassword(v);
                setPasswordRequiredError('');
                auth.clearError();
              }}
              error={inlinePasswordError}
              showForgotPassword
              onForgotPassword={() => {
                if (!ensureValidEmail()) {
                  return;
                }
                auth.forgotPassword();
              }}
              forgotLoading={auth.forgotLoading}
            />

            {auth.error?.type === 'generic' && auth.error.message && (
              <Callout.Root color="red" size="1" variant="surface">
                <Callout.Text>
                  <Text size="2">{auth.error.message}</Text>
                </Callout.Text>
              </Callout.Root>
            )}

            <Flex gap="2">

              <LoadingButton
                type="submit"
                size="3"
                disabled={!password || !email.trim() || !isValidEmail(email.trim())}
                loading={auth.loading}
                loadingLabel={t('auth.common.signingIn')}
                style={{
                  flex: 1,
                  backgroundColor: 'var(--accent-9)',
                  color: 'white',
                  fontWeight: 500,
                }}
              >
                {t('auth.common.signIn')}
              </LoadingButton>
            </Flex>
          </Flex>
        </form>
      </Box>
    );
  }

  // ── Google only ────────────────────────────────────────────────────────────

  if (method === 'google') {
    const googleClientId = authProviders?.google?.clientId as string | undefined;
    return (
      <Box style={{ width: '100%', maxWidth: '440px' }}>
        {!hideTitle && <AuthTitleSection />}
        <Flex direction="column" gap="4">
          {providerError && (
            <Callout.Root color="red" size="1" variant="surface">
              <Callout.Text>
                <Text size="2">{providerError}</Text>
              </Callout.Text>
            </Callout.Root>
          )}
          <Flex gap="2">

            <Box style={{ flex: 1 }}>
              {googleClientId ? (
                <GoogleSignInButton
                  clientId={googleClientId}
                  onSuccess={(credential) => {
                    setProviderError('');
                    auth.signInWithGoogle(credential);
                  }}
                  onError={setProviderError}
                  primary
                />
              ) : (
                <Text size="2" color="gray">{t('auth.common.googleNotConfigured')}</Text>
              )}
            </Box>
          </Flex>
        </Flex>
      </Box>
    );
  }

  // ── Microsoft only ─────────────────────────────────────────────────────

  if (method === 'microsoft') {
    const msClientId = authProviders?.microsoft?.clientId as string | undefined;
    const msAuthority: string | undefined = authProviders?.microsoft?.authority;
    return (
      <Box style={{ width: '100%', maxWidth: '440px' }}>
        {!hideTitle && <AuthTitleSection />}
        <Flex direction="column" gap="4">
          {providerError && (
            <Callout.Root color="red" size="1" variant="surface">
              <Callout.Text>
                <Text size="2">{providerError}</Text>
              </Callout.Text>
            </Callout.Root>
          )}
          <Flex gap="2">

            <Box style={{ flex: 1 }}>
              {msClientId ? (
                <MicrosoftSignInButton
                  clientId={msClientId}
                  authority={msAuthority}
                  authLoading={auth.microsoftLoading}
                  onSuccess={handleMicrosoftLoginSuccess}
                  onError={setProviderError}
                  primary
                />
              ) : (
                <Text size="2" color="gray">{t('auth.common.microsoftNotConfigured')}</Text>
              )}
            </Box>
          </Flex>
        </Flex>
      </Box>
    );
  }

  // Fallback — unknown method
  return null;
}

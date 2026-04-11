'use client';

import React, { useState } from 'react';
import { Box, Flex, Button, Text } from '@radix-ui/themes';
import { useSearchParams } from 'next/navigation';
import AuthTitleSection from '../components/auth-title-section';
import {
  EmailField,
  PasswordField,
  ProviderButton,
  getSamlProviderNameFromAuthProviders,
  Divider,
  ErrorBanner,
} from './form-components';
import GoogleSignInButton from './form-components/google-sign-in-button';
import MicrosoftSignInButton from './form-components/microsoft-sign-in-button';
import OAuthSignInButton from './form-components/oauth-sign-in-button';
import { useAuthActions } from '../hooks/use-auth-actions';
import type { AuthMethod } from '../api';
import OtpSignInFlow from './otp-sign-in-flow';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface MultipleProvidersProps {
  /** Auth methods returned by initAuth (always 2+). */
  allowedMethods: AuthMethod[];
  /** Provider-specific config (redirect URLs etc.). */
  authProviders: Record<string, Record<string, string>>;
  /** Go back to the email step. */
  onBack?: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * MultipleProviders — layout for 2+ auth methods.
 *
 * Adapts layout based on the combination of allowed methods:
 *
 * | Combination                 | Layout                                                        |
 * |-----------------------------|---------------------------------------------------------------|
 * | password + Google           | Email → Password → Sign In → divider("…Google") → Google      |
 * | password + Microsoft        | Email → Password → Sign In → divider("…Microsoft") → MS       |
 * | password + SSO              | Email → Password → Sign In → SSO                              |
 * | password + SSO + Google     | Email → Password → Sign In → SSO → divider → Google           |
 * | password + SSO + G + MS     | Email → Password → Sign In → SSO → divider("…any one") → G+MS |
 * | SSO + Google (no password)  | Email → SSO (primary) → divider → Google                      |
 * | Google + Microsoft (no pw)  | Email → Google → MS → Go Back                                 |
 */
export default function MultipleProviders({
  allowedMethods,
  authProviders,
  onBack,
}: MultipleProvidersProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [oauthError, setOauthError] = useState('');
  const [authVariant, setAuthVariant] = useState<'password' | 'otp'>('password');
  const searchParams = useSearchParams();
  const returnTo = searchParams.get('returnTo');
  const auth = useAuthActions({
    email,
    authProviders,
    redirectTo: returnTo ?? undefined,
  });

  // ── Derive which methods are present ──────────────────────────────────────

  const hasPassword = allowedMethods.includes('password');
  const hasOtp = allowedMethods.includes('otp');
  const hasSso = allowedMethods.includes('samlSso');
  const hasGoogle = allowedMethods.includes('google');
  const hasMicrosoft =
    allowedMethods.includes('microsoft') || allowedMethods.includes('azureAd');
  const hasOAuth = allowedMethods.includes('oauth');
  const hasSocial = hasGoogle || hasMicrosoft || hasOAuth;

  // Derived Microsoft provider details
  const msMethod: 'microsoft' | 'azureAd' = allowedMethods.includes('azureAd') ? 'azureAd' : 'microsoft';
  const googleClientId: string | undefined = authProviders?.google?.clientId;
  const msClientId: string | undefined =
    authProviders?.microsoft?.clientId || authProviders?.azureAd?.clientId;
  const msAuthority: string | undefined =
    authProviders?.microsoft?.authority || authProviders?.azureAd?.authority;

  // Derived generic OAuth provider details
  const oauthConfig = authProviders?.oauth as
    | { clientId?: string; authorizationUrl?: string; providerName?: string; scope?: string; redirectUri?: string }
    | undefined;
  const oauthClientId = oauthConfig?.clientId;
  const oauthAuthUrl = oauthConfig?.authorizationUrl;
  const oauthProviderName = oauthConfig?.providerName ?? 'OAuth';

  // ── Divider label ─────────────────────────────────────────────────────────

  const socialNames = [
    hasGoogle && 'Google',
    hasMicrosoft && 'Microsoft',
    hasOAuth && oauthProviderName,
  ].filter(Boolean);

  const dividerLabel =
    socialNames.length === 1
      ? `or continue with ${socialNames[0]}`
      : 'or continue with any one of them';

  // ── Generic error ─────────────────────────────────────────────────────────

  const genericError =
    auth.error?.type === 'generic' ? auth.error.message : null;

  const inlinePasswordError =
    auth.error?.type === 'wrongPassword'
      ? 'Incorrect password.'
      : auth.error?.type === 'noPasswordSet'
        ? 'No password set. Use Forgot Password below.'
        : undefined;

  // ── Password submit ───────────────────────────────────────────────────────

  const handlePasswordSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    auth.signInWithPassword(password);
  };

  // ── With password: Email → Password → Sign In → extras ────────────────────

  if (hasPassword) {
    if (hasOtp && authVariant === 'otp') {
      return (
        <Box style={{ width: '100%', maxWidth: '440px' }}>
          <AuthTitleSection />
          <OtpSignInFlow
            email={email}
            onEmailChange={setEmail}
            lockEmail={false}
            onBack={() => setAuthVariant('password')}
            sendLoginOtp={auth.sendLoginOtp}
            signInWithOtp={auth.signInWithOtp}
            otpSendLoading={auth.otpSendLoading}
            otpVerifyLoading={auth.otpVerifyLoading}
            error={auth.error}
            clearError={auth.clearError}
          />
          <Flex justify="center" style={{ marginTop: 'var(--space-3)' }}>
            <Text
              size="2"
              style={{
                color: 'var(--accent-11)',
                fontWeight: 500,
                cursor: 'pointer',
                userSelect: 'none',
              }}
              onClick={() => {
                auth.clearError();
                setAuthVariant('password');
              }}
            >
              Sign in with password instead
            </Text>
          </Flex>
        </Box>
      );
    }

    return (
      <Box style={{ width: '100%', maxWidth: '440px' }}>
        <AuthTitleSection />

        <form onSubmit={handlePasswordSubmit}>
          <Flex direction="column" gap="4">
            <EmailField
              value={email}
              onChange={(v) => {
                setEmail(v);
                auth.clearError();
              }}
            />

            <PasswordField
              value={password}
              onChange={(v) => {
                setPassword(v);
                auth.clearError();
              }}
              error={inlinePasswordError}
              showForgotPassword
              onForgotPassword={auth.forgotPassword}
              forgotLoading={auth.forgotLoading}
              autoFocus
            />

            {genericError && <ErrorBanner message={genericError} />}

            <Flex gap="2">
              {onBack && (
                <Button
                  type="button"
                  size="3"
                  onClick={onBack}
                  style={{
                    aspectRatio: '1',
                    flexShrink: 0,
                    padding: 0,
                    backgroundColor: 'var(--accent-9)',
                    color: 'white',
                    cursor: 'pointer',
                  }}
                >
                  <span className="material-icons-outlined" style={{ fontSize: '18px' }}>arrow_back</span>
                </Button>
              )}
              <Button
                type="submit"
                size="3"
                disabled={auth.loading || !password}
                style={{
                  flex: 1,
                  backgroundColor: 'var(--accent-9)',
                  color: 'white',
                  fontWeight: 500,
                  cursor: auth.loading ? 'not-allowed' : 'pointer',
                }}
              >
                {auth.loading ? 'Signing in…' : 'Sign In'}
              </Button>
            </Flex>

            {hasOtp && (
              <Flex justify="center">
                <Text
                  size="2"
                  style={{
                    color: 'var(--accent-11)',
                    fontWeight: 500,
                    cursor: 'pointer',
                    userSelect: 'none',
                  }}
                  onClick={() => {
                    auth.clearError();
                    setAuthVariant('otp');
                  }}
                >
                  Sign in with email OTP
                </Text>
              </Flex>
            )}

            {/* SSO sits directly below Sign In (no divider) */}
            {hasSso && (
              <ProviderButton
                provider="sso"
                samlProviderName={getSamlProviderNameFromAuthProviders(authProviders)}
                onClick={auth.redirectToSSO}
              />
            )}

            {/* Social providers below a divider */}
            {hasSocial && (
              <>
                <Divider label={dividerLabel} />
                {oauthError && <ErrorBanner message={oauthError} />}
                {hasGoogle && googleClientId && (
                  <GoogleSignInButton
                    clientId={googleClientId}
                    onSuccess={(credential) => {
                      setOauthError('');
                      auth.signInWithGoogle(credential);
                    }}
                    onError={setOauthError}
                  />
                )}
                {hasGoogle && !googleClientId && (
                  <ErrorBanner message="Google sign-in is currently unavailable. Contact your administrator." />
                )}
                {hasMicrosoft && msClientId && (
                  <MicrosoftSignInButton
                    clientId={msClientId}
                    authority={msAuthority}
                    authLoading={auth.microsoftLoading}
                    onSuccess={(credentials) => {
                      setOauthError('');
                      auth.signInWithMicrosoft(credentials, msMethod);
                    }}
                    onError={setOauthError}
                  />
                )}
                {hasMicrosoft && !msClientId && (
                  <ErrorBanner message="Microsoft sign-in is currently unavailable. Contact your administrator." />
                )}
                {hasOAuth && oauthClientId && oauthAuthUrl && (
                  <OAuthSignInButton
                    providerName={oauthProviderName}
                    clientId={oauthClientId}
                    authorizationUrl={oauthAuthUrl}
                    scope={oauthConfig?.scope}
                    redirectUri={oauthConfig?.redirectUri}
                    onSuccess={(accessToken) => {
                      setOauthError('');
                      auth.signInWithOAuth(accessToken);
                    }}
                    onError={setOauthError}
                    loading={auth.oauthLoading}
                  />
                )}
                {hasOAuth && (!oauthClientId || !oauthAuthUrl) && (
                  <ErrorBanner message="OAuth sign-in is currently unavailable. Contact your administrator." />
                )}
              </>
            )}
          </Flex>
        </form>
      </Box>
    );
  }

  // ── Without password: provider buttons only ───────────────────────────────

  return (
    <Box style={{ width: '100%', maxWidth: '440px' }}>
      <AuthTitleSection />

      <Flex direction="column" gap="4">
        {hasOtp ? (
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
        ) : (
          <EmailField
            value={email}
            onChange={(v) => {
              setEmail(v);
              auth.clearError();
            }}
          />
        )}

        {hasOtp && (hasSso || hasSocial) && (
          <Divider
            label={
              hasSso && hasSocial
                ? dividerLabel
                : hasSso
                  ? 'or continue with Single Sign-On'
                  : dividerLabel
            }
          />
        )}

        {/* First provider is primary-styled, paired with back button */}
        {hasSso && (
          <Flex gap="2">
            {onBack && (
              <Button
                type="button"
                size="3"
                onClick={onBack}
                style={{
                  aspectRatio: '1',
                  flexShrink: 0,
                  padding: 0,
                  backgroundColor: 'var(--accent-a3)',
                  color: 'var(--accent-11)',
                  cursor: 'pointer',
                }}
              >
                <span className="material-icons-outlined" style={{ fontSize: '18px' }}>arrow_back</span>
              </Button>
            )}
            <Box style={{ flex: 1 }}>
              <ProviderButton
                provider="sso"
                samlProviderName={getSamlProviderNameFromAuthProviders(authProviders)}
                onClick={auth.redirectToSSO}
                primary
              />
            </Box>

          </Flex>
        )}

        {hasSocial && hasSso && <Divider label={dividerLabel} />}

        {oauthError && <ErrorBanner message={oauthError} />}
        {hasGoogle && !googleClientId && (
          <ErrorBanner message="Google sign-in is currently unavailable. Contact your administrator." />
        )}
        {hasMicrosoft && !msClientId && (
          <ErrorBanner message="Microsoft sign-in is currently unavailable. Contact your administrator." />
        )}
        {hasOAuth && (!oauthClientId || !oauthAuthUrl) && (
          <ErrorBanner message="OAuth sign-in is currently unavailable. Contact your administrator." />
        )}

        {hasGoogle && googleClientId && (
          !hasSso ? (
            <Flex gap="2">
              {onBack && (
                <Button
                  type="button"
                  size="3"
                  onClick={onBack}
                  style={{
                    aspectRatio: '1',
                    flexShrink: 0,
                    padding: 0,
                    backgroundColor: 'var(--accent-a3)',
                    color: 'var(--accent-11)',
                    cursor: 'pointer',
                  }}
                >
                  <span className="material-icons-outlined" style={{ fontSize: '18px' }}>arrow_back</span>
                </Button>
              )}
              <Box style={{ flex: 1 }}>
                <GoogleSignInButton
                  clientId={googleClientId}
                  onSuccess={(credential) => {
                    setOauthError('');
                    auth.signInWithGoogle(credential);
                  }}
                  onError={setOauthError}
                  primary
                />
              </Box>
            </Flex>
          ) : (
            <GoogleSignInButton
              clientId={googleClientId}
              onSuccess={(credential) => {
                setOauthError('');
                auth.signInWithGoogle(credential);
              }}
              onError={setOauthError}
            />
          )
        )}
        {hasMicrosoft && msClientId && (
          !hasSso && !hasGoogle ? (
            <Flex gap="2">
              {onBack && (
                <Button
                  type="button"
                  size="3"
                  onClick={onBack}
                  style={{
                    aspectRatio: '1',
                    flexShrink: 0,
                    padding: 0,
                    backgroundColor: 'var(--accent-a3)',
                    color: 'var(--accent-11)',
                    cursor: 'pointer',
                  }}
                >
                  <span className="material-icons-outlined" style={{ fontSize: '18px' }}>arrow_back</span>
                </Button>
              )}
              <Box style={{ flex: 1 }}>
                <MicrosoftSignInButton
                  clientId={msClientId}
                  authority={msAuthority}
                  authLoading={auth.microsoftLoading}
                  onSuccess={(credentials) => {
                    setOauthError('');
                    auth.signInWithMicrosoft(credentials, msMethod);
                  }}
                  onError={setOauthError}
                  primary
                />
              </Box>
            </Flex>
          ) : (
            <MicrosoftSignInButton
              clientId={msClientId}
              authority={msAuthority}
              authLoading={auth.microsoftLoading}
              onSuccess={(credentials) => {
                setOauthError('');
                auth.signInWithMicrosoft(credentials, msMethod);
              }}
              onError={setOauthError}
              primary={!hasSso && !hasGoogle}
            />
          )
        )}
        {hasOAuth && oauthClientId && oauthAuthUrl && (
          !hasSso && !hasGoogle && !hasMicrosoft ? (
            <Flex gap="2">
              {onBack && (
                <Button
                  type="button"
                  size="3"
                  onClick={onBack}
                  style={{
                    aspectRatio: '1',
                    flexShrink: 0,
                    padding: 0,
                    backgroundColor: 'var(--accent-a3)',
                    color: 'var(--accent-11)',
                    cursor: 'pointer',
                  }}
                >
                  <span className="material-icons-outlined" style={{ fontSize: '18px' }}>arrow_back</span>
                </Button>
              )}
              <Box style={{ flex: 1 }}>
                <OAuthSignInButton
                  providerName={oauthProviderName}
                  clientId={oauthClientId}
                  authorizationUrl={oauthAuthUrl}
                  scope={oauthConfig?.scope}
                  redirectUri={oauthConfig?.redirectUri}
                  onSuccess={(accessToken) => {
                    setOauthError('');
                    auth.signInWithOAuth(accessToken);
                  }}
                  onError={setOauthError}
                  loading={auth.oauthLoading}
                  primary
                />
              </Box>
            </Flex>
          ) : (
            <OAuthSignInButton
              providerName={oauthProviderName}
              clientId={oauthClientId}
              authorizationUrl={oauthAuthUrl}
              scope={oauthConfig?.scope}
              redirectUri={oauthConfig?.redirectUri}
              onSuccess={(accessToken) => {
                setOauthError('');
                auth.signInWithOAuth(accessToken);
              }}
              onError={setOauthError}
              loading={auth.oauthLoading}
              primary={!hasSso && !hasGoogle && !hasMicrosoft}
            />
          )
        )}

      </Flex>
    </Box>
  );
}

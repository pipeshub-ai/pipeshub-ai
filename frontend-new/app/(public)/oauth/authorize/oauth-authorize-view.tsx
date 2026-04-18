'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import axios, { AxiosError, isAxiosError } from 'axios';
import { Box, Button, Flex, Separator, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

import { apiClient } from '@/lib/api';
import { extractApiErrorMessage, processError } from '@/lib/api/api-error';
import { useAuthStore } from '@/lib/store/auth-store';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { OAuthConnectionOutcome } from '@/app/(public)/oauth/components/oauth-connection-outcome';

const OAUTH_AUTHORIZE_PATH = '/oauth/authorize';
const AUTHORIZE_API = '/api/v1/oauth2/authorize';

interface ScopeInfo {
  name: string;
  description: string;
  category: string;
}

interface AppInfo {
  name: string;
  description?: string;
  logoUrl?: string;
  homepageUrl?: string;
  privacyPolicyUrl?: string;
}

interface ConsentData {
  app: AppInfo;
  scopes: ScopeInfo[];
  user: {
    email: string;
    name?: string;
  };
  redirectUri: string;
  state?: string;
}

interface AuthorizeResponse {
  requiresConsent?: boolean;
  consentData?: ConsentData;
  codeChallenge?: string;
  codeChallengeMethod?: string;
  redirectUrl?: string;
  error?: string;
  error_description?: string;
}

function normalizeScopeForPost(scopeFromUrl: string | null): string {
  if (!scopeFromUrl) return '';
  return scopeFromUrl.replace(/\+/g, ' ').trim();
}

/** Extract OAuth error code from an Axios error response (e.g. "invalid_scope"). */
function extractOAuthErrorCode(data: unknown): string | null {
  if (
    typeof data === 'object' &&
    data !== null &&
    'error' in data &&
    typeof (data as { error?: string }).error === 'string'
  ) {
    return (data as { error: string }).error;
  }
  return null;
}

/** Extract the best human-readable message from an error, preferring error_description. */
function errorMessageFromUnknown(err: unknown): string {
  if (isAxiosError(err)) {
    const data = err.response?.data;
    // Prefer error_description (OAuth-style) over the generic error code
    if (
      typeof data === 'object' &&
      data !== null &&
      'error_description' in data &&
      typeof (data as { error_description?: string }).error_description === 'string' &&
      (data as { error_description: string }).error_description.trim()
    ) {
      return (data as { error_description: string }).error_description;
    }
    const fromBody = extractApiErrorMessage(data);
    if (fromBody) return fromBody;
    const processed = processError(err as AxiosError);
    return processed.message;
  }
  if (err instanceof Error) return err.message;
  return 'An error occurred';
}

/** Extract the OAuth error code from an error, if present. */
function errorCodeFromUnknown(err: unknown): string | null {
  if (isAxiosError(err)) {
    return extractOAuthErrorCode(err.response?.data);
  }
  return null;
}

/** Parse OAuth error params from a redirect URL, if any. */
function extractErrorFromRedirectUrl(
  url: string
): { code: string; description: string } | null {
  try {
    const parsed = new URL(url);
    const error = parsed.searchParams.get('error');
    if (!error) return null;
    const description =
      parsed.searchParams.get('error_description') || error;
    return { code: error, description };
  } catch {
    return null;
  }
}

type ConsentOutcome =
  | { type: 'idle' }
  | { type: 'success'; redirectUrl: string; consent: 'granted' | 'denied' }
  | { type: 'error'; message: string };

export function OAuthAuthorizeView() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [errorCode, setErrorCode] = useState('');
  const [outcome, setOutcome] = useState<ConsentOutcome>({ type: 'idle' });
  const [consentData, setConsentData] = useState<ConsentData | null>(null);
  const [codeChallenge, setCodeChallenge] = useState('');
  const [codeChallengeMethod, setCodeChallengeMethod] = useState('');

  const clientId = searchParams.get('client_id');
  const redirectUri = searchParams.get('redirect_uri');
  const scope = searchParams.get('scope');
  const state = searchParams.get('state');

  const queryParams = useMemo(() => {
    const p: Record<string, string> = {};
    searchParams.forEach((value, key) => {
      p[key] = value;
    });
    return p;
  }, [searchParams]);

  const returnToPath = useMemo(() => {
    const q = searchParams.toString();
    return q ? `${OAUTH_AUTHORIZE_PATH}?${q}` : OAUTH_AUTHORIZE_PATH;
  }, [searchParams]);

  // Redirect unauthenticated users to login with returnTo (canonical OAuth path)
  useEffect(() => {
    if (!isHydrated) return;
    if (!isAuthenticated) {
      router.replace(`/login?returnTo=${encodeURIComponent(returnToPath)}`);
    }
  }, [isHydrated, isAuthenticated, router, returnToPath]);

  // Load consent data when authenticated and params are valid
  useEffect(() => {
    if (!isHydrated || !isAuthenticated) return;

    if (!clientId || !redirectUri) {
      setError(t('oauthConsent.missingParams'));
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    let cancelled = false;

    (async () => {
      setLoading(true);
      setError('');
      try {
        const { data } = await apiClient.get<AuthorizeResponse>(AUTHORIZE_API, {
          params: queryParams,
          signal: controller.signal,
          suppressErrorToast: true,
        });

        if (cancelled) return;

        if (data.redirectUrl) {
          // Check if the redirect URL carries OAuth error params (e.g. invalid_scope).
          // Show them on the consent page instead of silently redirecting.
          const redirectError = extractErrorFromRedirectUrl(data.redirectUrl);
          if (redirectError) {
            setErrorCode(redirectError.code);
            setError(redirectError.description);
          } else {
            window.location.href = data.redirectUrl;
          }
          return;
        }

        if (data.error) {
          setErrorCode(data.error);
          setError(data.error_description || data.error);
          return;
        }

        if (data.requiresConsent && data.consentData) {
          setConsentData(data.consentData);
          setCodeChallenge(data.codeChallenge ?? '');
          setCodeChallengeMethod(data.codeChallengeMethod ?? '');
        } else {
          setError(t('oauthConsent.noData'));
        }
      } catch (err) {
        if (axios.isCancel(err)) return;
        if (cancelled) return;
        const code = errorCodeFromUnknown(err);
        if (code) setErrorCode(code);
        setError(errorMessageFromUnknown(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [isHydrated, isAuthenticated, clientId, redirectUri, queryParams, t]);

  const successRedirectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (outcome.type !== 'success') return;
    const url = outcome.redirectUrl;
    successRedirectTimerRef.current = setTimeout(() => {
      successRedirectTimerRef.current = null;
      window.location.assign(url);
    }, 1000);
    return () => {
      if (successRedirectTimerRef.current) {
        clearTimeout(successRedirectTimerRef.current);
        successRedirectTimerRef.current = null;
      }
    };
  }, [outcome]);

  const handleConsent = async (consent: 'granted' | 'denied') => {
    if (!clientId || !redirectUri) return;

    setSubmitting(true);
    try {
      const cc =
        codeChallenge || searchParams.get('code_challenge') || undefined;
      const ccm =
        codeChallengeMethod ||
        searchParams.get('code_challenge_method') ||
        undefined;

      const { data } = await apiClient.post<{ redirectUrl?: string }>(
        AUTHORIZE_API,
        {
          client_id: clientId,
          redirect_uri: redirectUri,
          scope: normalizeScopeForPost(scope),
          state: state ?? '',
          code_challenge: cc,
          code_challenge_method: ccm,
          consent,
        },
        { suppressErrorToast: true }
      );

      if (data.redirectUrl) {
        setOutcome({ type: 'success', redirectUrl: data.redirectUrl, consent });
        return;
      }
      setOutcome({ type: 'error', message: t('oauthConsent.noRedirect') });
    } catch (err) {
      setOutcome({ type: 'error', message: errorMessageFromUnknown(err) });
    } finally {
      setSubmitting(false);
    }
  };

  if (!isHydrated) {
    return <LoadingScreen />;
  }

  if (!isAuthenticated) {
    return <LoadingScreen />;
  }

  if (loading) {
    return (
      <Flex
        align="center"
        justify="center"
        direction="column"
        gap="3"
        style={{ minHeight: '100vh', padding: 'var(--space-5)' }}
      >
        <LottieLoader variant="loader" size={64} />
        <Text size="2" color="gray">
          {t('oauthConsent.loading')}
        </Text>
      </Flex>
    );
  }

  if (error) {
    const errorTitle = errorCode
      ? t(`oauthConsent.errorTitles.${errorCode}`, { defaultValue: '' }) ||
        t('oauthConsent.errorTitle')
      : t('oauthConsent.errorTitle');

    const errorHint = errorCode
      ? t(`oauthConsent.errorHints.${errorCode}`, { defaultValue: '' })
      : '';

    return (
      <Flex
        align="center"
        justify="center"
        style={{
          minHeight: '100vh',
          padding: 'var(--space-5)',
          background: 'var(--gray-2)',
        }}
      >
        <Box
          style={{
            maxWidth: 520,
            width: '100%',
            padding: 'var(--space-5)',
            borderRadius: 'var(--radius-3)',
            border: '1px solid var(--gray-6)',
            background: 'var(--color-panel-solid)',
            boxShadow: 'var(--shadow-2)',
          }}
        >
          <Flex direction="column" gap="3" align="center">
            <Flex
              align="center"
              justify="center"
              style={{
                width: 48,
                height: 48,
                borderRadius: '50%',
                background: 'var(--red-3)',
              }}
            >
              <span
                className="material-icons-outlined"
                style={{ fontSize: 24, color: 'var(--red-11)' }}
              >
                error_outline
              </span>
            </Flex>

            <Text size="4" weight="bold" style={{ color: 'var(--gray-12)' }}>
              {errorTitle}
            </Text>

            {errorCode && (
              <Text
                as="div"
                size="1"
                style={{
                  fontFamily: 'var(--code-font-family, monospace)',
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-1)',
                  background: 'var(--red-3)',
                  color: 'var(--red-11)',
                }}
              >
                {errorCode}
              </Text>
            )}

            <Text
              as="div"
              size="2"
              style={{ color: 'var(--gray-11)', textAlign: 'center', lineHeight: 1.5 }}
            >
              {error}
            </Text>

            {errorHint && (
              <Box
                style={{
                  width: '100%',
                  padding: 'var(--space-3)',
                  borderRadius: 'var(--radius-2)',
                  background: 'var(--gray-3)',
                  border: '1px solid var(--gray-6)',
                }}
              >
                <Flex gap="2" align="start">
                  <span
                    className="material-icons-outlined"
                    style={{
                      fontSize: 16,
                      color: 'var(--gray-10)',
                      marginTop: 1,
                      flexShrink: 0,
                    }}
                  >
                    info
                  </span>
                  <Text size="2" style={{ color: 'var(--gray-11)', lineHeight: 1.5 }}>
                    {errorHint}
                  </Text>
                </Flex>
              </Box>
            )}
          </Flex>
        </Box>
      </Flex>
    );
  }

  if (!consentData) {
    return (
      <Flex
        align="center"
        justify="center"
        style={{ minHeight: '100vh', padding: 'var(--space-5)' }}
      >
        <Text size="2" color="gray">
          {t('oauthConsent.noData')}
        </Text>
      </Flex>
    );
  }

  if (outcome.type === 'success') {
    const granted = outcome.consent === 'granted';
    const scopePermissionSummary = consentData.scopes.map((s) => ({
      name: s.name,
      granted,
    }));
    return (
      <OAuthConnectionOutcome
        variant={granted ? 'success' : 'denied'}
        title={
          granted
            ? t('oauthConsent.outcomeAllowedTitle')
            : t('oauthConsent.outcomeDeniedTitle')
        }
        descriptionLines={
          granted
            ? [
                t('oauthConsent.outcomeAllowedLine1'),
                t('oauthConsent.outcomeAllowedLine2'),
              ]
            : [
                t('oauthConsent.outcomeDeniedLine1'),
                t('oauthConsent.outcomeDeniedLine2'),
              ]
        }
        scopePermissionSummary={scopePermissionSummary}
      />
    );
  }

  if (outcome.type === 'error') {
    return (
      <OAuthConnectionOutcome
        variant="error"
        title={t('oauthConsent.outcomeErrorTitle')}
        descriptionLines={[outcome.message]}
        primaryActionLabel={t('oauthConsent.tryAgain')}
        onPrimaryAction={() => setOutcome({ type: 'idle' })}
      />
    );
  }

  const appName = consentData.app.name;

  return (
    <Flex
      align="center"
      justify="center"
      style={{
        minHeight: '100vh',
        padding: 'var(--space-5)',
        background: 'var(--gray-2)',
      }}
    >
      <Box
        style={{
          width: '100%',
          maxWidth: 560,
          padding: 'var(--space-5)',
          borderRadius: 'var(--radius-3)',
          border: '1px solid var(--gray-6)',
          background: 'var(--color-panel-solid)',
          boxShadow: 'var(--shadow-2)',
        }}
      >
        <Flex direction="column" gap="4">
          <Flex align="center" gap="2">
            {consentData.app.logoUrl ? (
              <Box
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: 4,
                  overflow: 'hidden',
                  flexShrink: 0,
                }}
              >
                <img
                  src={consentData.app.logoUrl}
                  alt=""
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                />
              </Box>
            ) : (
              <Flex
                align="center"
                justify="center"
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: 4,
                  background: 'var(--gray-3)',
                  flexShrink: 0,
                }}
              >
                <span className="material-icons-outlined" style={{ fontSize: 14 }}>
                  settings_applications
                </span>
              </Flex>
            )}
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {appName}
            </Text>
          </Flex>

          <Separator size="4" />

          <Text as="div" size="2" color="gray" style={{ width: '100%' }}>
            {t('oauthConsent.signedInAs', {
              display:
                consentData.user.name && consentData.user.email
                  ? `${consentData.user.name} (${consentData.user.email})`
                  : consentData.user.name || consentData.user.email,
            })}
          </Text>

          <Separator size="4" />

          <Text size="3" weight="medium">
            {t('oauthConsent.requestHeading')}
          </Text>

          <Box
            style={{
              maxHeight: 320,
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              gap: 'var(--space-2)',
            }}
          >
            {consentData.scopes.map((scopeItem) => (
              <Flex
                key={scopeItem.name}
                direction="column"
                gap="1"
                align="start"
                p="3"
                width="100%"
                style={{
                  borderRadius: 'var(--radius-2)',
                  border: '1px solid var(--gray-6)',
                  background: 'var(--gray-2)',
                }}
              >
                <Text
                  as="div"
                  size="2"
                  weight="medium"
                  style={{ width: '100%', lineHeight: 'var(--line-height-2)' }}
                >
                  {scopeItem.name}
                </Text>
                <Text
                  as="div"
                  size="2"
                  color="gray"
                  style={{ width: '100%', lineHeight: 'var(--line-height-2)' }}
                >
                  {scopeItem.description}
                </Text>
              </Flex>
            ))}
          </Box>

          <Separator size="4" />

          <Text size="2" color="gray">
            {t('oauthConsent.legalNotice', { appName })}
            {consentData.app.privacyPolicyUrl ? (
              <>
                {' '}
                <a
                  href={consentData.app.privacyPolicyUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t('oauthConsent.privacyPolicy')}
                </a>
              </>
            ) : null}
          </Text>

          <Flex gap="3" width="100%">
            <Button
              type="button"
              variant="outline"
              color="gray"
              size="2"
              disabled={submitting}
              style={{ flex: 1, minHeight: 32 }}
              onClick={() => handleConsent('denied')}
            >
              {t('oauthConsent.deny')}
            </Button>
            <LoadingButton
              type="button"
              variant="solid"
              color="green"
              size="2"
              loading={submitting}
              loadingLabel={t('oauthConsent.submitting')}
              style={{
                flex: 1,
                minHeight: 32,
                backgroundColor: '#047857',
              }}
              onClick={() => handleConsent('granted')}
            >
              {t('oauthConsent.allow')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Box>
    </Flex>
  );
}

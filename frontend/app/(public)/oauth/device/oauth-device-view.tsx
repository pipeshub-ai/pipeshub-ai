'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import axios from 'axios';
import { Box, Button, Flex, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

import { apiClient } from '@/lib/api';
import { extractApiErrorMessage, processError } from '@/lib/api/api-error';
import { useAuthStore } from '@/config';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { LoadingButton } from '@/app/components/ui/loading-button';

const OAUTH_DEVICE_PATH = '/oauth/device';
const VERIFY_API = '/api/v1/oauth2/device/verify';
const CONSENT_API = '/api/v1/oauth2/device/consent';

interface ScopeInfo {
  name: string;
  description: string;
  category: string;
}

interface ConsentData {
  app: { name: string; description?: string };
  scopes: ScopeInfo[];
  user: { email: string; name?: string };
}

function errorMessageFromUnknown(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data;
    if (
      typeof data === 'object' &&
      data !== null &&
      'error_description' in data &&
      typeof (data as { error_description?: string }).error_description ===
        'string'
    ) {
      return (data as { error_description: string }).error_description;
    }
    const fromBody = extractApiErrorMessage(data);
    if (fromBody) return fromBody;
    return processError(err).message;
  }
  if (err instanceof Error) return err.message;
  return 'An error occurred';
}

export function OAuthDeviceView() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const initialCode = searchParams.get('user_code') ?? '';
  const [userCode, setUserCode] = useState(initialCode);
  const [loading, setLoading] = useState(Boolean(initialCode));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [consentData, setConsentData] = useState<ConsentData | null>(null);
  const [done, setDone] = useState<'granted' | 'denied' | null>(null);

  const returnToPath = useMemo(() => {
    const q = searchParams.toString();
    return q ? `${OAUTH_DEVICE_PATH}?${q}` : OAUTH_DEVICE_PATH;
  }, [searchParams]);

  useEffect(() => {
    if (!isHydrated) return;
    if (!isAuthenticated) {
      router.replace(`/login?returnTo=${encodeURIComponent(returnToPath)}`);
    }
  }, [isHydrated, isAuthenticated, router, returnToPath]);

  const lookup = async (code: string) => {
    setLoading(true);
    setError('');
    try {
      const { data } = await apiClient.post<{
        requiresConsent?: boolean;
        consentData?: ConsentData;
      }>(
        VERIFY_API,
        { user_code: code },
        { suppressErrorToast: true },
      );
      if (data.requiresConsent && data.consentData) {
        setConsentData(data.consentData);
      } else {
        setError(t('oauthConsent.noData'));
      }
    } catch (err) {
      setError(errorMessageFromUnknown(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isHydrated || !isAuthenticated || !initialCode) {
      if (!initialCode) setLoading(false);
      return;
    }
    void lookup(initialCode);
    // lookup on first authenticated landing with a code in the URL
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isHydrated, isAuthenticated, initialCode]);

  const handleConsent = async (consent: 'granted' | 'denied') => {
    setSubmitting(true);
    setError('');
    try {
      await apiClient.post(
        CONSENT_API,
        { user_code: userCode, consent },
        { suppressErrorToast: true },
      );
      setDone(consent);
    } catch (err) {
      setError(errorMessageFromUnknown(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (!isHydrated || !isAuthenticated) {
    return <LoadingScreen />;
  }

  if (done) {
    return (
      <Flex
        align="center"
        justify="center"
        direction="column"
        gap="3"
        style={{ minHeight: '100vh', padding: 'var(--space-5)' }}
      >
        <Text size="5" weight="bold">
          {done === 'granted'
            ? t('oauthDevice.doneTitle')
            : t('oauthDevice.deniedTitle')}
        </Text>
        <Text size="2" color="gray">
          {done === 'granted'
            ? t('oauthDevice.doneLine')
            : t('oauthDevice.deniedLine')}
        </Text>
      </Flex>
    );
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
          {t('oauthDevice.loading')}
        </Text>
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
        <Box style={{ width: '100%', maxWidth: 420 }}>
          <Text size="5" weight="bold">
            {t('oauthDevice.title')}
          </Text>
          <Text as="p" size="2" color="gray" mt="2">
            {t('oauthDevice.codeLabel')}
          </Text>
          <TextField.Root
            mt="3"
            size="3"
            placeholder={t('oauthDevice.codePlaceholder')}
            value={userCode}
            onChange={(e) => setUserCode(e.target.value)}
          />
          {error ? (
            <Text as="p" size="2" color="red" mt="2">
              {error}
            </Text>
          ) : null}
          <Button
            mt="4"
            onClick={() => lookup(userCode)}
            disabled={!userCode.trim()}
          >
            {t('oauthDevice.continue')}
          </Button>
        </Box>
      </Flex>
    );
  }

  return (
    <Flex
      align="center"
      justify="center"
      style={{ minHeight: '100vh', padding: 'var(--space-5)' }}
    >
      <Box style={{ width: '100%', maxWidth: 480 }}>
        <Text size="5" weight="bold">
          {consentData.app.name}
        </Text>
        <Text as="p" size="2" color="gray" mt="2">
          {t('oauthConsent.requestHeading')}
        </Text>
        <Box mt="3">
          {consentData.scopes.map((scope) => (
            <Text as="p" size="2" key={scope.name}>
              {scope.name}
              {scope.description ? ` — ${scope.description}` : ''}
            </Text>
          ))}
        </Box>
        {error ? (
          <Text as="p" size="2" color="red" mt="2">
            {error}
          </Text>
        ) : null}
        <Flex gap="3" mt="4">
          <Button
            variant="soft"
            color="gray"
            disabled={submitting}
            onClick={() => handleConsent('denied')}
          >
            {t('oauthConsent.deny')}
          </Button>
          <LoadingButton
            loading={submitting}
            onClick={() => handleConsent('granted')}
          >
            {t('oauthConsent.allow')}
          </LoadingButton>
        </Flex>
      </Box>
    </Flex>
  );
}

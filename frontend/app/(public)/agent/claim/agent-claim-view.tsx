'use client';

import { asArray } from '@/lib/utils/safe';
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

const AGENT_CLAIM_PATH = '/agent/claim';
const VERIFY_API = '/api/v1/agent/identity/claim/verify';
const COMPLETE_API = '/api/v1/agent/identity/claim/complete';

interface ClaimContext {
  scopes: Array<{ name: string; description?: string }>;
  user: { email: string };
}

function errorMessageFromUnknown(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data;
    if (typeof data?.error_description === 'string') return data.error_description;
    return extractApiErrorMessage(data) || processError(err).message;
  }
  return err instanceof Error ? err.message : 'An error occurred';
}

export function AgentClaimView() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const attemptToken = searchParams.get('claim_attempt_token') ?? '';
  const [context, setContext] = useState<ClaimContext | null>(null);
  const [userCode, setUserCode] = useState('');
  const [loading, setLoading] = useState(Boolean(attemptToken));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(attemptToken ? '' : t('agentClaim.missingLink'));
  const [done, setDone] = useState<'approve' | 'deny' | null>(null);

  const returnToPath = useMemo(() => {
    const q = searchParams.toString();
    return q ? `${AGENT_CLAIM_PATH}?${q}` : AGENT_CLAIM_PATH;
  }, [searchParams]);

  useEffect(() => {
    if (isHydrated && !isAuthenticated) {
      router.replace(`/login?returnTo=${encodeURIComponent(returnToPath)}`);
    }
  }, [isHydrated, isAuthenticated, router, returnToPath]);

  useEffect(() => {
    if (!isHydrated || !isAuthenticated || !attemptToken) return;
    apiClient
      .post<ClaimContext>(VERIFY_API, { claim_attempt_token: attemptToken }, { suppressErrorToast: true })
      .then(({ data }) => setContext(data))
      .catch((err) => setError(errorMessageFromUnknown(err)))
      .finally(() => setLoading(false));
  }, [isHydrated, isAuthenticated, attemptToken]);

  const decide = async (action: 'approve' | 'deny') => {
    setSubmitting(true);
    setError('');
    try {
      await apiClient.post(
        COMPLETE_API,
        { claim_attempt_token: attemptToken, user_code: userCode.trim(), action },
        { suppressErrorToast: true },
      );
      setDone(action);
    } catch (err) {
      setError(errorMessageFromUnknown(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (!isHydrated || !isAuthenticated) return <LoadingScreen />;

  const page = (children: React.ReactNode) => (
    <Flex
      align="center"
      justify="center"
      direction="column"
      gap="3"
      style={{ minHeight: '100vh', padding: 'var(--space-5)' }}
    >
      <Box style={{ width: '100%', maxWidth: 480 }}>{children}</Box>
    </Flex>
  );

  if (done) {
    return page(
      <>
        <Text size="5" weight="bold">
          {done === 'approve' ? t('agentClaim.doneTitle') : t('agentClaim.deniedTitle')}
        </Text>
        <Text as="p" size="2" color="gray" mt="2">
          {done === 'approve' ? t('agentClaim.doneLine') : t('agentClaim.deniedLine')}
        </Text>
      </>,
    );
  }

  if (loading) {
    return page(
      <Flex align="center" direction="column" gap="3">
        <LottieLoader variant="loader" size={64} />
        <Text size="2" color="gray">
          {t('agentClaim.loading')}
        </Text>
      </Flex>,
    );
  }

  if (!context) {
    return page(
      <>
        <Text size="5" weight="bold">
          {t('agentClaim.title')}
        </Text>
        <Text as="p" size="2" color="red" mt="2">
          {error}
        </Text>
      </>,
    );
  }

  return page(
    <>
      <Text size="5" weight="bold">
        {t('agentClaim.title')}
      </Text>
      <Text as="p" size="2" color="gray" mt="2">
        {t('oauthConsent.signedInAs', { display: context.user.email })}
      </Text>
      <Text as="p" size="2" color="amber" mt="2">
        {t('agentClaim.intro')}
      </Text>
      <Text as="p" size="2" color="gray" mt="3">
        {t('agentClaim.scopesHeading')}
      </Text>
      <Box mt="2">
        {asArray(context.scopes).map((scope) => (
          <Text as="p" size="2" key={scope.name}>
            {scope.name}
            {scope.description ? ` — ${scope.description}` : ''}
          </Text>
        ))}
      </Box>
      <TextField.Root
        mt="4"
        size="3"
        inputMode="numeric"
        maxLength={6}
        placeholder="123456"
        value={userCode}
        onChange={(e) => setUserCode(e.target.value.replace(/\D/g, ''))}
        aria-label={t('agentClaim.codeLabel')}
      />
      {error ? (
        <Text as="p" size="2" color="red" mt="2">
          {error}
        </Text>
      ) : null}
      <Flex gap="3" mt="4">
        <Button variant="soft" color="gray" disabled={submitting} onClick={() => decide('deny')}>
          {t('oauthConsent.deny')}
        </Button>
        <LoadingButton
          loading={submitting}
          disabled={userCode.length !== 6}
          onClick={() => decide('approve')}
        >
          {t('agentClaim.approve')}
        </LoadingButton>
      </Flex>
    </>,
  );
}

'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import axios from 'axios';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

import { apiClient } from '@/lib/api';
import { extractApiErrorMessage, processError } from '@/lib/api/api-error';
import { useAuthStore } from '@/config';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { LoadingButton } from '@/app/components/ui/loading-button';

const SLACK_LINK_PATH = '/slack/link';
const INSPECT_API = '/api/v1/configurationManager/slack-bot/identity-links/requests/inspect';
const DECIDE_API = '/api/v1/configurationManager/slack-bot/identity-links/requests/decide';

export interface SlackLinkContext {
  slack: { teamId: string; userId: string; name: string | null };
  botName: string | null;
  expiresAt: string;
  account: { email: string; fullName: string | null };
  alreadyLinkedToYou: boolean;
  linkedToAnotherAccount: boolean;
}

/** A link sent by another organisation's bot: said in the person's language (the server's words are the fallback). */
const OTHER_ORG_CODE = 'HTTP_SLACK_LINK_OTHER_ORG';

function errorMessageFromUnknown(err: unknown, t: (key: string) => string): string {
  if (axios.isAxiosError(err)) {
    const code = (err.response?.data as { error?: { code?: unknown } } | undefined)?.error?.code;
    if (code === OTHER_ORG_CODE) return t('slackLink.otherOrg');
    return extractApiErrorMessage(err.response?.data) || processError(err).message;
  }
  return err instanceof Error ? err.message : 'An error occurred';
}

/**
 * Where a Slack person, signed in to PipesHub, approves acting as their PipesHub account from Slack (ADR-SLACKID-01).
 * The page names both sides of the link before anything is bound.
 */
export function SlackLinkView() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const token = searchParams.get('token') ?? '';
  const [context, setContext] = useState<SlackLinkContext | null>(null);
  const [loading, setLoading] = useState(Boolean(token));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(token ? '' : t('slackLink.missingLink'));
  const [done, setDone] = useState<'approve' | 'deny' | null>(null);

  const returnToPath = useMemo(() => {
    const q = searchParams.toString();
    return q ? `${SLACK_LINK_PATH}?${q}` : SLACK_LINK_PATH;
  }, [searchParams]);

  useEffect(() => {
    if (isHydrated && !isAuthenticated) {
      router.replace(`/login?returnTo=${encodeURIComponent(returnToPath)}`);
    }
  }, [isHydrated, isAuthenticated, router, returnToPath]);

  useEffect(() => {
    if (!isHydrated || !isAuthenticated || !token) return;
    apiClient
      .post<SlackLinkContext>(INSPECT_API, { token }, { suppressErrorToast: true })
      .then(({ data }) => setContext(data))
      .catch((err) => setError(errorMessageFromUnknown(err, t)))
      .finally(() => setLoading(false));
  }, [isHydrated, isAuthenticated, token]);

  const decide = async (action: 'approve' | 'deny') => {
    setSubmitting(true);
    setError('');
    try {
      await apiClient.post(DECIDE_API, { token, action }, { suppressErrorToast: true });
      setDone(action);
    } catch (err) {
      setError(errorMessageFromUnknown(err, t));
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
          {done === 'approve' ? t('slackLink.doneTitle') : t('slackLink.deniedTitle')}
        </Text>
        <Text as="p" size="2" color="gray" mt="2">
          {done === 'approve' ? t('slackLink.doneLine') : t('slackLink.deniedLine')}
        </Text>
      </>,
    );
  }

  if (loading) {
    return page(
      <Flex align="center" direction="column" gap="3">
        <LottieLoader variant="loader" size={64} />
        <Text size="2" color="gray">
          {t('slackLink.loading')}
        </Text>
      </Flex>,
    );
  }

  if (!context) {
    return page(
      <>
        <Text size="5" weight="bold">
          {t('slackLink.title')}
        </Text>
        <Text as="p" size="2" color="red" mt="2" data-testid="slack-link-error">
          {error}
        </Text>
      </>,
    );
  }

  const slackPerson = context.slack.name
    ? `${context.slack.name} (${context.slack.userId})`
    : context.slack.userId;
  const account = context.account.fullName
    ? `${context.account.fullName} (${context.account.email})`
    : context.account.email;

  return page(
    <>
      <Text size="5" weight="bold">
        {t('slackLink.title')}
      </Text>
      <Text as="p" size="2" mt="3" data-testid="slack-link-summary">
        {t('slackLink.summary', { slackPerson, workspace: context.slack.teamId, account })}
      </Text>
      <Text as="p" size="2" color="gray" mt="2">
        {t('slackLink.effect')}
      </Text>
      {context.linkedToAnotherAccount ? (
        <Text as="p" size="2" color="amber" mt="2">
          {t('slackLink.replacesOther')}
        </Text>
      ) : null}
      <Text as="p" size="2" color="amber" mt="2">
        {t('slackLink.onlyIfYou')}
      </Text>
      {error ? (
        <Text as="p" size="2" color="red" mt="2">
          {error}
        </Text>
      ) : null}
      <Flex gap="3" mt="4">
        <Button variant="soft" color="gray" disabled={submitting} onClick={() => decide('deny')}>
          {t('slackLink.deny')}
        </Button>
        <LoadingButton loading={submitting} onClick={() => decide('approve')}>
          {t('slackLink.approve')}
        </LoadingButton>
      </Flex>
    </>,
  );
}

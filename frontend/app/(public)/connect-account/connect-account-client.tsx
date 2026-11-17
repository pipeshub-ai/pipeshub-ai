'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import NextLink from 'next/link';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Heading, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ConnectorsApi } from '@/app/(main)/workspace/connectors/api';
import type { UserAccessLinkInfo } from '@/app/(main)/workspace/connectors/types';

type View =
  | { kind: 'loading' }
  | { kind: 'ready'; info: UserAccessLinkInfo }
  | { kind: 'confirmOptOut'; info: UserAccessLinkInfo }
  | { kind: 'expired' }
  | { kind: 'optedOut' }
  | { kind: 'rateLimited' }
  | { kind: 'error' };

/** HTTP status of a failed public-client (plain axios) call. */
function statusOf(e: unknown): number | undefined {
  return (e as { response?: { status?: number } } | null)?.response?.status;
}

/** 404: unknown, expired or used; 400: not a token at all; 429: the per-address limit. */
function failedView(e: unknown, expiredOn400: boolean): View {
  const status = statusOf(e);
  if (status === 429) return { kind: 'rateLimited' };
  if (status === 404 || (expiredOn400 && status === 400)) return { kind: 'expired' };
  return { kind: 'error' };
}

const shellStyle = {
  minHeight: '100vh',
  width: '100%',
  padding: 'var(--space-5)',
  background: 'linear-gradient(180deg, var(--olive-2) 0%, var(--olive-1) 100%)',
} as const;

function OpenAppButton({ href }: { href: string }) {
  const { t } = useTranslation();
  return (
    <Button asChild size="2" variant="outline" color="gray" style={{ minHeight: 44 }}>
      <NextLink href={href}>{t('workspace.connectors.userAccess.link.openApp')}</NextLink>
    </Button>
  );
}

/**
 * Reads `?token=` and starts the source sign-in in this tab. With `&optOut=1` it asks first:
 * mail scanners open links, so loading the page must not turn reminders off.
 */
const LINK_TOKEN_KEY = 'pipeshub:connectLinkToken';
const LINK_ENTRY_KEY = 'pipeshubConnectLink';

export function ConnectAccountClient() {
  const { t } = useTranslation();
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    const params = new URLSearchParams(window.location.search);
    // The token works without a login: keep it out of the address bar and history, and in this
    // tab only, so a reload or Back from the source still finds it.
    // Each history entry keeps its own link (two links opened in one tab must not swap on reload).
    const state = (window.history.state ?? {}) as Record<string, unknown>;
    const entry = typeof state[LINK_ENTRY_KEY] === 'string' ? (state[LINK_ENTRY_KEY] as string) : String(Date.now());
    let linkToken = params.get('token')?.trim() ?? '';
    try {
      if (linkToken) sessionStorage.setItem(`${LINK_TOKEN_KEY}:${entry}`, linkToken);
      else linkToken = sessionStorage.getItem(`${LINK_TOKEN_KEY}:${entry}`) ?? '';
    } catch {
      // Storage blocked: the token only lives in this page.
    }
    if (params.has('token')) {
      params.delete('token');
      const rest = params.toString();
      window.history.replaceState(
        { ...state, [LINK_ENTRY_KEY]: entry },
        '',
        `${window.location.pathname}${rest ? `?${rest}` : ''}`
      );
    }
    setToken(linkToken);
    if (!linkToken) {
      setView({ kind: 'expired' });
      return;
    }
    const optOut = params.get('optOut') === '1';
    ConnectorsApi.getUserAccessLinkInfo(linkToken)
      .then((info) => setView(optOut ? { kind: 'confirmOptOut', info } : { kind: 'ready', info }))
      .catch((e) => setView(failedView(e, true)));
  }, []);

  // Back from the source restores this page from the back/forward cache with the button still spinning.
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setBusy(false);
    };
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, []);

  const proceed = async () => {
    setBusy(true);
    try {
      window.location.assign(await ConnectorsApi.authorizeUserAccessLink(token));
    } catch (e) {
      setBusy(false);
      setView(failedView(e, false));
    }
  };

  const optOut = async () => {
    setBusy(true);
    try {
      await ConnectorsApi.optOutUserAccessLink(token);
      setView({ kind: 'optedOut' });
    } catch (e) {
      setView(failedView(e, true));
    } finally {
      setBusy(false);
    }
  };

  let body: ReactNode;
  switch (view.kind) {
    case 'loading':
      body = (
        <Flex direction="column" align="center" gap="4">
          <Box style={{ width: 48, height: 48 }}>
            <LottieLoader variant="still" size={48} />
          </Box>
          <Text size="3" color="gray">
            {t('workspace.connectors.userAccess.link.loading')}
          </Text>
        </Flex>
      );
      break;
    case 'ready': {
      const { info } = view;
      const source = info.sourceType || info.connectorName;
      // Tokens are `<connectorId>.<secret>`; link/info does not return the id.
      const connectorId = token.split('.')[0];
      body = (
        <Flex direction="column" align="center" gap="4">
          <Heading as="h1" size="5" weight="medium">
            {t('workspace.connectors.userAccess.link.title', { source })}
          </Heading>
          <Text size="3" color="gray" style={{ overflowWrap: 'anywhere' }}>
            {t('workspace.connectors.userAccess.link.forEmail', { email: info.email })}
          </Text>
          {info.connected ? (
            <>
              <Text size="3">{t('workspace.connectors.userAccess.link.alreadyConnected', { source })}</Text>
              <OpenAppButton href="/" />
            </>
          ) : info.signInAvailable ? (
            <>
              <Text size="2" color="gray">
                {t('workspace.connectors.userAccess.connectDialog.signInHint', { source })}
              </Text>
              <LoadingButton size="3" style={{ minHeight: 44 }} loading={busy} onClick={() => void proceed()}>
                {t('workspace.connectors.userAccess.link.continue', { source })}
              </LoadingButton>
            </>
          ) : (
            <>
              <Text size="3">{t('workspace.connectors.userAccess.link.noSignIn')}</Text>
              <OpenAppButton href={`/workspace/connectors/personal/?connect=${encodeURIComponent(connectorId)}`} />
            </>
          )}
        </Flex>
      );
      break;
    }
    case 'confirmOptOut':
      body = (
        <Flex direction="column" align="center" gap="4">
          <Heading as="h1" size="5" weight="medium">
            {t('workspace.connectors.userAccess.link.confirmOptOut', { name: view.info.connectorName })}
          </Heading>
          <Text size="3" color="gray" style={{ overflowWrap: 'anywhere' }}>
            {t('workspace.connectors.userAccess.link.forEmail', { email: view.info.email })}
          </Text>
          <LoadingButton size="3" style={{ minHeight: 44 }} loading={busy} onClick={() => void optOut()}>
            {t('workspace.connectors.userAccess.link.stopReminders')}
          </LoadingButton>
        </Flex>
      );
      break;
    case 'optedOut':
      body = (
        <Flex direction="column" align="center" gap="4">
          <MaterialIcon name="notifications_off" size={24} color="var(--accent-11)" />
          <Text size="3">{t('workspace.connectors.userAccess.link.optedOut')}</Text>
          <OpenAppButton href="/" />
        </Flex>
      );
      break;
    case 'expired':
      body = (
        <Flex direction="column" align="center" gap="4">
          <MaterialIcon name="link_off" size={24} color="var(--gray-11)" />
          <Text size="3">{t('workspace.connectors.userAccess.link.expired')}</Text>
          <OpenAppButton href="/workspace/connectors/personal/" />
        </Flex>
      );
      break;
    default:
      body = (
        <Flex direction="column" align="center" gap="4">
          <MaterialIcon name="error_outline" size={24} color="var(--red-11)" />
          <Text size="3">
            {view.kind === 'rateLimited'
              ? t('workspace.connectors.userAccess.link.rateLimited')
              : t('workspace.connectors.userAccess.link.error')}
          </Text>
        </Flex>
      );
  }

  return (
    <Flex align="center" justify="center" style={shellStyle}>
      <Box style={{ width: '100%', maxWidth: 432, textAlign: 'center' }}>{body}</Box>
    </Flex>
  );
}

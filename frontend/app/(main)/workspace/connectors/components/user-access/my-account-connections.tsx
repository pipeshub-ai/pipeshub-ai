'use client';

import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Box, Button, Callout, Card, Dialog, Flex, Heading, Link, Text, TextField } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useToastStore } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { formatRelativeTime } from '@/lib/utils/formatters';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { CONNECTOR_OAUTH_POST_MESSAGE } from '@/app/(main)/connectors/oauth/connector-oauth-window-messages';
import { ConnectorsApi } from '../../api';
import type { UserAccessConnection, UserAccessConnector } from '../../types';

// shortcut: Notion is the only per-user connector type; add a link per type when another one arrives.
const TOKEN_HELP_URL: Record<string, string> = {
  Notion: 'https://developers.notion.com/guides/connections-and-tokens/personal-access-tokens',
};

// Not `noopener`: the callback page reports back through `window.opener`.
const SIGN_IN_POPUP_FEATURES = 'width=600,height=700,scrollbars=yes,resizable=yes';
const SIGN_IN_POPUP_POLL_MS = 1000;

/** "Connected · Last synced 2 hours ago", "Needs reconnecting: …" or "Not connected". */
export function UserAccessStatusText({ connection }: { connection: UserAccessConnection | null }) {
  const { t } = useTranslation();
  if (!connection) {
    return (
      <Text size="1" color="gray">
        {t('workspace.connectors.userAccess.notConnected')}
      </Text>
    );
  }
  if (connection.status === 'revoked') {
    return (
      <Text size="1" color="red">
        {t('workspace.connectors.userAccess.needsReconnect')}
        {connection.lastError ? `: ${connection.lastError}` : ''}
      </Text>
    );
  }
  const synced = connection.lastCompleteListingAt
    ? t('workspace.connectors.userAccess.lastSynced', {
        time: formatRelativeTime(connection.lastCompleteListingAt),
      })
    : t('workspace.connectors.userAccess.notSyncedYet');
  const account = connection.sourceEmail
    ? t('workspace.connectors.userAccess.connectedAs', { account: connection.sourceEmail })
    : t('workspace.connectors.userAccess.connected');
  return (
    <Text size="1" color="gray">
      {account} · {synced}
    </Text>
  );
}

function ConnectAccountDialog({
  connector,
  onClose,
  onConnected,
}: {
  connector: UserAccessConnector | null;
  onClose: () => void;
  onConnected: (connectorId: string, connection: UserAccessConnection | null) => void;
}) {
  const { t } = useTranslation();
  const tokenId = useId();
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  // With sign-in available, the token form is a fallback behind a disclosure.
  const [showToken, setShowToken] = useState(false);
  const popupRef = useRef<Window | null>(null);
  // Bumped by each sign-in and by closing the popup, so a late answer for an earlier one is ignored.
  const signInAttempt = useRef(0);

  const closePopup = useCallback(() => {
    signInAttempt.current += 1;
    const popup = popupRef.current;
    popupRef.current = null;
    if (popup && !popup.closed) popup.close();
  }, []);

  useEffect(() => {
    setToken('');
    setError(null);
    setSigningIn(false);
    setShowToken(false);
    closePopup();
  }, [connector?.connectorId, closePopup]);

  // The connector OAuth callback page completes the sign-in and reports back here.
  useEffect(() => {
    if (!signingIn || !connector) return;
    const connectorId = connector.connectorId;
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as { type?: string; error?: string; connectorId?: string | null } | null;
      // Another connector's OAuth popup in this window is not this sign-in.
      if (data?.connectorId && data.connectorId !== connectorId) return;
      if (data?.type === CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS) {
        setSigningIn(false);
        ConnectorsApi.getUserAccess(connectorId)
          .then((connection) => onConnected(connectorId, connection))
          .catch(() => setError(t('workspace.connectors.userAccess.connectDialog.signInFailed')));
      } else if (data?.type === CONNECTOR_OAUTH_POST_MESSAGE.ERROR) {
        setSigningIn(false);
        setError(data.error || t('workspace.connectors.userAccess.connectDialog.signInFailed'));
      }
    };
    window.addEventListener('message', onMessage);
    // Closed without finishing: stop waiting.
    const poll = setInterval(() => {
      if (!popupRef.current || popupRef.current.closed) setSigningIn(false);
    }, SIGN_IN_POPUP_POLL_MS);
    return () => {
      window.removeEventListener('message', onMessage);
      clearInterval(poll);
    };
  }, [signingIn, connector, onConnected, t]);

  const startSignIn = async () => {
    if (!connector || busy || signingIn) return;
    setError(null);
    // Opened on the click itself, or the browser blocks it; sent to Notion once the URL is known.
    const popup = window.open('', 'pipeshub-user-access-sign-in', SIGN_IN_POPUP_FEATURES);
    if (!popup) {
      setError(t('workspace.connectors.userAccess.connectDialog.popupBlocked'));
      return;
    }
    popupRef.current = popup;
    setSigningIn(true);
    const attempt = ++signInAttempt.current;
    try {
      const url = await ConnectorsApi.startUserAccessSignIn(connector.connectorId);
      // Cancelled (or another sign-in started) while the URL was on its way: that one owns the popup now.
      if (attempt !== signInAttempt.current) return;
      popup.location.href = url;
    } catch (err) {
      if (attempt !== signInAttempt.current) return;
      closePopup();
      setSigningIn(false);
      setError(getUserFacingErrorMessage(err, t('workspace.connectors.userAccess.connectDialog.signInFailed')));
    }
  };

  const close = () => {
    closePopup();
    onClose();
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!connector || !token.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const connection = await ConnectorsApi.connectUserAccess(connector.connectorId, token.trim());
      setToken(''); // the credential is not kept once it is stored
      onConnected(connector.connectorId, connection);
    } catch (err) {
      // 400 (wrong account or secret), 409 (account taken) and 503 (source unreachable) carry the reason.
      setError(getUserFacingErrorMessage(err, t('workspace.connectors.userAccess.connectDialog.errorFallback')));
    } finally {
      setBusy(false);
    }
  };

  const helpUrl = connector ? TOKEN_HELP_URL[connector.type] : undefined;
  const source = connector?.type ?? '';
  const signInAvailable = Boolean(connector?.signInAvailable);

  return (
    <Dialog.Root open={connector !== null} onOpenChange={(open) => !open && !busy && close()}>
      <Dialog.Content style={{ maxWidth: '30rem' }}>
        <Dialog.Title>
          {t('workspace.connectors.userAccess.connectDialog.title', { name: connector?.name ?? '' })}
        </Dialog.Title>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)' }}>
          {signInAvailable
            ? t('workspace.connectors.userAccess.connectDialog.descriptionSignIn', { source })
            : t('workspace.connectors.userAccess.connectDialog.description', { source })}
        </Dialog.Description>
        {signInAvailable ? (
          <Flex direction="column" gap="2" mt="4">
            <LoadingButton
              type="button"
              size="2"
              style={{ minHeight: 44 }}
              loading={signingIn}
              disabled={busy}
              // A deep link opens this dialog; the sign-in itself needs the user's click (popup blockers).
              autoFocus
              loadingLabel={t('workspace.connectors.userAccess.connectDialog.signingIn', { source })}
              onClick={() => void startSignIn()}
            >
              {t('workspace.connectors.userAccess.connectDialog.signIn', { source })}
            </LoadingButton>
            <Text size="1" color="gray">
              {t('workspace.connectors.userAccess.connectDialog.signInHint', { source })}
            </Text>
            {error && !showToken ? (
              <Callout.Root color="red" variant="surface" size="1" role="alert">
                <Callout.Text>{error}</Callout.Text>
              </Callout.Root>
            ) : null}
          </Flex>
        ) : null}
        {signInAvailable && !showToken ? (
          <Flex justify="between" align="center" gap="2" mt="4" wrap="wrap">
            <Button type="button" variant="ghost" size="2" onClick={() => setShowToken(true)}>
              {t('workspace.connectors.userAccess.connectDialog.useToken')}
            </Button>
            <Button type="button" variant="outline" color="gray" onClick={close}>
              {t('common.cancel')}
            </Button>
          </Flex>
        ) : (
        <>
        {helpUrl ? (
          <Box mt="2">
            <Link href={helpUrl} target="_blank" rel="noopener noreferrer" size="2">
              {t('workspace.connectors.userAccess.connectDialog.howTo')}
            </Link>
          </Box>
        ) : null}
        <form onSubmit={(e) => void submit(e)}>
          <Flex direction="column" gap="2" mt="4">
            <Text as="label" htmlFor={tokenId} size="2" weight="medium">
              {t('workspace.connectors.userAccess.connectDialog.tokenLabel')}
            </Text>
            <TextField.Root
              id={tokenId}
              size="2"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              disabled={busy}
              autoFocus
            />
            {error ? (
              <Callout.Root color="red" variant="surface" size="1" role="alert">
                <Callout.Text>{error}</Callout.Text>
              </Callout.Root>
            ) : null}
          </Flex>
          <Flex justify="end" gap="2" mt="4">
            <Button type="button" variant="outline" color="gray" disabled={busy} onClick={close}>
              {t('common.cancel')}
            </Button>
            <LoadingButton
              type="submit"
              loading={busy}
              disabled={!token.trim() || signingIn}
              loadingLabel={t('workspace.connectors.userAccess.connectDialog.submitting')}
            >
              {t('workspace.connectors.userAccess.connect')}
            </LoadingButton>
          </Flex>
        </form>
        </>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}

/**
 * Team connectors an admin set to per-user access: each person connects their own source
 * account and sees only what it can open. Hidden when there are none.
 */
export function MyAccountConnections() {
  const { t } = useTranslation();
  const headingId = useId();
  const addToast = useToastStore((s) => s.addToast);
  const [connectors, setConnectors] = useState<UserAccessConnector[]>([]);
  const [connecting, setConnecting] = useState<UserAccessConnector | null>(null);
  const [disconnecting, setDisconnecting] = useState<UserAccessConnector | null>(null);
  const [disconnectBusy, setDisconnectBusy] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const deepLinkId = searchParams?.get('connect') ?? null;
  const handledDeepLink = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await ConnectorsApi.getMyUserAccessConnectors();
        if (!cancelled) setConnectors(list);
      } catch {
        // The interceptor already reported anything other than a missing endpoint.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // `?connect=<id>` (from a notification): open that connector's dialog once, then drop the param so a refresh doesn't.
  useEffect(() => {
    if (!deepLinkId) {
      // Gone from the URL: the same notification clicked again opens the dialog again.
      handledDeepLink.current = null;
      return;
    }
    if (connectors.length === 0 || handledDeepLink.current === deepLinkId) return;
    handledDeepLink.current = deepLinkId;
    const target = connectors.find((c) => c.connectorId === deepLinkId);
    if (target && (!target.connection || target.connection.status === 'revoked')) setConnecting(target);
    const params = new URLSearchParams(searchParams?.toString() ?? '');
    params.delete('connect');
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname);
  }, [deepLinkId, connectors, searchParams, pathname, router]);

  const setConnection = useCallback((connectorId: string, connection: UserAccessConnection | null) => {
    setConnectors((list) =>
      list.map((c) => (c.connectorId === connectorId ? { ...c, connected: connection !== null, connection } : c))
    );
  }, []);

  const handleConnected = useCallback(
    (connectorId: string, connection: UserAccessConnection | null) => {
      setConnection(connectorId, connection);
      setConnecting(null);
      addToast({
        variant: 'success',
        title: t('workspace.connectors.userAccess.toasts.connected'),
        description: t('workspace.connectors.userAccess.toasts.connectedDescription'),
      });
    },
    [setConnection, addToast, t]
  );

  const handleOptOutChange = useCallback(
    async (connectorId: string, optedOut: boolean) => {
      try {
        const saved = await ConnectorsApi.setUserAccessReminders(connectorId, optedOut);
        setConnectors((list) => list.map((c) => (c.connectorId === connectorId ? { ...c, optedOut: saved } : c)));
        addToast({
          variant: 'success',
          title: saved
            ? t('workspace.connectors.userAccess.toasts.remindersOff')
            : t('workspace.connectors.userAccess.toasts.remindersOn'),
        });
      } catch {
        // The interceptor shows the server's reason.
      }
    },
    [addToast, t]
  );

  const handleDisconnect = useCallback(async () => {
    if (!disconnecting) return;
    setDisconnectBusy(true);
    try {
      await ConnectorsApi.disconnectUserAccess(disconnecting.connectorId);
      setConnection(disconnecting.connectorId, null);
      setDisconnecting(null);
      addToast({ variant: 'success', title: t('workspace.connectors.userAccess.toasts.disconnected') });
    } catch {
      // The interceptor shows the server's reason.
    } finally {
      setDisconnectBusy(false);
    }
  }, [disconnecting, setConnection, addToast, t]);

  if (connectors.length === 0) return null;

  return (
    <Box asChild>
      <section aria-labelledby={headingId}>
        <Flex direction="column" gap="3">
          <Flex direction="column" gap="1">
            <Heading as="h2" id={headingId} size="3" weight="medium">
              {t('workspace.connectors.userAccess.title')}
            </Heading>
            <Text size="2" color="gray">
              {t('workspace.connectors.userAccess.subtitle')}
            </Text>
          </Flex>
          {connectors.map((c) => {
            const revoked = c.connection?.status === 'revoked';
            return (
              <Card key={c.connectorId} size="2">
                <Flex align="center" justify="between" gap="3" wrap="wrap">
                  <Flex align="center" gap="3" style={{ minWidth: 0, flex: '1 1 240px' }}>
                    <ConnectorIcon type={c.type} size={24} />
                    <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                      <Text size="2" weight="medium">
                        {c.name}
                      </Text>
                      <UserAccessStatusText connection={c.connection} />
                      {!c.connection || revoked ? (
                        <Link asChild size="1" color="gray" style={{ alignSelf: 'flex-start' }}>
                          <button
                            type="button"
                            style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
                            onClick={() => void handleOptOutChange(c.connectorId, !c.optedOut)}
                          >
                            {c.optedOut
                              ? t('workspace.connectors.userAccess.remindAgain')
                              : t('workspace.connectors.userAccess.dontRemind')}
                          </button>
                        </Link>
                      ) : null}
                    </Flex>
                  </Flex>
                  <Flex gap="2">
                    {!c.connection || revoked ? (
                      <Button size="2" style={{ minHeight: 44 }} onClick={() => setConnecting(c)}>
                        {revoked
                          ? t('workspace.connectors.userAccess.reconnect')
                          : t('workspace.connectors.userAccess.connect')}
                      </Button>
                    ) : null}
                    {c.connection ? (
                      <Button
                        size="2"
                        variant="soft"
                        color="red"
                        style={{ minHeight: 44 }}
                        onClick={() => setDisconnecting(c)}
                      >
                        {t('workspace.connectors.userAccess.disconnect')}
                      </Button>
                    ) : null}
                  </Flex>
                </Flex>
              </Card>
            );
          })}
        </Flex>
        <ConnectAccountDialog
          connector={connecting}
          onClose={() => setConnecting(null)}
          onConnected={handleConnected}
        />
        <ConfirmationDialog
          open={disconnecting !== null}
          onOpenChange={(open) => !open && !disconnectBusy && setDisconnecting(null)}
          title={t('workspace.connectors.userAccess.disconnectDialog.title')}
          message={t('workspace.connectors.userAccess.disconnectDialog.message', {
            name: disconnecting?.name ?? '',
          })}
          confirmLabel={t('workspace.connectors.userAccess.disconnect')}
          confirmLoadingLabel={t('workspace.connectors.userAccess.disconnectDialog.confirming')}
          isLoading={disconnectBusy}
          onConfirm={() => void handleDisconnect()}
        />
      </section>
    </Box>
  );
}

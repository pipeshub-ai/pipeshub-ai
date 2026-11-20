'use client';

import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Callout, Dialog, Flex, Link, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { SecretTextField } from '@/app/components/ui/secret-text-field';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { CONNECTOR_OAUTH_POST_MESSAGE } from '@/app/(main)/connectors/oauth/connector-oauth-window-messages';
import { ConnectorsApi } from '../../api';
import type { UserAccessConnector } from '../../types';

// shortcut: Notion is the only per-user connector type; add a link per type when another one arrives.
const TOKEN_HELP_URL: Record<string, string> = {
  Notion: 'https://developers.notion.com/guides/connections-and-tokens/personal-access-tokens',
};

// Not `noopener`: the callback page reports back through `window.opener`.
const SIGN_IN_POPUP_FEATURES = 'width=600,height=700,scrollbars=yes,resizable=yes';
const SIGN_IN_POPUP_POLL_MS = 1000;

// Sent on window after a connect, so every button for that connector (one per reminder) shows Connected.
const CONNECTED_EVENT = 'pipeshub:user-access-connected';

/** Marks the token dialog so a host panel can keep itself open (and below it) while it shows. */
export const CONNECT_DIALOG_ATTR = 'data-ph-connect-account-dialog';

function TokenDialog({
  connector,
  onClose,
  onConnected,
}: {
  connector: UserAccessConnector | null;
  onClose: () => void;
  onConnected: () => void;
}) {
  const { t } = useTranslation();
  const tokenId = useId();
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setToken('');
    setError(null);
  }, [connector?.connectorId]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!connector || !token.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await ConnectorsApi.connectUserAccess(connector.connectorId, token.trim());
      setToken(''); // the credential is not kept once it is stored
      onConnected();
    } catch (err) {
      // 400 (wrong account or secret), 409 (account taken) and 503 (source unreachable) carry the reason.
      setError(getUserFacingErrorMessage(err, t('workspace.connectors.userAccess.connectDialog.errorFallback')));
    } finally {
      setBusy(false);
    }
  };

  const helpUrl = connector ? TOKEN_HELP_URL[connector.type] : undefined;
  return (
    <Dialog.Root open={connector !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <Dialog.Content style={{ maxWidth: '30rem' }} {...{ [CONNECT_DIALOG_ATTR]: '' }}>
        <Dialog.Title>
          {t('workspace.connectors.userAccess.connectDialog.title', { name: connector?.name ?? '' })}
        </Dialog.Title>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)' }}>
          {t('workspace.connectors.userAccess.connectDialog.description', { source: connector?.type ?? '' })}
        </Dialog.Description>
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
            <SecretTextField
              id={tokenId}
              size="2"
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
            <Button type="button" variant="outline" color="gray" disabled={busy} onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <LoadingButton
              type="submit"
              loading={busy}
              disabled={!token.trim()}
              loadingLabel={t('workspace.connectors.userAccess.connectDialog.submitting')}
            >
              {t('workspace.connectors.userAccess.connect')}
            </LoadingButton>
          </Flex>
        </form>
      </Dialog.Content>
    </Dialog.Root>
  );
}

/**
 * The caller's own connection to one per-user team connector, as a single control: "Connected"
 * once connected, else a Connect (or Reconnect) button. Connect signs in with the source in a
 * popup when the connector has an OAuth app, or asks for a token in a dialog. Renders nothing
 * while loading, or when the connector is gone or no longer per-user.
 */
export function ConnectAccountAction({
  connectorId,
  onConnected,
}: {
  connectorId: string;
  /** After a successful connect (e.g. to mark the notification read). */
  onConnected?: () => void;
}) {
  const { t } = useTranslation();
  // undefined: loading; null: not a per-user connector the caller can connect to.
  const [connector, setConnector] = useState<UserAccessConnector | null | undefined>(undefined);
  const [tokenFor, setTokenFor] = useState<UserAccessConnector | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const popupRef = useRef<Window | null>(null);
  // Bumped by each sign-in, so a sign-in URL that arrives after a newer start (or a failure) is dropped.
  const signInAttempt = useRef(0);

  const load = useCallback(async () => {
    try {
      const list = await ConnectorsApi.getMyUserAccessConnectors({ silent: true });
      setConnector(list.find((c) => c.connectorId === connectorId) ?? null);
    } catch {
      setConnector(null);
    }
  }, [connectorId]);

  useEffect(() => {
    void load();
    const onOther = (e: Event) => {
      if ((e as CustomEvent<string>).detail === connectorId) void load();
    };
    window.addEventListener(CONNECTED_EVENT, onOther);
    return () => window.removeEventListener(CONNECTED_EVENT, onOther);
  }, [load, connectorId]);

  const connected = useCallback(async () => {
    setTokenFor(null);
    setError(null);
    window.dispatchEvent(new CustomEvent(CONNECTED_EVENT, { detail: connectorId }));
    onConnected?.();
  }, [connectorId, onConnected]);

  // The connector OAuth callback page completes the sign-in and reports back here.
  useEffect(() => {
    if (!signingIn) return;
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as { type?: string; error?: string; connectorId?: string | null } | null;
      // Another connector's OAuth popup in this window is not this sign-in.
      if (data?.connectorId && data.connectorId !== connectorId) return;
      if (data?.type === CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS) {
        setSigningIn(false);
        void connected();
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
  }, [signingIn, connectorId, connected, t]);

  const start = async () => {
    if (!connector || signingIn) return;
    setError(null);
    if (!connector.signInAvailable) {
      setTokenFor(connector);
      return;
    }
    // Opened on the click itself, or the browser blocks it; sent to the source once the URL is known.
    const popup = window.open('', 'pipeshub-user-access-sign-in', SIGN_IN_POPUP_FEATURES);
    if (!popup) {
      setError(t('workspace.connectors.userAccess.connectDialog.popupBlocked'));
      return;
    }
    popupRef.current = popup;
    const attempt = ++signInAttempt.current;
    setSigningIn(true);
    try {
      const url = await ConnectorsApi.startUserAccessSignIn(connector.connectorId);
      if (attempt !== signInAttempt.current) return; // closed while the URL was on its way
      popup.location.href = url;
    } catch (err) {
      if (attempt !== signInAttempt.current) return;
      signInAttempt.current += 1;
      popup.close();
      setSigningIn(false);
      setError(getUserFacingErrorMessage(err, t('workspace.connectors.userAccess.connectDialog.signInFailed')));
    }
  };

  if (!connector) return null;

  if (connector.connection && connector.connection.status !== 'revoked') {
    return (
      <Flex align="center" gap="1" role="status">
        <MaterialIcon name="check_circle" size={16} color="var(--jade-11)" />
        <Text size="1" weight="medium" style={{ color: 'var(--jade-11)' }}>
          {connector.connection.sourceEmail
            ? t('workspace.connectors.userAccess.connectedAs', { account: connector.connection.sourceEmail })
            : t('workspace.connectors.userAccess.connected')}
        </Text>
      </Flex>
    );
  }

  const source = connector.type;
  return (
    <Flex direction="column" gap="1" align="start">
      <LoadingButton
        size="1"
        loading={signingIn}
        loadingLabel={t('workspace.connectors.userAccess.connectDialog.signingIn', { source })}
        onClick={() => void start()}
      >
        {connector.connection
          ? t('workspace.connectors.userAccess.reconnect')
          : t('workspace.connectors.userAccess.connect')}
      </LoadingButton>
      {error ? (
        <Text size="1" color="red" role="alert">
          {error}
        </Text>
      ) : null}
      <TokenDialog connector={tokenFor} onClose={() => setTokenFor(null)} onConnected={() => void connected()} />
    </Flex>
  );
}

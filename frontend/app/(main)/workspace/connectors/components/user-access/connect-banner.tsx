'use client';

import React, { useEffect, useState } from 'react';
import NextLink from 'next/link';
import { useTranslation } from 'react-i18next';
import { Button, Flex, IconButton, Link, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { onSessionEnd } from '@/lib/api/session-cache';
import { ConnectorsApi } from '../../api';
import type { UserAccessConnector } from '../../types';

const DISMISSED_KEY = 'pipeshub:userAccessBannerDismissed';
/** Coming back to the tab re-reads the list at most this often (the user may have connected elsewhere). */
const REFRESH_ON_FOCUS_MS = 60_000;

// One request shared by search and chat. Never rejects: the banner is optional.
let myConnectors: Promise<UserAccessConnector[]> | null = null;
let fetchedAt = 0;

function loadMyConnectors(): Promise<UserAccessConnector[]> {
  if (!myConnectors) {
    fetchedAt = Date.now();
    myConnectors = ConnectorsApi.getMyUserAccessConnectors({ silent: true }).catch(() => []);
  }
  return myConnectors;
}

/** Drop the cached list after the user connects, disconnects or changes reminders. */
export function forgetMyUserAccessConnectors(): void {
  myConnectors = null;
}

// The next person to sign in on this tab must not see this one's list.
onSessionEnd(forgetMyUserAccessConnectors);

function isDismissed(): boolean {
  try {
    return sessionStorage.getItem(DISMISSED_KEY) === '1';
  } catch {
    return false;
  }
}

const needsConnecting = (list: UserAccessConnector[]) =>
  list.find((c) => !c.optedOut && (!c.connection || c.connection.status === 'revoked')) ?? null;

/**
 * Slim notice on search and chat when a per-user team connector is missing for this user
 * (never connected, or the source revoked the connection), with a link that opens its connect dialog.
 * Dismissal lasts for the browser session; "Don't remind me" turns it off for good.
 */
export function ConnectAccountBanner({ style }: { style?: React.CSSProperties }) {
  const { t } = useTranslation();
  const [target, setTarget] = useState<UserAccessConnector | null>(null);

  useEffect(() => {
    if (isDismissed()) return;
    let cancelled = false;
    const apply = () =>
      void loadMyConnectors().then((list) => {
        if (!cancelled && !isDismissed()) setTarget(needsConnecting(list));
      });
    const onFocus = () => {
      if (Date.now() - fetchedAt < REFRESH_ON_FOCUS_MS) return;
      forgetMyUserAccessConnectors();
      apply();
    };
    apply();
    window.addEventListener('focus', onFocus);
    return () => {
      cancelled = true;
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  if (!target) return null;

  const revoked = target.connection?.status === 'revoked';
  const source = target.type || target.name;
  const dismiss = () => {
    try {
      sessionStorage.setItem(DISMISSED_KEY, '1');
    } catch {
      // Storage blocked: still hide it for this page.
    }
    setTarget(null);
  };
  const stopReminders = () => {
    setTarget(null);
    forgetMyUserAccessConnectors();
    // The interceptor reports a failure; the banner stays hidden for this page either way.
    void ConnectorsApi.setUserAccessReminders(target.connectorId, true).catch(() => {});
  };

  return (
    <Flex
      role="status"
      align="center"
      justify="between"
      gap="2"
      style={{
        width: '100%',
        padding: '0 var(--space-1) 0 var(--space-3)',
        marginBottom: 'var(--space-3)',
        borderRadius: 'var(--radius-2)',
        background: 'var(--accent-a2)',
        ...style,
      }}
    >
      <Flex align="center" gap="2" wrap="wrap" style={{ minWidth: 0 }}>
        <MaterialIcon name="info" size={16} color="var(--accent-11)" />
        <Text size="2">
          {revoked
            ? t('workspace.connectors.userAccess.banner.revoked', { source })
            : t('workspace.connectors.userAccess.banner.notConnected', { source })}
        </Text>
        <Link asChild size="2" weight="medium">
          <NextLink href={`/workspace/connectors/personal/?connect=${encodeURIComponent(target.connectorId)}`}>
            {revoked
              ? t('workspace.connectors.userAccess.banner.reconnect')
              : t('workspace.connectors.userAccess.banner.connect', { source })}
          </NextLink>
        </Link>
      </Flex>
      <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
        <Button variant="ghost" color="gray" size="1" style={{ minHeight: 44 }} onClick={stopReminders}>
          {t('workspace.connectors.userAccess.dontRemind')}
        </Button>
        <IconButton
          variant="ghost"
          color="gray"
          aria-label={t('workspace.connectors.userAccess.banner.dismiss')}
          style={{ minWidth: 44, minHeight: 44 }}
          onClick={dismiss}
        >
          <MaterialIcon name="close" size={16} />
        </IconButton>
      </Flex>
    </Flex>
  );
}

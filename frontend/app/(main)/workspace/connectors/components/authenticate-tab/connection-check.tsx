'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useToastStore } from '@/lib/store/toast-store';
import { ConnectorsApi } from '../../api';

/** The IPs a user adds to their database's firewall so the connection check (and sync) can get through. */
export function EgressIpNotice({ connectorName }: { connectorName: string }) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [ips, setIps] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    ConnectorsApi.getEgressIps()
      .then((found) => {
        if (!cancelled) setIps(found);
      })
      .catch(() => {
        // Without the address the form still works; the notice just stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (ips.length === 0) return null;

  const copy = async (ip: string) => {
    try {
      await navigator.clipboard.writeText(ip);
      addToast({
        variant: 'success',
        title: t('workspace.connectors.authTab.connectionCheck.ipCopied'),
        duration: 2500,
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.connectors.authTab.connectionCheck.copyFailed'),
        description: t('workspace.connectors.authTab.connectionCheck.copyFailedDescription'),
        duration: 4000,
      });
    }
  };

  return (
    <Flex
      direction="column"
      gap="2"
      style={{
        padding: 16,
        backgroundColor: 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-3)',
      }}
    >
      <Flex align="center" gap="2">
        <MaterialIcon name="shield" size={18} color="var(--accent-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.connectors.authTab.connectionCheck.egressTitle')}
        </Text>
      </Flex>
      <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
        {t('workspace.connectors.authTab.connectionCheck.egressDescription', { name: connectorName })}
      </Text>
      <Flex gap="2" wrap="wrap">
        {ips.map((ip) => (
          <Flex
            key={ip}
            align="center"
            style={{
              border: '1px solid var(--olive-4)',
              borderRadius: 'var(--radius-2)',
              background: 'var(--color-surface)',
              paddingRight: 4,
            }}
          >
            <Box
              asChild
              style={{
                padding: '6px 10px',
                fontSize: 12,
                fontFamily: 'var(--code-font-family, ui-monospace, monospace)',
                color: 'var(--gray-12)',
              }}
            >
              <code data-ph-egress-ip>{ip}</code>
            </Box>
            <Tooltip content={t('workspace.connectors.authTab.connectionCheck.copyIp')}>
              <IconButton
                type="button"
                size="1"
                variant="ghost"
                color="gray"
                radius="full"
                style={{ cursor: 'pointer' }}
                aria-label={t('workspace.connectors.authTab.connectionCheck.copyIp')}
                onClick={() => void copy(ip)}
              >
                <MaterialIcon name="content_copy" size={16} color="var(--gray-11)" />
              </IconButton>
            </Tooltip>
          </Flex>
        ))}
      </Flex>
    </Flex>
  );
}

/** Why the last connection check failed; the Next button stays put until a check passes. */
export function ConnectionCheckError({
  connectorName,
  message,
}: {
  connectorName: string;
  message: string;
}) {
  const { t } = useTranslation();
  return (
    <Flex
      data-ph-connection-check-error
      role="alert"
      align="start"
      gap="3"
      style={{
        padding: 'var(--space-3) var(--space-4)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--red-a6)',
        backgroundColor: 'var(--red-a2)',
      }}
    >
      <MaterialIcon name="error" size={18} color="var(--red-11)" style={{ flexShrink: 0, marginTop: 1 }} />
      <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--red-11)' }}>
          {t('workspace.connectors.authTab.connectionCheck.failedTitle', { name: connectorName })}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-12)', lineHeight: 1.55, overflowWrap: 'anywhere' }}>
          {message}
        </Text>
      </Flex>
    </Flex>
  );
}

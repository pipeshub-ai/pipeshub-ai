'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** The server sends codes, never sentences, so every locale words them. */
export interface AccessNotice {
  code: 'permissions_updating' | 'connector_syncing' | 'check_unavailable';
  connectorId?: string;
  connectorName?: string;
  /** On `connector_syncing`: it has never finished a sync, so there is nothing to search yet (a resync is not that). */
  firstSync?: boolean;
}

export function readAccessNotices(value: unknown): AccessNotice[] {
  return Array.isArray(value) ? value.filter((n) => n && typeof n.code === 'string') : [];
}

/** Polite status lines under search results or an answer: something may be incomplete, and what to do. */
export function AccessNotices({ notices }: { notices?: AccessNotice[] }) {
  const { t } = useTranslation();
  if (!notices?.length) return null;
  return (
    <Flex direction="column" gap="1" role="status" data-testid="access-notices" style={{ marginBottom: 'var(--space-3)', color: 'var(--slate-11)' }}>
      {notices.map((n) => (
        <Flex key={`${n.code}-${n.connectorId ?? ''}`} align="start" gap="1">
          <MaterialIcon name="info" size={14} color="var(--slate-11)" />
          <Text size="1">
            {t(`accessNotice.${n.code}${n.code === 'connector_syncing' && !n.connectorName ? '_unnamed' : ''}`, { name: n.connectorName })}
          </Text>
        </Flex>
      ))}
    </Flex>
  );
}

'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { AccountsError } from '../types';

export type Load<T> =
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; error: AccountsError };

/** The sentence for a failed read: "try again" for an outage, never "nothing here". */
export function errorCopyKey(error: AccountsError): string {
  switch (error.kind) {
    case 'unavailable':
      return 'aiAccounts.common.unavailable';
    case 'forbidden':
      return 'aiAccounts.common.adminOnly';
    case 'not_found':
      return 'aiAccounts.common.notFound';
    case 'conflict':
      return 'aiAccounts.common.conflict';
    case 'invalid':
    case 'failed':
      return 'aiAccounts.common.failed';
  }
}

/** Loading and failure states of one section; `children` renders only when ready. */
export function LoadState<T>({
  load,
  unavailableKey,
  onRetry,
  children,
}: {
  load: Load<T>;
  /** The section's own "can't be reached" sentence. */
  unavailableKey?: string;
  onRetry: () => void;
  children: (data: T) => React.ReactNode;
}) {
  const { t } = useTranslation();
  if (load.status === 'loading') {
    return (
      <Text size="2" role="status" style={{ color: 'var(--slate-11)' }}>
        {t('aiAccounts.common.loading')}
      </Text>
    );
  }
  if (load.status === 'error') {
    const key =
      load.error.kind === 'unavailable' && unavailableKey ? unavailableKey : errorCopyKey(load.error);
    return (
      <Flex direction="column" gap="2" align="start" role="alert">
        <Flex align="center" gap="2">
          <MaterialIcon name="error_outline" size={16} color="var(--red-11)" />
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {t(key)}
          </Text>
        </Flex>
        {load.error.kind !== 'forbidden' && (
          <Button type="button" size="1" variant="soft" onClick={onRetry}>
            {t('aiAccounts.common.tryAgain')}
          </Button>
        )}
      </Flex>
    );
  }
  return <>{children(load.data)}</>;
}

/** A date as the reader's locale writes it. */
export function formatAccountDate(iso: string, language: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat(language, { dateStyle: 'medium' }).format(date);
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

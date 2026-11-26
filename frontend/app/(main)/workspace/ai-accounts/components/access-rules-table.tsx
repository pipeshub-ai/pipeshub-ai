'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Table, Text } from '@radix-ui/themes';
import { accountKindKey, providerName, reasonKey, rowAvailability } from '../access-copy';
import type { AccessRule } from '../types';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

// Radix's Table.Root puts its props on a wrapper, so the table is named by its caption.
const VISUALLY_HIDDEN: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
};

export interface AccessRulesTableProps {
  rules: AccessRule[];
  label: string;
  /** The admin page's per-row switch; absent on the user's page. */
  control?: (rule: AccessRule) => React.ReactNode;
  controlHeader?: string;
}

/** The release's vendor rows as they apply to this org: available, or the one reason they are not. */
export function AccessRulesTable({ rules, label, control, controlHeader }: AccessRulesTableProps) {
  const { t } = useTranslation();
  // A phone has room for three columns: the account and its engines fold under the provider, so the row's switch
  // stays on screen instead of behind a sideways scroll.
  const isMobile = useIsMobile();
  const account = (rule: AccessRule) => (
    <Flex direction="column" gap="1">
      <Text size="2">{t(accountKindKey(rule.accountKind))}</Text>
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t(`aiAccounts.routes.${rule.route}`, { defaultValue: rule.route })}
        {rule.consumer ? ` · ${t('aiAccounts.table.consumer')}` : ''}
      </Text>
    </Flex>
  );
  const engines = (rule: AccessRule) => (rule.engines === null ? t('aiAccounts.table.allEngines') : rule.engines.join(', '));
  return (
    <Table.Root size="1" variant="ghost">
      <caption style={VISUALLY_HIDDEN}>{label}</caption>
      <Table.Header>
        <Table.Row>
          <Table.ColumnHeaderCell>{t('aiAccounts.table.provider')}</Table.ColumnHeaderCell>
          {!isMobile && <Table.ColumnHeaderCell>{t('aiAccounts.table.account')}</Table.ColumnHeaderCell>}
          {!isMobile && <Table.ColumnHeaderCell>{t('aiAccounts.table.engines')}</Table.ColumnHeaderCell>}
          <Table.ColumnHeaderCell>{t('aiAccounts.table.status')}</Table.ColumnHeaderCell>
          {control && <Table.ColumnHeaderCell>{controlHeader}</Table.ColumnHeaderCell>}
        </Table.Row>
      </Table.Header>
      <Table.Body>
        {rules.map((rule) => {
          const reason = rowAvailability(rule);
          return (
            <Table.Row key={rule.id} data-rule-id={rule.id}>
              {isMobile ? (
                <Table.RowHeaderCell>
                  <Flex direction="column" gap="1">
                    <Text size="2" weight="medium">{providerName(rule.provider)}</Text>
                    {account(rule)}
                    <Text size="1" style={{ color: 'var(--slate-11)' }}>
                      {t('aiAccounts.table.engines')}: {engines(rule)}
                    </Text>
                  </Flex>
                </Table.RowHeaderCell>
              ) : (
                <>
                  <Table.RowHeaderCell>{providerName(rule.provider)}</Table.RowHeaderCell>
                  <Table.Cell>{account(rule)}</Table.Cell>
                  <Table.Cell>
                    <Text size="2">{engines(rule)}</Text>
                  </Table.Cell>
                </>
              )}
              <Table.Cell>
                {reason === 'available' ? (
                  <Badge color="green" variant="soft" highContrast>
                    {t('aiAccounts.reasons.available')}
                  </Badge>
                ) : (
                  <Flex direction="column" gap="1" align="start">
                    <Badge color="gray" variant="soft" highContrast>
                      {t('aiAccounts.table.notAvailable')}
                    </Badge>
                    <Text size="1" style={{ color: 'var(--slate-11)' }}>
                      {t(reasonKey(reason))}
                    </Text>
                  </Flex>
                )}
              </Table.Cell>
              {control && <Table.Cell>{control(rule)}</Table.Cell>}
            </Table.Row>
          );
        })}
      </Table.Body>
    </Table.Root>
  );
}

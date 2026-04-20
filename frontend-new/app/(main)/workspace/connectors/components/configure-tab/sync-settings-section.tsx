'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Select } from '@radix-ui/themes';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { STRATEGY_LABELS, INTERVAL_OPTIONS } from '../../constants';
import type { SyncStrategy } from '../../types';

// ========================================
// SyncSettingsSection
// ========================================

export function SyncSettingsSection({
  supportedStrategies,
  selectedStrategy,
  intervalMinutes,
  connectorName,
  onStrategyChange,
  onIntervalChange,
}: {
  supportedStrategies: SyncStrategy[];
  selectedStrategy: SyncStrategy;
  intervalMinutes?: number;
  connectorName: string;
  onStrategyChange: (strategy: SyncStrategy) => void;
  onIntervalChange: (minutes: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      gap="4"
      style={{
        padding: 16,
        backgroundColor: 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-3)',
      }}
    >
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.connectors.configTab.syncSettings')}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t('workspace.connectors.configTab.syncSettingsDescription', { name: connectorName })}
        </Text>
      </Flex>

      {/* Sync Strategy */}
      <FormField label={t('workspace.connectors.configTab.syncStrategy')}>
        <Select.Root
          value={selectedStrategy}
          onValueChange={(v) => onStrategyChange(v as SyncStrategy)}
        >
          <Select.Trigger
            style={{ width: '100%', height: 32 }}
            placeholder={t('workspace.connectors.configTab.syncStrategyPlaceholder')}
          />
          <Select.Content>
            {supportedStrategies.map((strategy) => (
              <Select.Item key={strategy} value={strategy}>
                {STRATEGY_LABELS[strategy] ?? strategy}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
        <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
          {t('workspace.connectors.configTab.syncStrategyHelper', { name: connectorName })}
        </Text>
      </FormField>

      {/* Sync Interval (only when SCHEDULED) */}
      {selectedStrategy === 'SCHEDULED' && (
        <FormField label={t('workspace.connectors.configTab.syncInterval')}>
          <Select.Root
            value={String(intervalMinutes ?? 60)}
            onValueChange={(v) => onIntervalChange(Number(v))}
          >
            <Select.Trigger
              style={{ width: '100%', height: 32 }}
              placeholder={t('workspace.connectors.configTab.syncIntervalPlaceholder')}
            />
            <Select.Content>
              {INTERVAL_OPTIONS.map((opt) => (
                <Select.Item key={opt.value} value={String(opt.value)}>
                  {opt.label}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
          <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
            {t('workspace.connectors.configTab.syncIntervalHelper', { name: connectorName })}
          </Text>
        </FormField>
      )}
    </Flex>
  );
}

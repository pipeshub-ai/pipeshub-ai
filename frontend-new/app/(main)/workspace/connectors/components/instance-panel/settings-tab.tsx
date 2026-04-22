'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Avatar, Box, Button, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getSyncStrategyLabel, getSyncIntervalLabel } from '../instance-card/utils';
import type { ConnectorInstance, ConnectorConfig } from '../../types';

// ========================================
// Props
// ========================================

interface SettingsTabProps {
  instance: ConnectorInstance;
  /** Config data from GET /connectors/{id}/config */
  config?: ConnectorConfig | null;
  /** Opens remove confirmation (parent owns the dialog). */
  onRequestRemoveConnector?: () => void;
  removeDisabled?: boolean;
  removeDisabledTooltip?: string;
}

// ========================================
// SettingsTab
// ========================================

export function SettingsTab({
  instance,
  config,
  onRequestRemoveConnector,
  removeDisabled = false,
  removeDisabledTooltip,
}: SettingsTabProps) {
  const { t } = useTranslation();
  const syncStrategy = getSyncStrategyLabel(config ?? undefined) ?? 'Manual';
  const syncInterval = getSyncIntervalLabel(config ?? undefined);
  const isScheduled = syncStrategy.toLowerCase() === 'scheduled';
  const importStartDate = config?.config?.sync?.scheduledConfig?.startDateTime;

  const removeConnectorButton = onRequestRemoveConnector ? (
    <Button
      type="button"
      color="red"
      variant="soft"
      disabled={removeDisabled}
      style={{
        alignSelf: 'flex-start',
        cursor: removeDisabled ? 'not-allowed' : 'pointer',
      }}
      onClick={() => {
        if (removeDisabled) return;
        onRequestRemoveConnector();
      }}
    >
      Remove connector instance
    </Button>
  ) : null;

  const removeConnectorControl =
    removeConnectorButton && removeDisabled && removeDisabledTooltip ? (
      <Tooltip content={removeDisabledTooltip}>
        <span style={{ display: 'inline-flex' }}>{removeConnectorButton}</span>
      </Tooltip>
    ) : (
      removeConnectorButton
    );

  return (
    <Flex direction="column" gap="5" style={{ padding: '0' }}>
      {/* ── Enabled By ── */}
      <SectionCard title={t('workspace.connectors.settingsTab.enabledBy')}>
        <Flex direction="column" gap="4">
          <InfoRow
            label={t('workspace.connectors.settingsTab.member')}
            value={
              instance.enabledBy ? (
                <Flex align="center" gap="2">
                  <Avatar
                    size="1"
                    fallback={instance.enabledBy.name.charAt(0)}
                    src={instance.enabledBy.avatar}
                    radius="full"
                  />
                  <Text size="2" style={{ color: 'var(--gray-12)' }}>
                    {instance.enabledBy.name}
                  </Text>
                </Flex>
              ) : (
                <Text size="2" style={{ color: 'var(--gray-11)' }}>-</Text>
              )
            }
          />
          <InfoRow
            label={t('workspace.connectors.settingsTab.date')}
            value={
              <Text size="2" style={{ color: 'var(--gray-12)' }}>
                {instance.createdAtTimestamp
                  ? new Date(instance.createdAtTimestamp).toLocaleDateString('en-GB', {
                      day: 'numeric',
                      month: 'short',
                      year: 'numeric',
                    })
                  : '-'}
              </Text>
            }
          />
        </Flex>
      </SectionCard>

      {/* ── Import start date ── */}
      <SectionCard title={t('workspace.connectors.configTab.importDate')}>
        <ReadOnlyField
          value={
            importStartDate
              ? new Date(importStartDate).toLocaleDateString('en-GB', {
                  day: 'numeric',
                  month: 'short',
                  year: 'numeric',
                })
              : '-'
          }
          leadingIcon="date_range"
        />
      </SectionCard>

      {/* ── Sync Settings ── */}
      <SectionCard title={t('workspace.connectors.configTab.syncSettings')}>
        <Flex direction="column" gap="4">
          {/* Sync Strategy */}
          <Flex direction="column" gap="1">
            <Flex direction="column" gap="2">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {t('workspace.connectors.configTab.syncStrategy')}
              </Text>
              <ReadOnlyField value={syncStrategy} />
            </Flex>
            <Text size="1" weight="medium" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.connectors.configTab.syncStrategyHelper', { name: instance.name })}
            </Text>
          </Flex>

          {/* Sync Interval (shown only for scheduled) */}
          {isScheduled && syncInterval && (
            <Flex direction="column" gap="1">
              <Flex direction="column" gap="2">
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                  {t('workspace.connectors.configTab.syncInterval')}
                </Text>
                <ReadOnlyField value={syncInterval} />
              </Flex>
              <Text size="1" weight="medium" style={{ color: 'var(--gray-10)' }}>
                {t('workspace.connectors.configTab.syncIntervalHelper', { name: instance.name })}
              </Text>
            </Flex>
          )}
        </Flex>
      </SectionCard>

      {onRequestRemoveConnector ? (
        <Flex
          direction="column"
          gap="3"
          p="3"
          style={{
            borderRadius: 'var(--radius-3)',
            border: '1px solid var(--red-a6)',
            backgroundColor: 'var(--red-a2)',
          }}
        >
          <Text size="2" weight="bold" color="red">
            Danger zone
          </Text>
          <Text size="2" color="gray" style={{ maxWidth: 420 }}>
            Permanently remove this connector instance and stop syncing its data. This cannot be
            undone.
          </Text>
          {removeConnectorControl}
        </Flex>
      ) : null}
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Flex
      direction="column"
      gap="4"
      style={{
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        padding: 16,
        minWidth: 325,
      }}
    >
      <Flex direction="column" gap="4">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {title}
        </Text>
        <Box
          style={{
            height: 1,
            backgroundColor: 'var(--olive-3)',
            width: '100%',
          }}
        />
      </Flex>
      {children}
    </Flex>
  );
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Flex align="center" gap="4">
      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--gray-10)',
          width: 164,
          flexShrink: 0,
        }}
      >
        {label}
      </Text>
      {value}
    </Flex>
  );
}

function ReadOnlyField({ value, leadingIcon }: { value: string; leadingIcon?: string }) {
  return (
    <Flex
      align="center"
      style={{
        height: 32,
        padding: '0 4px',
        backgroundColor: 'var(--gray-a3)',
        border: '1px solid var(--gray-a6)',
        borderRadius: 'var(--radius-2)',
      }}
    >
      {leadingIcon && (
        <Flex
          align="center"
          justify="center"
          style={{ padding: '0 4px' }}
        >
          <MaterialIcon name={leadingIcon} size={16} color="var(--gray-11)" />
        </Flex>
      )}
      <Flex
        align="center"
        style={{ flex: 1, padding: '0 4px' }}
      >
        <Text size="2" style={{ color: 'var(--gray-12)' }}>
          {value}
        </Text>
      </Flex>
    </Flex>
  );
}

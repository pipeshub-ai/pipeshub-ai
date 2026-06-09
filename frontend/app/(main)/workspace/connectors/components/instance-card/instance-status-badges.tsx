'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  deriveInstanceSetupStatus,
  deriveInstanceSyncOperation,
  type InstanceSetupStatusKey,
  type InstanceSyncOperationKey,
} from './instance-status';
import type { ConnectorConfig, ConnectorInstance } from '../../types';

const SYNC_ACCENT: Record<
  NonNullable<ReturnType<typeof deriveInstanceSyncOperation>>['badgeColor'],
  { bg: string; border: string; value: string }
> = {
  blue: {
    bg: 'var(--blue-a3)',
    border: 'var(--blue-a6)',
    value: 'var(--blue-11)',
  },
  red: {
    bg: 'var(--red-a3)',
    border: 'var(--red-a6)',
    value: 'var(--red-11)',
  },
};

const SETUP_VALUE_COLOR: Record<
  ReturnType<typeof deriveInstanceSetupStatus>['badgeColor'],
  string
> = {
  gray: 'var(--gray-11)',
  amber: 'var(--amber-11)',
  green: 'var(--green-11)',
};

const SETUP_I18N: Record<InstanceSetupStatusKey, string> = {
  not_configured: 'workspace.connectors.instanceStatus.setup.notConfigured',
  needs_authentication: 'workspace.connectors.instanceStatus.setup.needsAuth',
  ready: 'workspace.connectors.instanceStatus.setup.ready',
};

const SETUP_TOOLTIP_I18N: Record<InstanceSetupStatusKey, string> = {
  not_configured: 'workspace.connectors.instanceStatus.setup.notConfiguredTooltip',
  needs_authentication: 'workspace.connectors.instanceStatus.setup.needsAuthTooltip',
  ready: 'workspace.connectors.instanceStatus.setup.readyTooltip',
};

const SYNC_I18N: Record<InstanceSyncOperationKey, string> = {
  syncing: 'workspace.connectors.instanceStatus.sync.syncing',
  full_syncing: 'workspace.connectors.instanceStatus.sync.fullSyncing',
  deleting: 'workspace.connectors.instanceStatus.sync.removing',
};

const SYNC_TOOLTIP_I18N: Record<InstanceSyncOperationKey, string> = {
  syncing: 'workspace.connectors.instanceStatus.sync.syncingTooltip',
  full_syncing: 'workspace.connectors.instanceStatus.sync.fullSyncingTooltip',
  deleting: 'workspace.connectors.instanceStatus.sync.removingTooltip',
};

/** Compact sync pill in the card header (no "Sync" caption). Hidden when idle. */
export function InstanceSyncOperationPill({ instance }: { instance: ConnectorInstance }) {
  const { t } = useTranslation();
  const syncOp = deriveInstanceSyncOperation(instance);
  if (!syncOp) return null;

  const accent = SYNC_ACCENT[syncOp.badgeColor];
  const label = t(SYNC_I18N[syncOp.key]);

  return (
    <Tooltip content={t(SYNC_TOOLTIP_I18N[syncOp.key])}>
      <Flex
        align="center"
        gap="1"
        aria-label={label}
        style={{
          height: 32,
          padding: '0 var(--space-3)',
          borderRadius: 'var(--radius-2)',
          backgroundColor: accent.bg,
          border: `1px solid ${accent.border}`,
          flexShrink: 0,
          cursor: 'default',
        }}
      >
        <MaterialIcon name={syncOp.icon} size={14} color={accent.value} />
        <Text size="2" weight="medium" style={{ color: accent.value, whiteSpace: 'nowrap' }}>
          {label}
        </Text>
      </Flex>
    </Tooltip>
  );
}

/** Configuration / auth readiness in the property list. */
export function InstanceSetupStatusRow({
  instance,
  config,
}: {
  instance: ConnectorInstance;
  config?: ConnectorConfig;
}) {
  const { t } = useTranslation();
  const setup = deriveInstanceSetupStatus(instance, config);
  const valueLabel = t(SETUP_I18N[setup.key]);
  const valueColor = SETUP_VALUE_COLOR[setup.badgeColor];

  return (
    <Tooltip content={t(SETUP_TOOLTIP_I18N[setup.key])}>
      <Flex align="center" gap="4" style={{ width: '100%' }}>
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--gray-10)',
            width: 164,
            flexShrink: 0,
            textTransform: 'uppercase',
            letterSpacing: '0.04px',
            lineHeight: '16px',
          }}
        >
          {t('workspace.connectors.instanceStatus.setup.rowLabel')}
        </Text>
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <MaterialIcon name={setup.icon} size={14} color={valueColor} />
          <Text size="2" style={{ color: valueColor, lineHeight: '20px' }}>
            {valueLabel}
          </Text>
        </Flex>
      </Flex>
    </Tooltip>
  );
}

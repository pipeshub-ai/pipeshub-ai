'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import {
  deriveInstanceSetupStatus,
  deriveInstanceSyncOperation,
  type InstanceSetupStatusKey,
  type InstanceSyncOperationKey,
} from './instance-status';
import type { ConnectorConfig, ConnectorInstance } from '../../types';

const SYNC_VALUE_COLOR: Record<
  NonNullable<ReturnType<typeof deriveInstanceSyncOperation>>['badgeColor'],
  string
> = {
  blue: 'var(--blue-11)',
  red: 'var(--red-11)',
};

const ROW_LABEL_STYLE: React.CSSProperties = {
  color: 'var(--gray-10)',
  width: 164,
  flexShrink: 0,
  textTransform: 'uppercase',
  letterSpacing: '0.04px',
  lineHeight: '16px',
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

const SYNC_LABEL_DEFAULTS: Record<InstanceSyncOperationKey, string> = {
  syncing: 'Sync in progress',
  full_syncing: 'Full sync in progress',
  deleting: 'Removing',
};

const SYNC_TOOLTIP_DEFAULTS: Record<InstanceSyncOperationKey, string> = {
  syncing: 'This connector is syncing new and updated content.',
  full_syncing: 'This connector is running a full re-sync from the source.',
  deleting: 'This connector instance is being removed.',
};

/** Sync-in-progress indicator for the instance card action row. Hidden when idle. */
export function InstanceSyncOperationIndicator({ instance }: { instance: ConnectorInstance }) {
  const syncOp = deriveInstanceSyncOperation(instance);
  if (!syncOp) return null;

  const valueColor = SYNC_VALUE_COLOR[syncOp.badgeColor];
  const label = SYNC_LABEL_DEFAULTS[syncOp.key];
  const isInProgress = syncOp.key === 'syncing' || syncOp.key === 'full_syncing';

  return (
    <Tooltip content={SYNC_TOOLTIP_DEFAULTS[syncOp.key]}>
      <Flex
        role="status"
        aria-live="polite"
        aria-label={label}
        align="center"
        gap="2"
        style={{ flexShrink: 0, marginLeft: 'var(--space-2)' }}
      >
        {isInProgress ? (
          <Spinner size={14} color={valueColor} ariaLabel={label} />
        ) : (
          <MaterialIcon name={syncOp.icon} size={14} color={valueColor} />
        )}
        <Text size="2" weight="medium" style={{ color: valueColor, whiteSpace: 'nowrap', lineHeight: '20px' }}>
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
          style={ROW_LABEL_STYLE}
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

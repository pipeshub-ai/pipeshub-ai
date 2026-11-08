'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import {
  deriveInstanceSetupStatus,
  deriveInstanceSyncOperation,
  type InstanceSetupStatusKey,
  type InstanceSyncOperationKey,
} from './instance-status';
import type { ConnectorConfig, ConnectorInstance } from '../../types';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

const SYNC_VALUE_COLOR: Record<
  NonNullable<ReturnType<typeof deriveInstanceSyncOperation>>['badgeColor'],
  string
> = {
  blue: 'var(--blue-11)',
  amber: 'var(--amber-11)',
  red: 'var(--red-11)',
};

const ROW_LABEL_STYLE: React.CSSProperties = {
  color: 'var(--gray-10)',
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
  red: 'var(--red-11)',
  green: 'var(--green-11)',
};

const SETUP_I18N: Record<InstanceSetupStatusKey, string> = {
  not_configured: 'workspace.connectors.instanceStatus.setup.notConfigured',
  needs_authentication: 'workspace.connectors.instanceStatus.setup.needsAuth',
  action_needed: 'workspace.connectors.instanceStatus.setup.actionNeeded',
  desktop_offline: 'workspace.connectors.instanceStatus.setup.desktopOffline',
  ready: 'workspace.connectors.instanceStatus.setup.ready',
};

const SETUP_TOOLTIP_I18N: Record<InstanceSetupStatusKey, string> = {
  not_configured: 'workspace.connectors.instanceStatus.setup.notConfiguredTooltip',
  needs_authentication: 'workspace.connectors.instanceStatus.setup.needsAuthTooltip',
  action_needed: 'workspace.connectors.instanceStatus.setup.actionNeededTooltip',
  desktop_offline: 'workspace.connectors.instanceStatus.setup.desktopOfflineTooltip',
  ready: 'workspace.connectors.instanceStatus.setup.readyTooltip',
};

const SYNC_I18N: Record<InstanceSyncOperationKey, string> = {
  syncing: 'workspace.connectors.instanceStatus.sync.syncing',
  full_syncing: 'workspace.connectors.instanceStatus.sync.fullSyncing',
  queued: 'workspace.connectors.instanceStatus.sync.queued',
  deleting: 'workspace.connectors.instanceStatus.sync.removing',
};

const SYNC_TOOLTIP_I18N: Record<InstanceSyncOperationKey, string> = {
  syncing: 'workspace.connectors.instanceStatus.sync.syncingTooltip',
  full_syncing: 'workspace.connectors.instanceStatus.sync.fullSyncingTooltip',
  queued: 'workspace.connectors.instanceStatus.sync.queuedTooltip',
  deleting: 'workspace.connectors.instanceStatus.sync.removingTooltip',
};

const SYNC_LABEL_DEFAULTS: Record<InstanceSyncOperationKey, string> = {
  syncing: 'Sync in progress',
  full_syncing: 'Full sync in progress',
  queued: 'Queued',
  deleting: 'Removing',
};

const SYNC_TOOLTIP_DEFAULTS: Record<InstanceSyncOperationKey, string> = {
  syncing: 'This connector is syncing new and updated content.',
  full_syncing: 'This connector is running a full re-sync from the source.',
  queued:
    'Waiting for a free sync slot. It will start automatically when one frees up.',
  deleting: 'This connector instance is being removed.',
};

/** Sync-in-progress indicator for the instance card action row. Hidden when idle. */
export function InstanceSyncOperationIndicator({ instance }: { instance: ConnectorInstance }) {
  const { t } = useTranslation();
  const syncOp = deriveInstanceSyncOperation(instance);
  if (!syncOp) return null;

  const valueColor = SYNC_VALUE_COLOR[syncOp.badgeColor];
  const label = t(SYNC_I18N[syncOp.key], { defaultValue: SYNC_LABEL_DEFAULTS[syncOp.key] });
  const tooltipContent = t(SYNC_TOOLTIP_I18N[syncOp.key], {
    defaultValue: SYNC_TOOLTIP_DEFAULTS[syncOp.key],
  });
  const isInProgress =
    syncOp.key === 'syncing' ||
    syncOp.key === 'full_syncing' ||
    syncOp.key === 'queued';

  return (
    <Tooltip content={tooltipContent}>
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
  const isMobile = useIsMobile();
  const { t } = useTranslation();
  const setup = deriveInstanceSetupStatus(instance, config);
  const valueLabel = t(SETUP_I18N[setup.key]);
  const valueColor = SETUP_VALUE_COLOR[setup.badgeColor];

  // The items' own titles say what to do; the generic text only frames them.
  const tooltip =
    setup.key === 'action_needed'
      ? t(SETUP_TOOLTIP_I18N[setup.key], {
          items: (instance.actionItems ?? []).map((item) => item.title).join('; '),
        })
      : t(SETUP_TOOLTIP_I18N[setup.key]);

  return (
    <Tooltip content={tooltip}>
      <Flex direction={isMobile ? 'column' : 'row'} align={isMobile ? 'start' : 'center'} gap={isMobile ? '1' : '4'} style={{ width: '100%' }}>
        <Text
          size="1"
          weight="medium"
          style={{ ...ROW_LABEL_STYLE, width: isMobile ? 'auto' : 164 }}
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

/** Header badge of an instance with something for its admin to do; opens the instance panel. */
export function InstanceActionNeededBadge({
  instance,
  onOpen,
}: {
  instance: ConnectorInstance;
  onOpen?: (instance: ConnectorInstance) => void;
}) {
  const { t } = useTranslation();
  const items = instance.actionItems ?? [];
  if (items.length === 0) return null;
  const anError = items.some((item) => item.severity === 'error');
  const label = t('workspace.connectors.actionItems.badge');

  return (
    <Tooltip content={items.map((item) => item.title).join('; ')}>
      <Badge
        asChild
        color={anError ? 'red' : 'amber'}
        variant="soft"
        size="1"
        style={{ flexShrink: 0, cursor: onOpen ? 'pointer' : 'default', minHeight: 24 }}
      >
        <button type="button" aria-label={label} onClick={() => onOpen?.(instance)} disabled={!onOpen}>
          <MaterialIcon name="priority_high" size={14} />
          {label}
        </button>
      </Badge>
    </Tooltip>
  );
}

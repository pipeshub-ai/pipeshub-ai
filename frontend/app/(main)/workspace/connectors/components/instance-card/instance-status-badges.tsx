'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  deriveInstanceSetupStatus,
  type InstanceSetupStatusKey,
} from './instance-status';
import type { ConnectorConfig, ConnectorInstance } from '../../types';

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

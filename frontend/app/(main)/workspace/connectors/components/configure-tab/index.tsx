'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Callout, Flex } from '@radix-ui/themes';
import { ConnectorConfigureExtras } from '@/config';
import { useConnectorsStore } from '../../store';
import { InstanceHeader } from './instance-header';
import { SyncSettingsSection } from './sync-settings-section';
import { CustomSyncFieldsSection } from './custom-sync-fields-section';
import { FiltersSection } from './filters-section';

// ========================================
// Component
// ========================================

export function ConfigureTab({ readOnly = false }: { readOnly?: boolean }) {
  const { t } = useTranslation();
  const {
    connectorSchema,
    connectorConfig,
    panelConnector,
    panelConnectorId,
    instanceName,
    formData,
    formErrors,
    setSyncFormValue,
    setSyncStrategy,
    setSyncInterval,
  } = useConnectorsStore();

  if (!connectorSchema || !panelConnector) return null;

  const schema = connectorSchema;
  const syncConfig = schema.sync;
  const supportedStrategies = syncConfig?.supportedStrategies ?? [];
  const customSyncFields = syncConfig?.customFields ?? [];
  const selectedStrategy = formData.sync.selectedStrategy;

  return (
    <Flex direction="column" gap="6" style={{ padding: 'var(--space-1) 0' }}>
      {readOnly && (
        <Callout.Root color="blue" variant="surface" size="1">
          <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.connectors.configTab.localFsDesktopOnlyBanner')}
          </Callout.Text>
        </Callout.Root>
      )}

      {/* ── A. Instance Header ── */}
      <InstanceHeader connectorName={panelConnector.name} />

      {/* ── B. Sync Settings ── */}
      {supportedStrategies.length > 0 && (
        <SyncSettingsSection
          supportedStrategies={supportedStrategies}
          selectedStrategy={selectedStrategy}
          intervalMinutes={formData.sync.scheduledConfig.intervalMinutes}
          connectorName={panelConnector.name}
          onStrategyChange={setSyncStrategy}
          onIntervalChange={setSyncInterval}
          disabled={readOnly}
        />
      )}

      {/* ── E. Custom Sync Fields (server-driven) ── */}
      {customSyncFields.length > 0 && (
        <CustomSyncFieldsSection
          fields={customSyncFields}
          groups={syncConfig?.customFieldGroups}
          values={formData.sync.customValues}
          errors={formErrors}
          onChange={setSyncFormValue}
          connectorConfig={connectorConfig}
          connectorType={panelConnector.type}
          lastSyncSummary={panelConnector.lastSyncSummary}
          isNewConnector={!connectorConfig?.isConfigured}
          readOnly={readOnly}
        />
      )}

      <FiltersSection key={panelConnectorId ?? 'new'} readOnly={readOnly} />

      <ConnectorConfigureExtras
        connectorId={panelConnectorId ?? null}
        connectorType={panelConnector.type}
        // A new instance's panel still holds the catalog entry; prefer the instance's own name.
        connectorName={connectorConfig?.name || instanceName.trim() || panelConnector.name}
        scope={panelConnector.scope}
      />
    </Flex>
  );
}

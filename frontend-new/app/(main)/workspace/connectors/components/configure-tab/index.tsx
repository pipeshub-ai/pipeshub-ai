'use client';

import React from 'react';
import { Flex } from '@radix-ui/themes';
import { useConnectorsStore } from '../../store';
import { InstanceHeader } from './instance-header';
import { SyncSettingsSection } from './sync-settings-section';
import { CustomSyncFieldsSection } from './custom-sync-fields-section';
import { FiltersSection } from './filters-section';

// ========================================
// Component
// ========================================

export function ConfigureTab() {
  const {
    connectorSchema,
    panelConnector,
    panelConnectorId,
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
        />
      )}

      {/* ── E. Custom Sync Fields (server-driven) ── */}
      {customSyncFields.length > 0 && (
        <CustomSyncFieldsSection
          fields={customSyncFields}
          values={formData.sync.customValues}
          errors={formErrors}
          onChange={setSyncFormValue}
        />
      )}

      <FiltersSection key={panelConnectorId ?? 'new'} />
    </Flex>
  );
}

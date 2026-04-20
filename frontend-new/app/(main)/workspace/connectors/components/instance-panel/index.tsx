'use client';

import React, { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { usePathname } from 'next/navigation';
import { Flex, Text, Tabs, Button, DropdownMenu } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getConnectorIconPath } from '@/lib/utils/connector-icon-utils';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { useConnectorsStore } from '../../store';
import type { ConnectorScope } from '../../types';
import { OverviewTab } from './overview-tab';
import { SettingsTab } from './settings-tab';
import type { InstancePanelTab } from '../../types';

// ========================================
// InstanceManagementPanel
// ========================================

export function InstanceManagementPanel() {
  const pathname = usePathname();
  const {
    isInstancePanelOpen,
    selectedInstance,
    instancePanelTab,
    instanceConfigs,
    instanceStats,
    instances,
    closeInstancePanel,
    setInstancePanelTab,
    openPanel,
    openInstancePanel,
  } = useConnectorsStore();

  const { t } = useTranslation();
  const [iconError, setIconError] = useState(false);
  const [triggerHovered, setTriggerHovered] = useState(false);

  const handleManageConfiguration = useCallback(() => {
    if (!selectedInstance) return;
    const scope: ConnectorScope = pathname.includes('/connectors/personal')
      ? 'personal'
      : 'team';
    // Open the connector configuration panel (reuse the setup panel)
    closeInstancePanel();
    openPanel(selectedInstance, selectedInstance._key, scope);
  }, [selectedInstance, closeInstancePanel, openPanel, pathname]);

  if (!selectedInstance) return null;

  const instanceId = selectedInstance._key;
  const instanceConfig = instanceId ? instanceConfigs[instanceId] : undefined;
  const instanceStat = instanceId ? instanceStats[instanceId] : undefined;

  const iconSrc = getConnectorIconPath(selectedInstance.iconPath);
  const lastSyncedLabel = selectedInstance.lastSynced
    ? `Synced ${selectedInstance.lastSynced}`
    : undefined;

  // Connector icon used as panel header icon
  const connectorIcon = (
    <Flex
      align="center"
      justify="center"
      style={{ width: 20, height: 20, flexShrink: 0 }}
    >
      {iconError ? (
        <MaterialIcon name="hub" size={16} color="var(--gray-9)" />
      ) : (
        <img
          src={iconSrc}
          alt={selectedInstance.name}
          width={16}
          height={16}
          onError={() => setIconError(true)}
          style={{ display: 'block', objectFit: 'contain' }}
        />
      )}
    </Flex>
  );

  const headerActions = lastSyncedLabel ? (
    <Flex align="center" gap="1">
      <MaterialIcon name="sync" size={14} color="var(--gray-9)" />
      <Text size="1" style={{ color: 'var(--gray-9)' }}>
        {lastSyncedLabel}
      </Text>
    </Flex>
  ) : undefined;

  // When there are multiple instances of this connector type, render an
  // instance-switcher dropdown instead of a plain title string.
  const titleNode =
    instances.length > 1 ? (
      <DropdownMenu.Root>
        <DropdownMenu.Trigger>
          <button
            onMouseEnter={() => setTriggerHovered(true)}
            onMouseLeave={() => setTriggerHovered(false)}
            style={{
              appearance: 'none',
              border: 'none',
              background: triggerHovered ? 'var(--slate-a3)' : 'transparent',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              padding: '2px 6px 2px 4px',
              borderRadius: 'var(--radius-2)',
              transition: 'background 0.15s',
            }}
          >
            <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {selectedInstance.name}
            </Text>
            <MaterialIcon name="expand_more" size={16} color="var(--slate-11)" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Content>
          {instances.map((inst) => (
            <DropdownMenu.Item
              key={inst._key}
              onSelect={() => {
                if (inst._key !== selectedInstance._key) {
                  openInstancePanel(inst);
                }
              }}
              style={{
                fontWeight: inst._key === selectedInstance._key ? 500 : 400,
              }}
            >
              {inst.name}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    ) : undefined;

  return (
    <WorkspaceRightPanel
      open={isInstancePanelOpen}
      onOpenChange={(open) => {
        if (!open) closeInstancePanel();
      }}
      title={selectedInstance.name}
      titleNode={titleNode}
      icon={connectorIcon}
      headerActions={headerActions}
      hideFooter
    >
      <Flex direction="column" style={{ height: '100%' }}>
        {/* ── Tab bar ── */}
        <Tabs.Root
          value={instancePanelTab}
          onValueChange={(v) => setInstancePanelTab(v as InstancePanelTab)}
        >
          <Tabs.List
            size="2"
            style={{
              borderBottom: '1px solid var(--gray-a6)',
              marginBottom: 16,
            }}
          >
            <Tabs.Trigger value="overview">{t('workspace.connectors.instancePanel.overview')}</Tabs.Trigger>
            <Tabs.Trigger value="settings">{t('workspace.connectors.instancePanel.settings')}</Tabs.Trigger>
          </Tabs.List>

          <Tabs.Content value="overview">
            <OverviewTab instance={selectedInstance} stats={instanceStat} />
          </Tabs.Content>
          <Tabs.Content value="settings">
            <SettingsTab instance={selectedInstance} config={instanceConfig} />
          </Tabs.Content>
        </Tabs.Root>

        {/* ── Manage Configuration button (bottom) ── */}
        <Flex
          justify="end"
          style={{
            marginTop: 'auto',
            paddingTop: 16,
          }}
        >
          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={handleManageConfiguration}
            style={{ cursor: 'pointer' }}
          >
            <MaterialIcon name="settings" size={16} color="var(--gray-11)" />
            {t('workspace.connectors.instancePanel.manageConfig')}
          </Button>
        </Flex>
      </Flex>
    </WorkspaceRightPanel>
  );
}

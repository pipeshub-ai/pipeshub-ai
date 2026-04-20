'use client';

import React, { useState, useCallback } from 'react';
import { usePathname } from 'next/navigation';
import { AlertDialog, Flex, Text, Tabs, Button, DropdownMenu } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getConnectorIconPath } from '@/lib/utils/connector-icon-utils';
import {
  WorkspaceRightPanel,
  useWorkspaceDrawerNestedModalHost,
} from '@/app/(main)/workspace/components/workspace-right-panel';
import { useToastStore } from '@/lib/store/toast-store';
import { useConnectorsStore } from '../../store';
import { ConnectorsApi } from '../../api';
import type { Connector, ConnectorScope, InstancePanelTab } from '../../types';
import { OverviewTab } from './overview-tab';
import { SettingsTab } from './settings-tab';

// ========================================
// InstanceManagementPanel
// ========================================

export function InstanceManagementPanel() {
  const pathname = usePathname();
  const addToast = useToastStore((s) => s.addToast);
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
    upsertConnectorInstance,
    removeConnectorInstanceCaches,
    bumpCatalogRefresh,
  } = useConnectorsStore();

  const [iconError, setIconError] = useState(false);
  const [triggerHovered, setTriggerHovered] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const nestedModalHost = useWorkspaceDrawerNestedModalHost(isInstancePanelOpen);

  const handleManageConfiguration = useCallback(() => {
    if (!selectedInstance) return;
    const scope: ConnectorScope = pathname.includes('/connectors/personal')
      ? 'personal'
      : 'team';
    // Open the connector configuration panel (reuse the setup panel)
    closeInstancePanel();
    openPanel(selectedInstance, selectedInstance._key, scope);
  }, [selectedInstance, closeInstancePanel, openPanel, pathname]);

  const openRemoveDialog = useCallback(() => {
    if (!selectedInstance?._key) return;
    setPendingDeleteId(selectedInstance._key);
    setDeleteOpen(true);
  }, [selectedInstance?._key]);

  const confirmRemoveConnector = useCallback(async () => {
    const id = pendingDeleteId;
    if (!id || deleteBusy) return;
    setDeleteBusy(true);
    try {
      const raw = await ConnectorsApi.deleteConnectorInstance(id);
      const data = raw as Record<string, unknown> | null | undefined;
      if (
        data &&
        typeof data === 'object' &&
        typeof data._key === 'string' &&
        typeof data.type === 'string'
      ) {
        upsertConnectorInstance(data as unknown as Connector);
      }
      removeConnectorInstanceCaches(id);
      addToast({
        variant: 'success',
        title: 'Connector removal started',
        description: 'This instance is being deleted. It may take a moment to disappear from the list.',
        duration: 4000,
      });
      setDeleteOpen(false);
      setPendingDeleteId(null);
      closeInstancePanel();
      bumpCatalogRefresh();
    } catch {
      addToast({
        variant: 'error',
        title: 'Failed to remove connector',
      });
    } finally {
      setDeleteBusy(false);
    }
  }, [
    pendingDeleteId,
    deleteBusy,
    upsertConnectorInstance,
    removeConnectorInstanceCaches,
    addToast,
    closeInstancePanel,
    bumpCatalogRefresh,
  ]);

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
  const instancePendingDelete =
    (pendingDeleteId ? instances.find((i) => i._key === pendingDeleteId) : null) ?? selectedInstance;

  const removeConnectorDisabled =
    selectedInstance.isActive === true || selectedInstance.status === 'DELETING';

  const removeConnectorDisabledReason =
    selectedInstance.status === 'DELETING'
      ? 'This connector is already being removed.'
      : selectedInstance.isActive
        ? 'Turn off sync before removing this connector.'
        : null;

  const manageConfigDisabled = selectedInstance.status === 'DELETING';

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
    <>
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
            <Tabs.Trigger value="overview">Overview</Tabs.Trigger>
            <Tabs.Trigger value="settings">Settings</Tabs.Trigger>
          </Tabs.List>

            <Tabs.Content value="overview">
              <OverviewTab instance={selectedInstance} stats={instanceStat} />
            </Tabs.Content>
            <Tabs.Content value="settings">
              <SettingsTab
                instance={selectedInstance}
                config={instanceConfig}
                onRequestRemoveConnector={
                  selectedInstance._key ? openRemoveDialog : undefined
                }
                removeConnectorDisabled={removeConnectorDisabled}
                removeConnectorDisabledReason={removeConnectorDisabledReason}
              />
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
            disabled={manageConfigDisabled}
            onClick={handleManageConfiguration}
            style={{ cursor: 'pointer' }}
          >
            <MaterialIcon name="settings" size={16} color="var(--gray-11)" />
            Manage Configuration
          </Button>
        </Flex>
      </Flex>
    </WorkspaceRightPanel>

      {nestedModalHost ? (
        <AlertDialog.Root
          open={deleteOpen}
          onOpenChange={(open) => {
            if (!open && deleteBusy) return;
            setDeleteOpen(open);
            if (!open) setPendingDeleteId(null);
          }}
        >
          <AlertDialog.Content container={nestedModalHost} style={{ maxWidth: 440 }}>
            <AlertDialog.Title>Remove connector instance?</AlertDialog.Title>
            <AlertDialog.Description size="2">
              Are you sure you want to delete{' '}
              <Text weight="bold">&quot;{instancePendingDelete.name}&quot;</Text>? This cannot be
              undone.
            </AlertDialog.Description>
            <Flex gap="3" justify="end" mt="4">
              <AlertDialog.Cancel>
                <Button variant="soft" color="gray" disabled={deleteBusy}>
                  Cancel
                </Button>
              </AlertDialog.Cancel>
              <Button color="red" loading={deleteBusy} onClick={() => void confirmRemoveConnector()}>
                Remove connector
              </Button>
            </Flex>
          </AlertDialog.Content>
        </AlertDialog.Root>
      ) : null}
    </>
  );
}

'use client';

import { useEffect, useCallback, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useToastStore } from '@/lib/store/toast-store';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import {
  ConnectorCatalogLayout,
  ConnectorPanel,
  ConnectorDetailsLayout,
  InstanceManagementPanel,
  ConfigSuccessDialog,
} from '../components';
import type { Connector, ConnectorInstance, TeamFilterTab } from '../types';

// ========================================
// Constants
// ========================================

const TEAM_TABS = [
  { value: 'all', label: 'All' },
  { value: 'configured', label: 'Configured' },
  { value: 'not_configured', label: 'Not Configured' },
];

// ========================================
// Page
// ========================================

function TeamConnectorsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const addToast = useToastStore((s) => s.addToast);

  // The connectorType query param determines whether we show the instance page
  const connectorType = searchParams.get('connectorType');

  const {
    registryConnectors,
    activeConnectors,
    searchQuery,
    teamFilterTab,
    isLoading,
    instances,
    isLoadingInstances,
    connectorTypeInfo,
    showConfigSuccessDialog,
    newlyConfiguredConnectorId,
    instanceConfigs,
    instanceStats,
    setRegistryConnectors,
    setActiveConnectors,
    setSearchQuery,
    setTeamFilterTab,
    setIsLoading,
    setError,
    openPanel,
    setInstances,
    setIsLoadingInstances,
    setConnectorTypeInfo,
    setInstanceConfig,
    setInstanceStats,
    clearInstanceData,
    openInstancePanel,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
  } = useConnectorsStore();

  // ── URL → Store: sync tab from query param ───────────────────
  useEffect(() => {
    const tab = searchParams.get('tab') as TeamFilterTab | null;
    const validTabs: TeamFilterTab[] = ['all', 'configured', 'not_configured'];
    if (tab && validTabs.includes(tab)) {
      setTeamFilterTab(tab);
    } else {
      setTeamFilterTab('all');
    }
  }, [searchParams, setTeamFilterTab]);

  // ── Fetch connector list data ───────────────────────────────
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [registryRes, activeRes] = await Promise.allSettled([
        ConnectorsApi.getRegistryConnectors('team'),
        ConnectorsApi.getActiveConnectors('team'),
      ]);

      if (registryRes.status === 'fulfilled') {
        setRegistryConnectors(registryRes.value.connectors);
      }
      if (activeRes.status === 'fulfilled') {
        setActiveConnectors(activeRes.value.connectors);
      }

      // If both failed, show error
      if (registryRes.status === 'rejected' && activeRes.status === 'rejected') {
        setError('Failed to load connectors');
        addToast({
          variant: 'error',
          title: 'Failed to load connectors',
        });
      }
    } catch {
      setError('Failed to load connectors');
    } finally {
      setIsLoading(false);
    }
  }, [setRegistryConnectors, setActiveConnectors, setIsLoading, setError, addToast]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // ── Fetch instances when connectorType is present ───────────
  // Filter active connectors by type client-side, then fetch config + stats per instance
  useEffect(() => {
    if (!connectorType) return;

    // Prefer registry connector for type-level info (correct type name, not instance name)
    const registryInfo = registryConnectors.find((c) => c.type === connectorType) ?? null;
    const activeInfo = activeConnectors.find((c) => c.type === connectorType) ?? null;
    setConnectorTypeInfo(registryInfo ?? activeInfo);

    // Filter active connectors by type
    const typeInstances = activeConnectors.filter(
      (c) => c.type === connectorType
    ) as ConnectorInstance[];

    setInstances(typeInstances);

    // Fetch config and stats for each instance
    const fetchInstanceDetails = async () => {
      setIsLoadingInstances(true);
      try {
        const detailPromises = typeInstances.map(async (instance) => {
          if (!instance._key) return;
          const [configRes, statsRes] = await Promise.allSettled([
            ConnectorsApi.getConnectorConfig(instance._key),
            ConnectorsApi.getConnectorStats(instance._key),
          ]);
          if (configRes.status === 'fulfilled') {
            setInstanceConfig(instance._key, configRes.value);
          }
          if (statsRes.status === 'fulfilled') {
            setInstanceStats(instance._key, statsRes.value.data);
          }
        });
        await Promise.allSettled(detailPromises);
      } catch {
        // Individual failures are handled per-instance above
      } finally {
        setIsLoadingInstances(false);
      }
    };

    if (typeInstances.length > 0) {
      fetchInstanceDetails();
    } else {
      setIsLoadingInstances(false);
    }
  }, [
    connectorType,
    activeConnectors,
    registryConnectors,
    setConnectorTypeInfo,
    setIsLoadingInstances,
    setInstances,
    setInstanceConfig,
    setInstanceStats,
  ]);

  // ── Handlers (list view) ───────────────────────────────────
  const handleSetup = useCallback(
    (connector: Connector) => {
      // For active connectors (have _key), open in edit mode
      // For registry connectors (no _key), open in create mode
      const connectorId = connector._key;
      openPanel(connector, connectorId);
    },
    [openPanel]
  );

  const handleCardClick = useCallback(
    (connector: Connector) => {
      // Navigate to the connector type page
      router.push(
        `/workspace/connectors/team/?connectorType=${encodeURIComponent(connector.type)}`
      );
    },
    [router]
  );

  const handleNavigateToPersonal = useCallback(() => {
    router.push('/workspace/connectors/personal/');
  }, [router]);

  const handleTabChange = useCallback(
    (val: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (val === 'all') {
        params.delete('tab');
      } else {
        params.set('tab', val);
      }
      const query = params.toString();
      router.replace(
        query
          ? `/workspace/connectors/team/?${query}`
          : '/workspace/connectors/team/'
      );
    },
    [router, searchParams]
  );

  // ── Handlers (type page view) ──────────────────────────────
  const handleBackToList = useCallback(() => {
    setConnectorTypeInfo(null);
    clearInstanceData();
    router.push('/workspace/connectors/team/');
  }, [router, setConnectorTypeInfo, clearInstanceData]);

  const handleAddInstance = useCallback(() => {
    if (!connectorTypeInfo) return;
    // Open panel in create mode (no _key)
    openPanel(connectorTypeInfo);
  }, [connectorTypeInfo, openPanel]);

  const handleOpenDocs = useCallback(() => {
    // Try config.documentationLinks first, then fall back to connectorInfo
    const configObj = connectorTypeInfo?.config as Record<string, unknown> | undefined;
    const docLinks = configObj?.documentationLinks as { url?: string }[] | undefined;
    const docUrl =
      docLinks?.[0]?.url ??
      (connectorTypeInfo?.connectorInfo?.documentationUrl as string | undefined);
    if (docUrl) {
      window.open(docUrl, '_blank', 'noopener,noreferrer');
    }
  }, [connectorTypeInfo]);

  const handleManageInstance = useCallback(
    (instance: ConnectorInstance) => {
      openInstancePanel(instance);
    },
    [openInstancePanel]
  );

  const handleStartSync = useCallback(
    async (instance: ConnectorInstance) => {
      if (!instance._key) return;
      try {
        await ConnectorsApi.startSync(instance._key);
        addToast({
          variant: 'success',
          title: `${connectorTypeInfo?.name ?? 'Connector'} is now syncing`,
          description: 'Your records will be available shortly.',
          duration: 3000,
        });
        // Re-fetch active connectors and instance details
        try {
          const activeRes = await ConnectorsApi.getActiveConnectors('team');
          setActiveConnectors(activeRes.connectors);
        } catch {
          // Silently fail — the list will refresh on next navigation
        }
      } catch {
        addToast({
          variant: 'error',
          title: 'Failed to start sync',
        });
      }
    },
    [connectorTypeInfo, addToast, setActiveConnectors]
  );

  const handleInstanceChevron = useCallback(
    (instance: ConnectorInstance) => {
      openInstancePanel(instance);
    },
    [openInstancePanel]
  );

  // ── Success dialog handlers ─────────────────────────────────
  const handleStartSyncingFromDialog = useCallback(async () => {
    setShowConfigSuccessDialog(false);
    const instanceId = newlyConfiguredConnectorId;
    setNewlyConfiguredConnectorId(null);
    if (!instanceId) return;

    try {
      await ConnectorsApi.startSync(instanceId);
      addToast({
        variant: 'success',
        title: `Your ${connectorTypeInfo?.name ?? 'connector'} instance is now syncing`,
        description:
          'This may take a few minutes. You\'ll be notified when it\'s done.',
        duration: 3000,
      });
      // Re-fetch active connectors to update the list
      try {
        const activeRes = await ConnectorsApi.getActiveConnectors('team');
        setActiveConnectors(activeRes.connectors);
      } catch {
        // Silently fail
      }
    } catch {
      addToast({
        variant: 'error',
        title: 'Failed to start sync',
      });
    }
  }, [
    newlyConfiguredConnectorId,
    connectorTypeInfo,
    addToast,
    setActiveConnectors,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
  ]);

  const handleDoLater = useCallback(() => {
    setShowConfigSuccessDialog(false);
    setNewlyConfiguredConnectorId(null);
  }, [setShowConfigSuccessDialog, setNewlyConfiguredConnectorId]);

  // ── Render ─────────────────────────────────────────────────
  // If connectorType is present, show the connector type page
  if (connectorType) {
    return (
      <>
        <ConnectorDetailsLayout
          connector={connectorTypeInfo}
          scope="team"
          scopeLabel="Connectors"
          instances={instances}
          instanceConfigs={instanceConfigs}
          instanceStats={instanceStats}
          isLoading={isLoadingInstances}
          onBack={handleBackToList}
          onAddInstance={handleAddInstance}
          onOpenDocs={handleOpenDocs}
          onManageInstance={handleManageInstance}
          onStartSync={handleStartSync}
          onInstanceChevron={handleInstanceChevron}
        />
        <ConnectorPanel />
        <InstanceManagementPanel />
        <ConfigSuccessDialog
          open={showConfigSuccessDialog}
          connectorName={connectorTypeInfo?.name ?? ''}
          onStartSyncing={handleStartSyncingFromDialog}
          onDoLater={handleDoLater}
        />
      </>
    );
  }

  return (
    <>
      <ConnectorCatalogLayout
        title="Connectors"
        subtitle="Connect and manage integrations with external services"
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        tabs={TEAM_TABS}
        activeTab={teamFilterTab}
        onTabChange={handleTabChange}
        trailingAction={
          <NavigateButton
            label="Your Connectors"
            onClick={handleNavigateToPersonal}
          />
        }
        registryConnectors={registryConnectors}
        activeConnectors={activeConnectors}
        onSetup={handleSetup}
        onCardClick={handleCardClick}
        isLoading={isLoading}
      />
      <ConnectorPanel />
    </>
  );
}

// ========================================
// Sub-component: trailing nav button
// ========================================

function NavigateButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        padding: '0 var(--space-3)',
        font: 'inherit',
        outline: 'none',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-2)',
        height: 'var(--space-8)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--gray-a4)' : 'var(--gray-a3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <span
        style={{
          fontSize: 14,
          fontWeight: 500,
          lineHeight: '20px',
          color: 'var(--gray-11)',
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </span>
      <MaterialIcon name="arrow_forward" size={16} color="var(--gray-11)" />
    </button>
  );
}

export default function TeamConnectorsPage() {
  return (
    <Suspense>
      <TeamConnectorsPageContent />
    </Suspense>
  );
}



'use client';

import { useEffect, useLayoutEffect, useCallback, useMemo, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import { ensureConnectorSyncActiveThenResync } from '../utils/connector-sync-actions';
import { filterConnectorsForScope } from '../utils/filter-connectors-by-scope';
import { fetchFilteredConnectorLists } from '../utils/fetch-filtered-connector-lists';
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

function TeamConnectorsAccessGate() {
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  useEffect(() => {
    if (!isProfileInitialized) return;
    if (isAdmin !== true) {
      router.replace('/workspace/connectors/personal/');
    }
  }, [isProfileInitialized, isAdmin, router]);

  if (!isProfileInitialized || isAdmin !== true) {
    return null;
  }

  return <TeamConnectorsPageContent />;
}

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
    upsertConnectorInstance,
    clearInstanceData,
    openInstancePanel,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    catalogRefreshToken,
    bumpCatalogRefresh,
    setSelectedScope,
  } = useConnectorsStore();

  // Keep catalog scope in store aligned with this route (panel + API use `selectedScope`).
  useLayoutEffect(() => {
    setSelectedScope('team');
  }, [setSelectedScope]);

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
        setRegistryConnectors(filterConnectorsForScope(registryRes.value.connectors, 'team'));
      }
      if (activeRes.status === 'fulfilled') {
        setActiveConnectors(filterConnectorsForScope(activeRes.value.connectors, 'team'));
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
  }, [fetchData, catalogRefreshToken]);

  /** Stable across upserts that only change fields on existing instances (same ids → same string). */
  const instanceDetailKeys = useMemo(() => {
    if (!connectorType) return '';
    return activeConnectors
      .filter((c) => c.type === connectorType && c._key)
      .map((c) => c._key as string)
      .sort()
      .join('|');
  }, [activeConnectors, connectorType]);

  // ── Instance type page: keep list + header in sync (no loading) ──
  useEffect(() => {
    if (!connectorType) return;

    const registryInfo = registryConnectors.find((c) => c.type === connectorType) ?? null;
    const activeInfo = activeConnectors.find((c) => c.type === connectorType) ?? null;
    setConnectorTypeInfo(registryInfo ?? activeInfo);

    const typeInstances = activeConnectors.filter(
      (c) => c.type === connectorType
    ) as ConnectorInstance[];

    setInstances(typeInstances);
  }, [
    connectorType,
    activeConnectors,
    registryConnectors,
    setConnectorTypeInfo,
    setInstances,
  ]);

  // ── Fetch config + stats when instance set or catalog refresh changes (full loader) ──
  useEffect(() => {
    if (!connectorType) {
      setIsLoadingInstances(false);
      return;
    }

    const instanceIds = instanceDetailKeys.split('|').filter(Boolean);
    if (instanceIds.length === 0) {
      setIsLoadingInstances(false);
      return;
    }

    let cancelled = false;

    const run = async () => {
      setIsLoadingInstances(true);
      try {
        await Promise.allSettled(
          instanceIds.map(async (id) => {
            const [configRes, statsRes] = await Promise.allSettled([
              ConnectorsApi.getConnectorConfig(id),
              ConnectorsApi.getConnectorStats(id),
            ]);
            if (cancelled) return;
            if (configRes.status === 'fulfilled') {
              setInstanceConfig(id, configRes.value);
            }
            if (statsRes.status === 'fulfilled') {
              setInstanceStats(id, statsRes.value.data);
            }
          })
        );
      } finally {
        if (!cancelled) {
          setIsLoadingInstances(false);
        }
      }
    };

    void run();

    return () => {
      cancelled = true;
    };
  }, [
    connectorType,
    catalogRefreshToken,
    instanceDetailKeys,
    setIsLoadingInstances,
    setInstanceConfig,
    setInstanceStats,
  ]);

  const refreshConnectorRowQuiet = useCallback(
    async (connectorId: string) => {
      const fresh = await ConnectorsApi.getConnectorInstance(connectorId);
      upsertConnectorInstance(fresh);
      void ConnectorsApi.getConnectorStats(connectorId)
        .then((res) => setInstanceStats(connectorId, res.data))
        .catch(() => {});
    },
    [upsertConnectorInstance, setInstanceStats]
  );

  /** Re-sync catalog lists without toggling page `isLoading` (e.g. after sync toggle). */
  const refreshConnectorsListsQuiet = useCallback(async () => {
    const { registry, active } = await fetchFilteredConnectorLists('team');
    if (registry) setRegistryConnectors(registry);
    if (active) setActiveConnectors(active);
  }, [setRegistryConnectors, setActiveConnectors]);

  // ── Handlers (list view) ───────────────────────────────────
  const handleSetup = useCallback(
    (connector: Connector) => {
      // For active connectors (have _key), open in edit mode
      // For registry connectors (no _key), open in create mode
      const connectorId = connector._key;
      openPanel(connector, connectorId, 'team');
    },
    [openPanel]
  );

  /** "+" on catalog cards must create a new instance, not edit whichever instance supplied `_key`. */
  const handleAddInstanceFromCatalog = useCallback(
    (connector: Connector) => {
      const registry = registryConnectors.find((c) => c.type === connector.type);
      const base = registry ?? connector;
      const { _key: _omitInstanceKey, ...template } = base;
      openPanel(template, undefined, 'team');
    },
    [registryConnectors, openPanel]
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
    bumpCatalogRefresh();
    router.push('/workspace/connectors/team/');
  }, [router, setConnectorTypeInfo, clearInstanceData, bumpCatalogRefresh]);

  const handleAddInstance = useCallback(() => {
    if (!connectorTypeInfo) return;
    const registry = registryConnectors.find((c) => c.type === connectorTypeInfo.type);
    const base = registry ?? connectorTypeInfo;
    const { _key: _omitInstanceKey, ...template } = base;
    openPanel(template, undefined, 'team');
  }, [connectorTypeInfo, registryConnectors, openPanel]);

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
        await ensureConnectorSyncActiveThenResync({
          _key: instance._key,
          type: instance.type,
        });
        addToast({
          variant: 'success',
          title: `${connectorTypeInfo?.name ?? 'Connector'} is now syncing`,
          description: 'Your records will be available shortly.',
          duration: 3000,
        });
        await refreshConnectorRowQuiet(instance._key);
      } catch {
        addToast({
          variant: 'error',
          title: 'Failed to start sync',
        });
      }
    },
    [connectorTypeInfo, addToast, refreshConnectorRowQuiet]
  );

  const handleToggleSyncActive = useCallback(
    async (instance: ConnectorInstance) => {
      if (!instance._key) return;
      try {
        await ConnectorsApi.toggleConnector(instance._key, 'sync');
        addToast({
          variant: 'success',
          title: instance.isActive ? 'Connector sync disabled' : 'Connector sync enabled',
          duration: 2500,
        });
        await refreshConnectorRowQuiet(instance._key);
        await refreshConnectorsListsQuiet();
      } catch {
        addToast({
          variant: 'error',
          title: 'Could not update connector',
        });
      }
    },
    [addToast, refreshConnectorRowQuiet, refreshConnectorsListsQuiet]
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
      await ensureConnectorSyncActiveThenResync({ _key: instanceId });
      addToast({
        variant: 'success',
        title: `Your ${connectorTypeInfo?.name ?? 'connector'} instance is now syncing`,
        description:
          'This may take a few minutes. You\'ll be notified when it\'s done.',
        duration: 3000,
      });
      await refreshConnectorRowQuiet(instanceId);
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
    refreshConnectorRowQuiet,
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
          onToggleSyncActive={handleToggleSyncActive}
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
        onAddInstance={handleAddInstanceFromCatalog}
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
        padding: '0 12px',
        font: 'inherit',
        outline: 'none',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        height: 32,
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
    <ServiceGate services={['connector']}>
      <Suspense>
        <TeamConnectorsAccessGate />
      </Suspense>
    </ServiceGate>
  );
}



'use client';

import { useEffect, useLayoutEffect, useCallback, useMemo, useState, Suspense, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { isElectron } from '@/lib/utils/api-base-url';
import { isLocalFsConnectorType } from '../utils/local-fs-helpers';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import { startConnectorSync } from '../utils/connector-sync-actions';
import { filterConnectorsForScope } from '../utils/filter-connectors-by-scope';
import { fetchFilteredConnectorLists } from '../utils/fetch-filtered-connector-lists';
import { useAuthStore } from '@/lib/store/auth-store';
import {
  buildLocalFsWatcherOptionsFromConnectorConfig,
  buildLocalSyncScheduleFromConnectorConfig,
  extractLocalFsRootPath,
  startElectronLocalSync,
  stopElectronLocalSync,
  getElectronLocalSyncStatus,
  replayElectronLocalSync,
} from '../utils/electron-local-sync';
import {
  ConnectorCatalogLayout,
  ConnectorPanel,
  ConnectorDetailsLayout,
  InstanceManagementPanel,
  ConfigSuccessDialog,
} from '../components';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import type {
  Connector,
  ConnectorInstance,
  TeamFilterTab,
  ConnectorConfig,
} from '../types';

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
  const { t } = useTranslation();

  const teamTabs = [
    { value: 'all', label: t('workspace.actions.tabs.all') },
    { value: 'configured', label: t('workspace.actions.tabs.configured') },
    { value: 'not_configured', label: t('workspace.actions.tabs.notConfigured') },
  ];

  const accessToken = useAuthStore((s) => s.accessToken);
  const managedWatcherIdsRef = useRef<Set<string>>(new Set());

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
    localSyncStatuses,
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
    setLocalSyncStatus,
    clearLocalSyncStatus,
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

  const ensureLocalWatcherForInstance = useCallback(
    async (instance: ConnectorInstance, config?: ConnectorConfig | null) => {
      if (!instance._key) return;
      if (!isElectron()) return;
      if (!isLocalFsConnectorType(instance.type)) return;
      if (!instance.isActive || !instance.isConfigured || !instance.isAuthenticated) {
        await stopElectronLocalSync(instance._key);
        managedWatcherIdsRef.current.delete(instance._key);
        clearLocalSyncStatus(instance._key);
        return;
      }
      if (!accessToken) return;
      const rootPath = extractLocalFsRootPath(config);
      if (!rootPath) return;

      await startElectronLocalSync({
        connectorId: instance._key,
        connectorName: instance.name,
        rootPath,
        accessToken,
        ...buildLocalFsWatcherOptionsFromConnectorConfig(config),
        ...buildLocalSyncScheduleFromConnectorConfig(config, instance.type),
      });
      await replayElectronLocalSync(instance._key);
      const status = await getElectronLocalSyncStatus(instance._key);
      if (status) {
        setLocalSyncStatus(instance._key, status);
        managedWatcherIdsRef.current.add(instance._key);
      }
    },
    [accessToken, setLocalSyncStatus, clearLocalSyncStatus]
  );

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
        setError(t('workspace.connectors.toasts.loadError'));
        addToast({
          variant: 'error',
          title: t('workspace.connectors.toasts.loadError'),
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

    const currentInstanceIds = new Set(
      typeInstances.map((instance) => instance._key).filter(Boolean) as string[]
    );
    for (const watcherId of Array.from(managedWatcherIdsRef.current)) {
      if (!currentInstanceIds.has(watcherId)) {
        stopElectronLocalSync(watcherId);
        managedWatcherIdsRef.current.delete(watcherId);
        clearLocalSyncStatus(watcherId);
      }
    }

    setInstances(typeInstances);
  }, [
    connectorType,
    activeConnectors,
    registryConnectors,
    setConnectorTypeInfo,
    setInstances,
    clearLocalSyncStatus,
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
              const instanceRow = activeConnectors.find(
                (c) => c._key === id && c.type === connectorType
              ) as ConnectorInstance | undefined;
              if (instanceRow) {
                await ensureLocalWatcherForInstance(instanceRow, configRes.value);
              }
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
    activeConnectors,
    setIsLoadingInstances,
    setInstanceConfig,
    setInstanceStats,
    ensureLocalWatcherForInstance,
    clearLocalSyncStatus,
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

  useEffect(() => {
    if (!isElectron() || !connectorTypeInfo || !isLocalFsConnectorType(connectorTypeInfo.type)) {
      return;
    }

    const syncStatuses = async () => {
      const ids = Array.from(managedWatcherIdsRef.current);
      await Promise.all(
        ids.map(async (id) => {
          const status = await getElectronLocalSyncStatus(id);
          if (status) setLocalSyncStatus(id, status);
        })
      );
    };

    syncStatuses();
    const timer = setInterval(syncStatuses, 4000);
    return () => clearInterval(timer);
  }, [connectorTypeInfo, setLocalSyncStatus]);

  // ── Handlers (list view) ───────────────────────────────────
  const handleSetup = useCallback(
    (connector: Connector) => {
      if (isLocalFsConnectorType(connector.type) && !isElectron()) {
        addToast({
          variant: 'info',
          title: 'Desktop app required',
          description:
            'Local filesystem connector is only available in the PipesHub desktop app. Please use the desktop app to set up this connector.',
          duration: 5000,
        });
        return;
      }
      // For active connectors (have _key), open in edit mode
      // For registry connectors (no _key), open in create mode
      const connectorId = connector._key;
      openPanel(connector, connectorId, 'team');
    },
    [openPanel, addToast]
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
    if (isLocalFsConnectorType(connectorTypeInfo.type) && !isElectron()) {
      addToast({
        variant: 'info',
        title: 'Desktop app required',
        description:
          'Local filesystem connector is only available in the PipesHub desktop app. Please use the desktop app to set up this connector.',
        duration: 5000,
      });
      return;
    }
    const registry = registryConnectors.find((c) => c.type === connectorTypeInfo.type);
    const base = registry ?? connectorTypeInfo;
    const { _key: _omitInstanceKey, ...template } = base;
    openPanel(template, undefined, 'team');
  }, [connectorTypeInfo, registryConnectors, openPanel, addToast]);

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
      if (!instance._key || instance.status === CONNECTOR_INSTANCE_STATUS.DELETING) return;
      try {
        await startConnectorSync({
          _key: instance._key,
          type: instance.type,
        });
        const configRes = await ConnectorsApi.getConnectorConfig(instance._key);
        setInstanceConfig(instance._key, configRes);
        await ensureLocalWatcherForInstance(instance, configRes);
        addToast({
          variant: 'success',
          title: t('workspace.connectors.toasts.syncStarted', { name: connectorTypeInfo?.name ?? 'Connector' }),
          description: t('workspace.connectors.toasts.syncStartedDescription'),
          duration: 3000,
        });
        await refreshConnectorRowQuiet(instance._key);
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.connectors.toasts.syncError'),
        });
      }
    },
    [
      connectorTypeInfo,
      addToast,
      refreshConnectorRowQuiet,
      setInstanceConfig,
      ensureLocalWatcherForInstance,
    ]
  );

  const handleToggleSyncActive = useCallback(
    async (instance: ConnectorInstance) => {
      if (!instance._key || instance.status === CONNECTOR_INSTANCE_STATUS.DELETING) return;
      try {
        await ConnectorsApi.toggleConnector(instance._key, 'sync');
        addToast({
          variant: 'success',
          title: instance.isActive ? 'Connector sync disabled' : 'Connector sync enabled',
          duration: 2500,
        });
        await refreshConnectorRowQuiet(instance._key);
        const configRes = await ConnectorsApi.getConnectorConfig(instance._key);
        setInstanceConfig(instance._key, configRes);
        await ensureLocalWatcherForInstance(instance, configRes);
        await refreshConnectorsListsQuiet();
      } catch {
        addToast({
          variant: 'error',
          title: 'Could not update connector',
        });
      }
    },
    [
      addToast,
      refreshConnectorRowQuiet,
      refreshConnectorsListsQuiet,
      setInstanceConfig,
      ensureLocalWatcherForInstance,
    ]
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
      await startConnectorSync({ _key: instanceId, type: connectorTypeInfo?.type });
      const instance = activeConnectors.find((item) => item._key === instanceId) as
        | ConnectorInstance
        | undefined;
      if (instance) {
        const configRes = await ConnectorsApi.getConnectorConfig(instanceId);
        setInstanceConfig(instanceId, configRes);
        await ensureLocalWatcherForInstance(instance, configRes);
      }
      addToast({
        variant: 'success',
        title: t('workspace.connectors.toasts.syncStarted', { name: connectorTypeInfo?.name ?? 'connector' }),
        description: t('workspace.connectors.toasts.syncStartedLongDescription'),
        duration: 3000,
      });
      await refreshConnectorRowQuiet(instanceId);
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.connectors.toasts.syncError'),
      });
    }
  }, [
    newlyConfiguredConnectorId,
    connectorTypeInfo,
    activeConnectors,
    addToast,
    refreshConnectorRowQuiet,
    setInstanceConfig,
    ensureLocalWatcherForInstance,
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
          scopeLabel={t('workspace.sidebar.nav.connectors')}
          instances={instances}
          instanceConfigs={instanceConfigs}
          instanceStats={instanceStats}
          localSyncStatuses={localSyncStatuses}
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
        title={t('workspace.sidebar.nav.connectors')}
        subtitle={t('workspace.connectors.subtitle')}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        tabs={teamTabs}
        activeTab={teamFilterTab}
        onTabChange={handleTabChange}
        trailingAction={
          <NavigateButton
            label={t('workspace.sidebar.nav.yourConnectors')}
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
        padding: '0 var(--space-3)',
        font: 'inherit',
        outline: 'none',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-2)',
        height: 'var(--space-6)',
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



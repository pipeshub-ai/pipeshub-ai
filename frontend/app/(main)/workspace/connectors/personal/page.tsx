'use client';

import { useEffect, useLayoutEffect, useCallback, useMemo, useState, Suspense, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { isElectron } from '@/lib/electron';
import { isLocalFsConnectorType } from '../utils/local-fs-helpers';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import {
  isConnectorSyncInProgressError,
  isConnectorSyncLockedError,
  startConnectorSync,
} from '../utils/connector-sync-actions';
import { useSyncConflictGuard } from '../utils/use-sync-conflict-guard';
import { filterConnectorsForScope } from '../utils/filter-connectors-by-scope';
import { fetchFilteredConnectorLists } from '../utils/fetch-filtered-connector-lists';
import {
  refreshAllConnectorInstances,
  refreshConnectorInstanceDetails,
} from '../utils/refresh-instance-details';
import {
  stopElectronLocalSync,
  getElectronLocalSyncStatus,
} from '../utils/electron-local-sync';
import { useEnsureLocalWatcher } from '../utils/use-ensure-local-watcher';
import {
  ConnectorCatalogLayout,
  ConnectorPanel,
  ConnectorDetailsLayout,
  InstanceManagementPanel,
  ConfigSuccessDialog,
} from '../components';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { getConnectorDocumentationUrl } from '../utils/connector-metadata';
import { useResolvedConnectorTypeParam } from '../utils/resolve-connector-type-param';
import type {
  Connector,
  ConnectorInstance,
  PersonalFilterTab,
} from '../types';

const LOCAL_FS_DESKTOP_REQUIRED_TOAST_DURATION_MS = 5000;

// ========================================
// Page
// ========================================

function PersonalConnectorsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const addToast = useToastStore((s) => s.addToast);
  const { t } = useTranslation();

  const personalTabs = [
    { value: 'all', label: t('workspace.actions.tabs.all') },
    { value: 'active', label: t('status.active') },
    { value: 'inactive', label: t('status.inactive') },
  ];

  const managedWatcherIdsRef = useRef<Set<string>>(new Set());
  const [isRefreshingAllInstances, setIsRefreshingAllInstances] = useState(false);

  const {
    registryConnectors,
    activeConnectors,
    searchQuery,
    personalFilterTab,
    isLoading,
    instances,
    isLoadingInstances,
    connectorTypeInfo,
    showConfigSuccessDialog,
    newlyConfiguredConnectorId,
    instanceConfigs,
    setRegistryConnectors,
    setActiveConnectors,
    setSearchQuery,
    setPersonalFilterTab,
    setIsLoading,
    setError,
    openPanel,
    setInstances,
    setIsLoadingInstances,
    setConnectorTypeInfo,
    setInstanceConfig,
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

  const { connectorType, showConnectorTypePage } = useResolvedConnectorTypeParam(
    searchParams,
    registryConnectors,
    activeConnectors,
    '/workspace/connectors/personal/',
  );

  // Keep catalog scope in store aligned with this route (panel + API use `selectedScope`).
  useLayoutEffect(() => {
    setSelectedScope('personal');
  }, [setSelectedScope]);

  const ensureLocalWatcherForInstance = useEnsureLocalWatcher(managedWatcherIdsRef);
  const showLocalFsDesktopRequiredToast = useCallback(() => {
    addToast({
      variant: 'info',
      title: t('workspace.connectors.personal.desktopRequiredTitle'),
      description: t('workspace.connectors.personal.desktopRequiredDescription'),
      duration: LOCAL_FS_DESKTOP_REQUIRED_TOAST_DURATION_MS,
    });
  }, [addToast, t]);

  // ── URL → Store: sync tab from query param ───────────────────
  useEffect(() => {
    const tab = searchParams.get('tab') as PersonalFilterTab | null;
    const validTabs: PersonalFilterTab[] = ['all', 'active', 'inactive'];
    if (tab && validTabs.includes(tab)) {
      setPersonalFilterTab(tab);
    } else {
      setPersonalFilterTab('all');
    }
  }, [searchParams, setPersonalFilterTab]);

  // ── Fetch connector list data ───────────────────────────────
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [registryRes, activeRes] = await Promise.allSettled([
        ConnectorsApi.getRegistryConnectors('personal'),
        ConnectorsApi.getActiveConnectors('personal'),
      ]);

      if (registryRes.status === 'fulfilled') {
        setRegistryConnectors(
          filterConnectorsForScope(registryRes.value.connectors, 'personal')
        );
      }
      if (activeRes.status === 'fulfilled') {
        setActiveConnectors(filterConnectorsForScope(activeRes.value.connectors, 'personal'));
      }

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
        void stopElectronLocalSync(watcherId);
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

  // ── Fetch config when instance set or catalog refresh changes (full loader) ──
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
    const isLocalFs = isLocalFsConnectorType(connectorType);

    const run = async () => {
      setIsLoadingInstances(true);
      try {
        await Promise.allSettled(
          instanceIds.map(async (id) => {
            const config = await ConnectorsApi.getConnectorConfig(id).catch(() => null);
            if (cancelled) return;
            if (config) {
              setInstanceConfig(id, config);
              if (isLocalFs) {
                const instanceRow = activeConnectors.find(
                  (c) => c._key === id && c.type === connectorType
                ) as ConnectorInstance | undefined;
                if (instanceRow) {
                  await ensureLocalWatcherForInstance(instanceRow, config);
                }
              }
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
    ensureLocalWatcherForInstance,
  ]);

  const refreshInstanceDetailsForPage = useCallback(
    async (connectorId: string) => {
      const isLocalFs = Boolean(connectorType && isLocalFsConnectorType(connectorType));
      return refreshConnectorInstanceDetails(connectorId, {
        afterConfig: isLocalFs
          ? async (fresh, config) => {
              await ensureLocalWatcherForInstance(fresh, config);
            }
          : undefined,
      });
    },
    [connectorType, ensureLocalWatcherForInstance]
  );

  const refreshConnectorRowQuiet = useCallback(
    async (connectorId: string) => refreshInstanceDetailsForPage(connectorId),
    [refreshInstanceDetailsForPage]
  );

  /** Re-sync catalog lists without toggling page `isLoading` (e.g. after sync toggle). */
  const refreshConnectorsListsQuiet = useCallback(async () => {
    const { registry, active } = await fetchFilteredConnectorLists('personal');
    if (registry) setRegistryConnectors(registry);
    if (active) setActiveConnectors(active);
  }, [setRegistryConnectors, setActiveConnectors]);

  const handleRefreshAllInstances = useCallback(async () => {
    const instanceIds = instanceDetailKeys.split('|').filter(Boolean);
    if (instanceIds.length === 0 || isRefreshingAllInstances) return;

    setIsRefreshingAllInstances(true);
    try {
      await refreshConnectorsListsQuiet();
      await refreshAllConnectorInstances(instanceIds, refreshInstanceDetailsForPage);
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.connectors.toasts.refreshInstancesError'),
      });
    } finally {
      setIsRefreshingAllInstances(false);
    }
  }, [
    instanceDetailKeys,
    isRefreshingAllInstances,
    refreshConnectorsListsQuiet,
    refreshInstanceDetailsForPage,
    addToast,
    t,
  ]);

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

    void syncStatuses();
    const timer = setInterval(syncStatuses, 4000);
    return () => clearInterval(timer);
  }, [connectorTypeInfo, setLocalSyncStatus]);

  // ── Handlers (list view) ───────────────────────────────────
  const handleSetup = useCallback(
    (connector: Connector) => {
      if (isLocalFsConnectorType(connector.type) && !isElectron()) {
        showLocalFsDesktopRequiredToast();
        return;
      }
      const connectorId = connector._key;
      openPanel(connector, connectorId, 'personal');
    },
    [openPanel, showLocalFsDesktopRequiredToast]
  );

  const handleAddInstanceFromCatalog = useCallback(
    (connector: Connector) => {
      const registry = registryConnectors.find((c) => c.type === connector.type);
      const base = registry ?? connector;
      const { _key: _omitInstanceKey, ...template } = base;
      openPanel(template, undefined, 'personal');
    },
    [registryConnectors, openPanel]
  );

  const handleCardClick = useCallback(
    (connector: Connector) => {
      router.push(
        `/workspace/connectors/personal/?connectorType=${encodeURIComponent(connector.type)}`
      );
    },
    [router]
  );

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
          ? `/workspace/connectors/personal/?${query}`
          : '/workspace/connectors/personal/'
      );
    },
    [router, searchParams]
  );

  // ── Handlers (type page view) ──────────────────────────────
  const handleBackToList = useCallback(() => {
    setConnectorTypeInfo(null);
    clearInstanceData();
    bumpCatalogRefresh();
    router.push('/workspace/connectors/personal/');
  }, [router, setConnectorTypeInfo, clearInstanceData, bumpCatalogRefresh]);

  const handleAddInstance = useCallback(() => {
    if (!connectorTypeInfo) return;
    if (isLocalFsConnectorType(connectorTypeInfo.type) && !isElectron()) {
      showLocalFsDesktopRequiredToast();
      return;
    }
    const registry = registryConnectors.find((c) => c.type === connectorTypeInfo.type);
    const base = registry ?? connectorTypeInfo;
    const { _key: _omitInstanceKey, ...template } = base;
    openPanel(template, undefined, 'personal');
  }, [connectorTypeInfo, registryConnectors, openPanel, showLocalFsDesktopRequiredToast]);

  const handleOpenDocs = useCallback(() => {
    const docUrl = getConnectorDocumentationUrl(connectorTypeInfo);
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
        if (isLocalFsConnectorType(instance.type)) {
          const fresh = await refreshConnectorRowQuiet(instance._key);
          let config = instanceConfigs[instance._key];
          if (!config) {
            config = await ConnectorsApi.getConnectorConfig(instance._key);
            setInstanceConfig(instance._key, config);
          }
          await ensureLocalWatcherForInstance(fresh, config);
        } else {
          await refreshConnectorRowQuiet(instance._key);
        }
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
      ensureLocalWatcherForInstance,
      instanceConfigs,
      setInstanceConfig,
    ]
  );

  const handleInstanceChevron = useCallback(
    (instance: ConnectorInstance) => {
      openInstancePanel(instance);
    },
    [openInstancePanel]
  );

  // ── Success dialog handlers ─────────────────────────────────
  const { guard: syncConflictGuard, dialog: syncConflictDialog } = useSyncConflictGuard();

  const handleStartSyncingFromDialog = useCallback(async () => {
    setShowConfigSuccessDialog(false);
    const instanceId = newlyConfiguredConnectorId;
    setNewlyConfiguredConnectorId(null);
    if (!instanceId) return;

    const doStartSync = async (force: boolean) => {
      try {
        // startConnectorSync already refreshes the instance row + config.
        await startConnectorSync(
          { _key: instanceId, type: connectorTypeInfo?.type },
          { force }
        );
        if (isLocalFsConnectorType(connectorTypeInfo?.type ?? '')) {
          const state = useConnectorsStore.getState();
          const fresh =
            state.activeConnectors.find((c) => c._key === instanceId) ??
            state.instances.find((c) => c._key === instanceId);
          let config = state.instanceConfigs[instanceId] ?? instanceConfigs[instanceId];
          if (fresh && !config) {
            config = await ConnectorsApi.getConnectorConfig(instanceId);
            setInstanceConfig(instanceId, config);
          }
          if (fresh && config) {
            await ensureLocalWatcherForInstance(fresh, config);
          }
        }
        addToast({
          variant: 'success',
          title: t('workspace.connectors.toasts.syncStarted', { name: connectorTypeInfo?.name ?? 'connector' }),
          description: t('workspace.connectors.toasts.syncStartedLongDescription'),
          duration: 3000,
        });
      } catch (err) {
        // The guard turns these into a restart prompt or a wait toast.
        if (isConnectorSyncInProgressError(err) || isConnectorSyncLockedError(err)) {
          throw err;
        }
        addToast({
          variant: 'error',
          title: t('workspace.connectors.toasts.syncError'),
        });
      }
    };

    const currentStatus = instances.find((i) => i._key === instanceId)?.status;
    await syncConflictGuard(doStartSync, {
      requestedFullSync: false,
      currentStatus,
    });
  }, [
    newlyConfiguredConnectorId,
    connectorTypeInfo,
    addToast,
    setInstanceConfig,
    ensureLocalWatcherForInstance,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    instanceConfigs,
    instances,
    syncConflictGuard,
    t,
  ]);

  const handleDoLater = useCallback(() => {
    setShowConfigSuccessDialog(false);
    setNewlyConfiguredConnectorId(null);
  }, [setShowConfigSuccessDialog, setNewlyConfiguredConnectorId]);

  // ── Render ─────────────────────────────────────────────────
  if (showConnectorTypePage) {
    return (
      <>
        <ConnectorDetailsLayout
          connector={connectorTypeInfo}
          scope="personal"
          scopeLabel={t('workspace.sidebar.nav.connectors')}
          instances={instances}
          instanceConfigs={instanceConfigs}
          isLoading={isLoadingInstances}
          onBack={handleBackToList}
          onAddInstance={handleAddInstance}
          onOpenDocs={handleOpenDocs}
          onManageInstance={handleManageInstance}
          onToggleSyncActive={handleToggleSyncActive}
          onInstanceChevron={handleInstanceChevron}
          onRefreshAll={handleRefreshAllInstances}
          isRefreshingAll={isRefreshingAllInstances}
          onRefreshInstance={(instance) =>
            instance._key
              ? refreshInstanceDetailsForPage(instance._key)
              : Promise.resolve()
          }
        />
        <ConnectorPanel />
        <InstanceManagementPanel />
        <ConfigSuccessDialog
          open={showConfigSuccessDialog}
          connectorName={connectorTypeInfo?.name ?? ''}
          onStartSyncing={handleStartSyncingFromDialog}
          onDoLater={handleDoLater}
        />
        {syncConflictDialog}
      </>
    );
  }

  return (
    <>
      <ConnectorCatalogLayout
        title={t('workspace.sidebar.nav.yourConnectors')}
        subtitle={t('workspace.connectors.subtitle')}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        tabs={personalTabs}
        activeTab={personalFilterTab}
        onTabChange={handleTabChange}
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

export default function PersonalConnectorsPage() {
  return (
    <ServiceGate services={['connector']}>
      <Suspense>
        <PersonalConnectorsPageContent />
      </Suspense>
    </ServiceGate>
  );
}

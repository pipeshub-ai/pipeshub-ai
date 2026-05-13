'use client';

import { useEffect, useLayoutEffect, useCallback, useMemo, Suspense, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { isElectron } from '@/lib/electron';
import { isLocalFsConnectorType } from '../utils/local-fs-helpers';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import { startConnectorSync } from '../utils/connector-sync-actions';
import { filterConnectorsForScope } from '../utils/filter-connectors-by-scope';
import { fetchFilteredConnectorLists } from '../utils/fetch-filtered-connector-lists';
import { shouldRemoveScheduledCrawlingJobOnSyncDisable } from '../utils/scheduled-crawling';
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

  // The connectorType query param determines whether we show the instance page
  const connectorType = searchParams.get('connectorType');

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
    instanceStats,
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
    const isLocalFs = isLocalFsConnectorType(connectorType);

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
              if (isLocalFs) {
                const instanceRow = activeConnectors.find(
                  (c) => c._key === id && c.type === connectorType
                ) as ConnectorInstance | undefined;
                if (instanceRow) {
                  await ensureLocalWatcherForInstance(instanceRow, configRes.value);
                }
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
  ]);

  const refreshConnectorRowQuiet = useCallback(
    async (connectorId: string) => {
      const fresh = await ConnectorsApi.getConnectorInstance(connectorId);
      upsertConnectorInstance(fresh);
      void ConnectorsApi.getConnectorStats(connectorId)
        .then((res) => setInstanceStats(connectorId, res.data))
        .catch(() => {});
      return fresh;
    },
    [upsertConnectorInstance, setInstanceStats]
  );

  /** Re-sync catalog lists without toggling page `isLoading` (e.g. after sync toggle). */
  const refreshConnectorsListsQuiet = useCallback(async () => {
    const { registry, active } = await fetchFilteredConnectorLists('personal');
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
        let connectorConfig = instanceConfigs[instance._key];
        const isDisablingLocalFs = instance.isActive && isLocalFsConnectorType(instance.type);
        if (isDisablingLocalFs) {
          try {
            connectorConfig = await ConnectorsApi.getConnectorConfig(instance._key);
            setInstanceConfig(instance._key, connectorConfig);
          } catch (error) {
            console.warn(
              `[connectors:${instance._key}] failed to refresh config before scheduled cleanup check:`,
              error instanceof Error ? error.message : error,
            );
          }
        }
        const removeScheduledCrawlingJob =
          shouldRemoveScheduledCrawlingJobOnSyncDisable(instance, connectorConfig);

        await ConnectorsApi.toggleConnector(instance._key, 'sync');
        let scheduledCrawlingCleanupFailed = false;
        if (removeScheduledCrawlingJob) {
          try {
            await ConnectorsApi.removeScheduledCrawlingJob(instance.type, instance._key);
          } catch (error) {
            scheduledCrawlingCleanupFailed = true;
            console.warn(
              `[connectors:${instance._key}] scheduled crawling cleanup failed:`,
              error instanceof Error ? error.message : error,
            );
          }
        }
        addToast({
          variant: scheduledCrawlingCleanupFailed ? 'warning' : 'success',
          title: instance.isActive ? 'Connector sync disabled' : 'Connector sync enabled',
          ...(scheduledCrawlingCleanupFailed
            ? {
                description:
                  'Scheduled crawling cleanup failed. Please try disabling again if scheduled sync still runs.',
              }
            : {}),
          duration: 2500,
        });
        if (isLocalFsConnectorType(instance.type)) {
          const fresh = await refreshConnectorRowQuiet(instance._key);
          await ensureLocalWatcherForInstance(fresh, instanceConfigs[instance._key]);
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
  const handleStartSyncingFromDialog = useCallback(async () => {
    setShowConfigSuccessDialog(false);
    const instanceId = newlyConfiguredConnectorId;
    setNewlyConfiguredConnectorId(null);
    if (!instanceId) return;

    try {
      await startConnectorSync({ _key: instanceId, type: connectorTypeInfo?.type });
      if (isLocalFsConnectorType(connectorTypeInfo?.type ?? '')) {
        const fresh = await refreshConnectorRowQuiet(instanceId);
        let config = instanceConfigs[instanceId];
        if (!config) {
          config = await ConnectorsApi.getConnectorConfig(instanceId);
          setInstanceConfig(instanceId, config);
        }
        await ensureLocalWatcherForInstance(fresh, config);
      } else {
        await refreshConnectorRowQuiet(instanceId);
      }
      addToast({
        variant: 'success',
        title: t('workspace.connectors.toasts.syncStarted', { name: connectorTypeInfo?.name ?? 'connector' }),
        description: t('workspace.connectors.toasts.syncStartedLongDescription'),
        duration: 3000,
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.connectors.toasts.syncError'),
      });
    }
  }, [
    newlyConfiguredConnectorId,
    connectorTypeInfo,
    addToast,
    refreshConnectorRowQuiet,
    setInstanceConfig,
    ensureLocalWatcherForInstance,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    instanceConfigs,
    t,
  ]);

  const handleDoLater = useCallback(() => {
    setShowConfigSuccessDialog(false);
    setNewlyConfiguredConnectorId(null);
  }, [setShowConfigSuccessDialog, setNewlyConfiguredConnectorId]);

  // ── Render ─────────────────────────────────────────────────
  if (connectorType) {
    return (
      <>
        <ConnectorDetailsLayout
          connector={connectorTypeInfo}
          scope="personal"
          scopeLabel={t('workspace.sidebar.nav.connectors')}
          instances={instances}
          instanceConfigs={instanceConfigs}
          instanceStats={instanceStats}
          isLoading={isLoadingInstances}
          onBack={handleBackToList}
          onAddInstance={handleAddInstance}
          onOpenDocs={handleOpenDocs}
          onManageInstance={handleManageInstance}
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

'use client';

import { useEffect, useLayoutEffect, useCallback, useMemo, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
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
import type { Connector, ConnectorInstance, PersonalFilterTab } from '../types';

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
    const { registry, active } = await fetchFilteredConnectorLists('personal');
    if (registry) setRegistryConnectors(registry);
    if (active) setActiveConnectors(active);
  }, [setRegistryConnectors, setActiveConnectors]);

  // ── Handlers (list view) ───────────────────────────────────
  const handleSetup = useCallback(
    (connector: Connector) => {
      const connectorId = connector._key;
      openPanel(connector, connectorId, 'personal');
    },
    [openPanel]
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
    const registry = registryConnectors.find((c) => c.type === connectorTypeInfo.type);
    const base = registry ?? connectorTypeInfo;
    const { _key: _omitInstanceKey, ...template } = base;
    openPanel(template, undefined, 'personal');
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

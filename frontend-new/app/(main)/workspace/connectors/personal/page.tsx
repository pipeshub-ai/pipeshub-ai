'use client';

import { useEffect, useCallback, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
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
    clearInstanceData,
    openInstancePanel,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
  } = useConnectorsStore();

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
        setRegistryConnectors(registryRes.value.connectors);
      }
      if (activeRes.status === 'fulfilled') {
        setActiveConnectors(activeRes.value.connectors);
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
      const connectorId = connector._key;
      openPanel(connector, connectorId);
    },
    [openPanel]
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
    router.push('/workspace/connectors/personal/');
  }, [router, setConnectorTypeInfo, clearInstanceData]);

  const handleAddInstance = useCallback(() => {
    if (!connectorTypeInfo) return;
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
          title: t('workspace.connectors.toasts.syncStarted', { name: connectorTypeInfo?.name ?? 'Connector' }),
          description: t('workspace.connectors.toasts.syncStartedDescription'),
          duration: 3000,
        });
        // Re-fetch active connectors and instance details
        try {
          const activeRes = await ConnectorsApi.getActiveConnectors('personal');
          setActiveConnectors(activeRes.connectors);
        } catch {
          // Silently fail — the list will refresh on next navigation
        }
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.connectors.toasts.syncError'),
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
        title: t('workspace.connectors.toasts.syncStarted', { name: connectorTypeInfo?.name ?? 'connector' }),
        description: t('workspace.connectors.toasts.syncStartedLongDescription'),
        duration: 3000,
      });
      // Re-fetch active connectors to update the list
      try {
        const activeRes = await ConnectorsApi.getActiveConnectors('personal');
        setActiveConnectors(activeRes.connectors);
      } catch {
        // Silently fail
      }
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
    setActiveConnectors,
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
        onCardClick={handleCardClick}
        isLoading={isLoading}
      />
      <ConnectorPanel />
    </>
  );
}

export default function PersonalConnectorsPage() {
  return (
    <Suspense>
      <PersonalConnectorsPageContent />
    </Suspense>
  );
}

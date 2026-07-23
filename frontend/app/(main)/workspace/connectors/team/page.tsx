'use client';

import { useEffect, useLayoutEffect, useCallback, useMemo, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
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
  ConnectorCatalogLayout,
  ConnectorPanel,
  ConnectorDetailsLayout,
  InstanceManagementPanel,
  ConfigSuccessDialog,
} from '../components';
import { AdminAccessRequiredDialog } from '../components/admin-access-required-dialog';
import type { AdminAccessDialogPhase } from '../components/admin-access-required-dialog';
import { shouldPromptAdminAccess } from '../utils/admin-access-helpers';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { getConnectorDocumentationUrl } from '../utils/connector-metadata';
import { useResolvedConnectorTypeParam } from '../utils/resolve-connector-type-param';
import type { Connector, ConnectorInstance, TeamFilterTab } from '../types';

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
    clearInstanceData,
    openInstancePanel,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    catalogRefreshToken,
    bumpCatalogRefresh,
    setSelectedScope,
  } = useConnectorsStore();

  const [adminAccessDialogOpen, setAdminAccessDialogOpen] = useState(false);
  const [adminAccessDialogPhase, setAdminAccessDialogPhase] =
    useState<AdminAccessDialogPhase>('question');
  const [pendingSetupConnector, setPendingSetupConnector] = useState<Connector | null>(null);
  const [pendingSetupConnectorId, setPendingSetupConnectorId] = useState<string | undefined>(
    undefined
  );
  const { connectorType, showConnectorTypePage } = useResolvedConnectorTypeParam(
    searchParams,
    registryConnectors,
    activeConnectors,
    '/workspace/connectors/team/',
  );
  const [isRefreshingAllInstances, setIsRefreshingAllInstances] = useState(false);

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

    const run = async () => {
      setIsLoadingInstances(true);
      try {
        await Promise.allSettled(
          instanceIds.map(async (id) => {
            const configRes = await ConnectorsApi.getConnectorConfig(id).catch(() => null);
            if (cancelled) return;
            if (configRes) {
              setInstanceConfig(id, configRes);
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
  ]);

  const refreshConnectorRowQuiet = useCallback(
    async (connectorId: string) => refreshConnectorInstanceDetails(connectorId),
    []
  );

  const handleRefreshInstanceDetails = useCallback(
    async (connectorId: string) => refreshConnectorInstanceDetails(connectorId),
    []
  );

  /** Re-sync catalog lists without toggling page `isLoading` (e.g. after sync toggle). */
  const refreshConnectorsListsQuiet = useCallback(async () => {
    const { registry, active } = await fetchFilteredConnectorLists('team');
    if (registry) setRegistryConnectors(registry);
    if (active) setActiveConnectors(active);
  }, [setRegistryConnectors, setActiveConnectors]);

  const handleRefreshAllInstances = useCallback(async () => {
    const instanceIds = instanceDetailKeys.split('|').filter(Boolean);
    if (instanceIds.length === 0 || isRefreshingAllInstances) return;

    setIsRefreshingAllInstances(true);
    try {
      await refreshConnectorsListsQuiet();
      await refreshAllConnectorInstances(instanceIds, refreshConnectorInstanceDetails);
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
    addToast,
    t,
  ]);

  const proceedWithSetup = useCallback(
    (connector: Connector, connectorId?: string) => {
      openPanel(connector, connectorId, 'team');
    },
    [openPanel]
  );

  const requestSetupOrPromptAdminAccess = useCallback(
    (connector: Connector, connectorId?: string) => {
      const isCreateMode = connectorId === undefined;

      if (shouldPromptAdminAccess(connector, isCreateMode)) {
        setPendingSetupConnector(connector);
        setPendingSetupConnectorId(connectorId);
        setAdminAccessDialogPhase('question');
        setAdminAccessDialogOpen(true);
        return;
      }

      proceedWithSetup(connector, connectorId);
    },
    [proceedWithSetup]
  );

  const handleAdminAccessConfirm = useCallback(() => {
    if (!pendingSetupConnector) return;
    setAdminAccessDialogOpen(false);
    setAdminAccessDialogPhase('question');
    proceedWithSetup(pendingSetupConnector, pendingSetupConnectorId);
    setPendingSetupConnector(null);
    setPendingSetupConnectorId(undefined);
  }, [pendingSetupConnector, pendingSetupConnectorId, proceedWithSetup]);

  // ── Handlers (list view) ───────────────────────────────────
  const handleSetup = useCallback(
    (connector: Connector) => {
      const connectorId = connector._key;
      requestSetupOrPromptAdminAccess(connector, connectorId);
    },
    [requestSetupOrPromptAdminAccess]
  );

  /** "+" on catalog cards must create a new instance, not edit whichever instance supplied `_key`. */
  const handleAddInstanceFromCatalog = useCallback(
    (connector: Connector) => {
      const registry = registryConnectors.find((c) => c.type === connector.type);
      const base = registry ?? connector;
      const { _key: _omitInstanceKey, ...template } = base;
      requestSetupOrPromptAdminAccess(template, undefined);
    },
    [registryConnectors, requestSetupOrPromptAdminAccess]
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
    requestSetupOrPromptAdminAccess(template, undefined);
  }, [connectorTypeInfo, registryConnectors, requestSetupOrPromptAdminAccess]);

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
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    instances,
    syncConflictGuard,
    t,
  ]);

  const handleDoLater = useCallback(() => {
    setShowConfigSuccessDialog(false);
    setNewlyConfiguredConnectorId(null);
  }, [setShowConfigSuccessDialog, setNewlyConfiguredConnectorId]);

  // ── Render ─────────────────────────────────────────────────
  // If connectorType query param is present, show the connector type page
  if (showConnectorTypePage) {
    return (
      <>
        <ConnectorDetailsLayout
          connector={connectorTypeInfo}
          scope="team"
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
              ? handleRefreshInstanceDetails(instance._key)
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
        <AdminAccessRequiredDialog
          open={adminAccessDialogOpen}
          onOpenChange={setAdminAccessDialogOpen}
          connector={pendingSetupConnector}
          phase={adminAccessDialogPhase}
          onPhaseChange={setAdminAccessDialogPhase}
          onConfirmAdmin={handleAdminAccessConfirm}
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
      <AdminAccessRequiredDialog
        open={adminAccessDialogOpen}
        onOpenChange={setAdminAccessDialogOpen}
        connector={pendingSetupConnector}
        phase={adminAccessDialogPhase}
        onPhaseChange={setAdminAccessDialogPhase}
        onConfirmAdmin={handleAdminAccessConfirm}
      />
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



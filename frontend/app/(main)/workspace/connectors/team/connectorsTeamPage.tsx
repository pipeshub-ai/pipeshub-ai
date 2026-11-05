'use client';

import { useEffect, useLayoutEffect, useCallback, useMemo, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Button } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useFeatureFlagsStore, selectVectorStoreRebuildEnabled } from '@/lib/store/feature-flags-store';
import { useToastStore } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api';
import { ServiceGate } from '@/app/components/ui/service-gate';
import useSWR from 'swr';
import { SWR_NS, SWR_POLICY, writeSwrKey } from '@/lib/api/swr-cache';
import { useConnectorsStore } from '../store';
import { ConnectorsApi } from '../api';
import { ACTIVE_TEAM_CONNECTORS_KEY, fetchActiveTeamConnectors } from '../cache-keys';
import { loadInstanceConfigs } from '../utils/load-instance-configs';
import { fetchFilteredConnectorLists } from '../utils/fetch-filtered-connector-lists';
import {
  startConnectorSync,
  toggleConnectorSyncOn,
} from '../utils/connector-sync-actions';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { localFsDesktopToast } from '../utils/local-fs-helpers';
import { filterConnectorsForScope } from '../utils/filter-connectors-by-scope';
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
import { DemoDataRemovalNotice } from '../demo-data/components';
import type { AdminAccessDialogPhase } from '../components/admin-access-required-dialog';
import { shouldPromptAdminAccess } from '../utils/admin-access-helpers';
import { getTeamVisibilityWarning } from '../utils/team-visibility-warning';
import { TeamVisibilityWarningDialog } from '../components/team-visibility-warning-dialog';
import { TEAM_CONNECTOR_VISIBILITY_WARNINGS } from '@/config';
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
  const isAdmin = useUserStore(selectIsAdmin);
  const { t } = useTranslation();
  const vectorStoreRebuildEnabled = useFeatureFlagsStore(selectVectorStoreRebuildEnabled);

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
    instances,
    connectorTypeInfo,
    showConfigSuccessDialog,
    newlyConfiguredConnectorId,
    instanceConfigs,
    setRegistryConnectors,
    setActiveConnectors,
    setSearchQuery,
    setTeamFilterTab,
    setError,
    openPanel,
    setInstances,
    setConnectorTypeInfo,
    clearInstanceData,
    openInstancePanel,
    setShowConfigSuccessDialog,
    setNewlyConfiguredConnectorId,
    catalogRefreshToken,
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
  // The active list is shared with the demo check (one request per minute); the registry is read here.
  const {
    data: teamList,
    error: teamListError,
    isLoading: isTeamListLoading,
  } = useSWR(ACTIVE_TEAM_CONNECTORS_KEY, fetchActiveTeamConnectors, SWR_POLICY.list60s);

  // The personal page writes the same store field, so the team list is applied on every mount too.
  useEffect(() => {
    if (teamList) setActiveConnectors(filterConnectorsForScope(teamList.connectors ?? [], 'team'));
  }, [teamList, setActiveConnectors]);

  const [isRegistryLoading, setIsRegistryLoading] = useState(true);
  const [registryFailed, setRegistryFailed] = useState(false);
  const isLoading = isRegistryLoading || isTeamListLoading;

  const fetchRegistry = useCallback(async () => {
    setIsRegistryLoading(true);
    setRegistryFailed(false);
    setError(null);
    try {
      const res = await ConnectorsApi.getRegistryConnectors('team');
      setRegistryConnectors(filterConnectorsForScope(res.connectors, 'team'));
    } catch {
      setRegistryFailed(true);
    } finally {
      setIsRegistryLoading(false);
    }
  }, [setRegistryConnectors, setError]);

  useEffect(() => {
    void fetchRegistry();
  }, [fetchRegistry, catalogRefreshToken]);

  const bothListsFailed = registryFailed && Boolean(teamListError);
  useEffect(() => {
    if (!bothListsFailed) return;
    setError(t('workspace.connectors.toasts.loadError'));
    addToast({
      variant: 'error',
      title: t('workspace.connectors.toasts.loadError'),
    });
  }, [bothListsFailed, setError, addToast, t]);

  /** Stable across upserts that only change fields on existing instances (same ids → same string). */
  const instanceDetailKeys = useMemo(() => {
    if (!connectorType) return '';
    return activeConnectors
      .filter((c) => c.type === connectorType && c._key)
      .map((c) => c._key as string)
      .sort()
      .join('|');
  }, [activeConnectors, connectorType]);

  // Read from the team list itself: the store may still hold the personal page's list for a render.
  // An instance deleted here can stay in the server's list for a while; the store already hides it.
  const deletedInstanceIds = useConnectorsStore((s) => s.deletedInstanceIds);
  const teamInstanceKeys = useMemo(() => {
    if (!connectorType || !teamList) return '';
    const deleted = new Set(deletedInstanceIds);
    return filterConnectorsForScope(teamList.connectors ?? [], 'team')
      .filter((c) => c.type === connectorType && c._key && !deleted.has(c._key))
      .map((c) => c._key as string)
      .sort()
      .join('|');
  }, [teamList, connectorType, deletedInstanceIds]);

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

  // ── Instance configs: read for this set of instances at most once a minute; writes invalidate ──
  const { isLoading: isLoadingConfigs } = useSWR(
    connectorType && teamInstanceKeys ? ([SWR_NS.connectorConfigs, teamInstanceKeys] as const) : null,
    ([, ids]) => loadInstanceConfigs(ids.split('|')),
    SWR_POLICY.list60s,
  );

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
    const { registry, active, activeResponse } = await fetchFilteredConnectorLists('team');
    if (registry) setRegistryConnectors(registry);
    if (active) setActiveConnectors(active);
    // The page re-applies the cached list on mount, so the cache must hold this newer answer.
    if (activeResponse) writeSwrKey(ACTIVE_TEAM_CONNECTORS_KEY, activeResponse);
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

  const [visibilityWarningConnector, setVisibilityWarningConnector] = useState<Connector | null>(null);

  const requestSetupOrPromptAdminAccess = useCallback(
    (connector: Connector, connectorId?: string, visibilityAcknowledged = false) => {
      const isCreateMode = connectorId === undefined;

      if (
        isCreateMode &&
        !visibilityAcknowledged &&
        getTeamVisibilityWarning(connector, TEAM_CONNECTOR_VISIBILITY_WARNINGS)
      ) {
        setVisibilityWarningConnector(connector);
        return;
      }

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

  const handleVisibilityWarningContinue = useCallback(() => {
    const connector = visibilityWarningConnector;
    setVisibilityWarningConnector(null);
    if (connector) requestSetupOrPromptAdminAccess(connector, undefined, true);
  }, [visibilityWarningConnector, requestSetupOrPromptAdminAccess]);

  const visibilityWarningDialog = (
    <TeamVisibilityWarningDialog
      open={visibilityWarningConnector !== null}
      onOpenChange={(open) => {
        if (!open) setVisibilityWarningConnector(null);
      }}
      connector={visibilityWarningConnector}
      hasPersonal={
        getTeamVisibilityWarning(visibilityWarningConnector, TEAM_CONNECTOR_VISIBILITY_WARNINGS)?.hasPersonal ?? false
      }
      onContinue={handleVisibilityWarningContinue}
    />
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
    // No catalog refresh: leaving is not a write, and every write on the type page refreshes it already.
    router.push('/workspace/connectors/team/');
  }, [router, setConnectorTypeInfo, clearInstanceData]);

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
        if (!instance.isActive) {
          const outcome = await toggleConnectorSyncOn(instance._key, instance.type);
          if (outcome.kind === 'requires-desktop') {
            addToast(localFsDesktopToast(outcome));
            return;
          }
        } else {
          await ConnectorsApi.toggleConnector(instance._key, 'sync');
        }
        await refreshConnectorRowQuiet(instance._key);
        addToast({
          variant: 'success',
          title: instance.isActive ? 'Connector sync disabled' : 'Connector sync enabled',
          duration: 2500,
        });
        try {
          await refreshConnectorsListsQuiet();
        } catch {
          addToast({
            variant: 'error',
            title: t('workspace.connectors.toasts.refreshInstancesError'),
          });
        }
      } catch (err: unknown) {
        if (!isProcessedError(err)) {
          addToast({ variant: 'error', title: 'Could not update connector' });
        }
        // A failed toggle may still have been committed server-side; show what's really there.
        await refreshConnectorRowQuiet(instance._key).catch(() => {});
      }
    },
    [addToast, refreshConnectorRowQuiet, refreshConnectorsListsQuiet, t]
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
      const outcome = await startConnectorSync({
        _key: instanceId,
        type: connectorTypeInfo?.type,
      });
      if (outcome?.kind === 'requires-desktop') {
        addToast(localFsDesktopToast(outcome));
        await refreshConnectorRowQuiet(instanceId);
        return;
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
  // If connectorType query param is present, show the connector type page
  if (showConnectorTypePage) {
    return (
      <>
        <ConnectorDetailsLayout
          connector={connectorTypeInfo}
          scopeLabel={t('workspace.sidebar.nav.connectors')}
          instances={instances}
          instanceConfigs={instanceConfigs}
          isLoading={isLoadingConfigs}
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
        <AdminAccessRequiredDialog
          open={adminAccessDialogOpen}
          onOpenChange={setAdminAccessDialogOpen}
          connector={pendingSetupConnector}
          phase={adminAccessDialogPhase}
          onPhaseChange={setAdminAccessDialogPhase}
          onConfirmAdmin={handleAdminAccessConfirm}
        />
        {visibilityWarningDialog}
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
        banner={<DemoDataRemovalNotice isAdmin={isAdmin} />}
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
      {visibilityWarningDialog}
    </>
  );
}

// ========================================
// Sub-component: trailing nav button
// ========================================

function NavigateButton({ label, onClick }: { label: string; onClick: () => void }) {
  const isMobile = useIsMobile();
  // Phones get a light text link; wider screens keep the soft button.
  return (
    <Button
      size="2"
      variant={isMobile ? 'ghost' : 'soft'}
      color="gray"
      className="touch-hit"
      onClick={onClick}
      style={{ cursor: 'pointer' }}
    >
      {label}
      <MaterialIcon name="arrow_forward" size={16} color="var(--gray-11)" />
    </Button>
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



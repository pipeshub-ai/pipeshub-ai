'use client';

import { useCallback, useEffect, useMemo, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Flex, IconButton } from '@radix-ui/themes';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { useToastStore } from '@/lib/store/toast-store';
import {
  useFeatureFlagsStore,
  selectActionsEnabled,
  selectFeatureFlagsLoaded,
} from '@/lib/store/feature-flags-store';
import { primaryHttpDocumentationUrl } from '@/app/(main)/agents/agent-builder/components/toolset-agent-auth-helpers';
import {
  ToolsetsApi,
  MAX_TOOLSETS_LIST_LIMIT,
  isPersonalToolset,
  type BuilderSidebarToolset,
  type RegistryToolsetRow,
} from '@/app/(main)/toolsets/api';
import { isNoneAuthType } from '@/app/(main)/workspace/connectors/utils/auth-helpers';
import { useWorkspaceToolsetListRefresh } from '../hooks/use-workspace-toolset-list-refresh';
import { useToolsetTypeInstanceList } from '../hooks/use-toolset-type-instance-list';
import { UserToolsetConfigDialog } from '@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog';
import {
  ActionSetupPanel,
  ActionsCatalogLayout,
  ActionTypeDetailsLayout,
  type ActionInstanceAuthTab,
} from '../components';
import {
  actionCatalogItemToRegistryRow,
  mergedMyToolsetsCatalogFromIncludeRegistry,
  typeDetailRegistryRow,
  type ActionCatalogItem,
  type MyActionsTab,
} from '../types';
import type { ActionCardCta } from '../components/action-card';
import { ApiActionsSection } from '@/app/(main)/workspace/api-toolsets/components';
import { API_ACTION_FILTERS, type ApiActionFilter } from '@/app/(main)/workspace/api-toolsets/toolset-form';
import { isToolsetOAuthSuccessMessageType } from '@/app/(main)/toolsets/oauth/toolset-oauth-window-messages';

/** The setup panel's registry row for editing a personal action. */
function registryRowForInstance(inst: BuilderSidebarToolset): RegistryToolsetRow {
  const type = inst.toolsetType || inst.normalized_name || inst.name;
  return {
    name: type,
    displayName: inst.displayName || type,
    description: inst.description || '',
    category: inst.category || 'app',
    appGroup: '',
    iconPath: inst.iconPath || '',
    supportedAuthTypes: [String(inst.authType || 'NONE').toUpperCase()],
    toolCount: inst.toolCount ?? 0,
    ...(inst.documentationLinks?.length ? { documentationLinks: inst.documentationLinks } : {}),
  };
}

const PERSONAL_CATALOG_TABS: { value: MyActionsTab; labelKey: string }[] = [
  { value: 'all', labelKey: 'workspace.actions.tabs.all' },
  { value: 'authenticated', labelKey: 'workspace.actions.tabs.authenticated' },
  { value: 'not_authenticated', labelKey: 'workspace.actions.tabs.notAuthenticated' },
];

const API_ACTION_FILTER_BY_TAB: Record<MyActionsTab, ApiActionFilter> = {
  all: API_ACTION_FILTERS.all,
  authenticated: API_ACTION_FILTERS.connected,
  not_authenticated: API_ACTION_FILTERS.notConnected,
};

function PersonalActionsPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const addToast = useToastStore((s) => s.addToast);
  const actionsEnabled = useFeatureFlagsStore(selectActionsEnabled);
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const toolsetTypeParam = searchParams.get('toolsetType');
  const instanceTabParam = searchParams.get('instanceTab');
  const instanceFilterTab = useMemo((): ActionInstanceAuthTab => {
    if (instanceTabParam === 'authenticated' || instanceTabParam === 'not_authenticated') {
      return instanceTabParam;
    }
    return 'all';
  }, [instanceTabParam]);

  const [catalogTab, setCatalogTab] = useState<MyActionsTab>(() => {
    const tab = searchParams.get('tab');
    if (tab === 'authenticated' || tab === 'not_authenticated') return tab;
    return 'all';
  });

  useEffect(() => {
    const tab = searchParams.get('tab');
    if (tab === 'authenticated' || tab === 'not_authenticated') setCatalogTab(tab);
    else setCatalogTab('all');
  }, [searchParams]);

  const [myToolsets, setMyToolsets] = useState<BuilderSidebarToolset[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedCatalogSearch, setDebouncedCatalogSearch] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [configureToolset, setConfigureToolset] = useState<BuilderSidebarToolset | null>(null);
  /** Adding a personal action of this type. */
  const [setupRow, setSetupRow] = useState<RegistryToolsetRow | null>(null);
  /** Editing this personal action. */
  const [editAction, setEditAction] = useState<BuilderSidebarToolset | null>(null);
  const { refreshKey, bumpRefreshKey, refreshToolsetLists } = useWorkspaceToolsetListRefresh();
  const {
    instanceSearch,
    setInstanceSearch,
    typeListPage,
    setTypeListPage,
    typeListInstances,
    typeListPagination,
    typeListFilterCounts,
    typeListLoading,
    typeListRefreshing,
  } = useToolsetTypeInstanceList({
    toolsetTypeParam,
    instanceFilterTab,
    refreshKey,
    enabled: Boolean(toolsetTypeParam),
  });
  const [catalogTabCounts, setCatalogTabCounts] = useState<{
    all: number;
    authenticated: number;
    notAuthenticated: number;
  } | null>(null);

  /**
   * Popup posts `oauth-success` to `window` as soon as the callback route finishes.
   * Refresh here so the type-instance list updates even if the nested hook misses `event.source`
   * or the first refetch races the DB commit (we still run the delayed follow-up in `refreshToolsetLists`).
   */
  useEffect(() => {
    const oauthContextOpen = Boolean(toolsetTypeParam || configureToolset?.instanceId);
    if (!oauthContextOpen) return;
    const onMsg = (event: MessageEvent) => {
      if (typeof window === 'undefined' || event.origin !== window.location.origin) return;
      if (!isToolsetOAuthSuccessMessageType(event.data?.type)) return;
      refreshToolsetLists();
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [configureToolset?.instanceId, refreshToolsetLists, toolsetTypeParam]);

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedCatalogSearch(searchQuery.trim()), 300);
    return () => window.clearTimeout(id);
  }, [searchQuery]);

  /** Keep the setup panel in sync when the instance list refetches (toolbar refresh, OAuth, etc.). */
  useEffect(() => {
    setConfigureToolset((prev) => {
      if (!prev?.instanceId) return prev;
      const source = toolsetTypeParam ? typeListInstances : myToolsets;
      const found = source.find((i) => i.instanceId === prev.instanceId);
      if (!found) return prev;
      if (
        prev.isAuthenticated === found.isAuthenticated &&
        prev.displayName === found.displayName &&
        prev.instanceName === found.instanceName &&
        prev.authType === found.authType &&
        prev.oauthConfigId === found.oauthConfigId
      ) {
        return prev;
      }
      return { ...prev, ...found };
    });
  }, [myToolsets, typeListInstances, toolsetTypeParam]);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const authStatus =
        catalogTab === 'authenticated'
          ? ('authenticated' as const)
          : catalogTab === 'not_authenticated'
            ? ('not-authenticated' as const)
            : undefined;
      const { toolsets: myRes, filterCounts } = await ToolsetsApi.getAllMyToolsets({
        includeRegistry: true,
        limitPerPage: MAX_TOOLSETS_LIST_LIMIT,
        search: debouncedCatalogSearch || undefined,
        authStatus,
      });
      setMyToolsets(myRes);
      if (filterCounts) setCatalogTabCounts(filterCounts);
      else setCatalogTabCounts(null);
    } catch {
      addToast({ variant: 'error', title: t('workspace.actions.loadError') });
    } finally {
      setIsLoading(false);
    }
  }, [addToast, t, catalogTab, debouncedCatalogSearch]);

  useEffect(() => {
    if (flagsLoaded && !actionsEnabled) {
      router.replace('/workspace/general');
    }
  }, [flagsLoaded, actionsEnabled, router]);

  useEffect(() => {
    if (toolsetTypeParam || (flagsLoaded && !actionsEnabled)) return;
    void load();
  }, [load, refreshKey, toolsetTypeParam, flagsLoaded, actionsEnabled]);

  useEffect(() => {
    if (!toolsetTypeParam) return;
    setIsLoading(false);
  }, [toolsetTypeParam]);

  const items = useMemo(() => mergedMyToolsetsCatalogFromIncludeRegistry(myToolsets), [myToolsets]);

  /**
   * Type-detail header: toolset metadata from any row of this type (not only the first list row),
   * with fallback to catalog my-toolsets so the title stays the integration name when filters empty.
   */
  /** The type's registry entry, read on its own: a filter can leave the instance list empty. */
  const [typeRegistryEntry, setTypeRegistryEntry] = useState<RegistryToolsetRow | null>(null);
  useEffect(() => {
    setTypeRegistryEntry(null);
    if (!toolsetTypeParam) return;
    const q = toolsetTypeParam.toLowerCase();
    let cancelled = false;
    ToolsetsApi.getRegistryToolsets({
      page: 1,
      limit: MAX_TOOLSETS_LIST_LIMIT,
      search: toolsetTypeParam,
      includeTools: false,
      includeToolCount: true,
      groupByCategory: false,
    })
      .then((res) => {
        if (!cancelled) setTypeRegistryEntry(res.toolsets.find((r) => r.name.toLowerCase() === q) ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [toolsetTypeParam]);

  const typeRegistryRow = useMemo((): RegistryToolsetRow | null => {
    if (!toolsetTypeParam) return null;
    const q = toolsetTypeParam.toLowerCase();
    const primary =
      typeListInstances.find((i) => (i.toolsetType || '').toLowerCase() === q) ??
      myToolsets.find((i) => (i.toolsetType || '').toLowerCase() === q);
    return typeDetailRegistryRow(toolsetTypeParam, primary, typeRegistryEntry);
  }, [toolsetTypeParam, typeListInstances, myToolsets, typeRegistryEntry]);

  const handleInstanceTabChange = useCallback(
    (tab: ActionInstanceAuthTab) => {
      const params = new URLSearchParams(searchParams.toString());
      if (tab === 'all') params.delete('instanceTab');
      else params.set('instanceTab', tab);
      const q = params.toString();
      router.replace(q ? `/workspace/actions/personal/?${q}` : '/workspace/actions/personal/');
    },
    [router, searchParams]
  );

  const typeInstanceFilter = useMemo(() => {
    if (!toolsetTypeParam) return null;
    const counts = typeListFilterCounts ?? { all: 0, authenticated: 0, notAuthenticated: 0 };
    return {
      tab: instanceFilterTab,
      onTabChange: handleInstanceTabChange,
      counts: {
        all: counts.all,
        authenticated: counts.authenticated,
        notAuthenticated: counts.notAuthenticated,
      },
      search: instanceSearch,
      onSearchChange: setInstanceSearch,
      onRefresh: bumpRefreshKey,
      showInfoBanner: true,
    };
  }, [bumpRefreshKey, handleInstanceTabChange, instanceFilterTab, instanceSearch, toolsetTypeParam, typeListFilterCounts]);

  const typeDetailPagination = useMemo(() => {
    if (!typeListPagination || typeListPagination.totalPages <= 1) return null;
    return {
      page: typeListPagination.page,
      totalPages: typeListPagination.totalPages,
      hasNext: typeListPagination.hasNext,
      hasPrev: typeListPagination.hasPrev,
      onPageChange: (p: number) => setTypeListPage(p),
    };
  }, [typeListPagination]);

  const configurePanelDocUrl = useMemo(
    () => primaryHttpDocumentationUrl(configureToolset?.documentationLinks),
    [configureToolset?.documentationLinks]
  );

  const handleTabChange = useCallback(
    (val: string) => {
      const validTab = (val === 'authenticated' || val === 'not_authenticated') ? val : 'all';
      setCatalogTab(validTab as MyActionsTab);
      const params = new URLSearchParams(searchParams.toString());
      params.delete('toolsetType');
      params.delete('instanceTab');
      if (val === 'all') params.delete('tab');
      else params.set('tab', val);
      const q = params.toString();
      router.replace(q ? `/workspace/actions/personal/?${q}` : '/workspace/actions/personal/');
    },
    [router, searchParams]
  );

  const resolveCta = useCallback(
    (item: ActionCatalogItem): { cta: ActionCardCta; label: string } => {
      if (!item.hasOrgInstance) {
        return { cta: 'setup', label: t('workspace.actions.cta.setup') };
      }
      if (!item.isUserAuthenticated) {
        return { cta: 'authenticate', label: t('workspace.actions.cta.authenticate') };
      }
      return { cta: 'configure', label: t('workspace.actions.cta.configure') };
    },
    [t]
  );

  const pushTypeDetail = useCallback(
    (item: ActionCatalogItem) => {
      if (item.rowKind !== 'byToolsetType' || !item.hasOrgInstance) return;
      const params = new URLSearchParams(searchParams.toString());
      params.set('toolsetType', item.toolsetType);
      params.delete('instanceTab');
      const q = params.toString();
      router.push(`/workspace/actions/personal/?${q}`);
    },
    [router, searchParams]
  );

  const handleCta = useCallback(
    (item: ActionCatalogItem) => {
      if (!item.hasOrgInstance || item.rowKind === 'byToolsetType') {
        setSetupRow(actionCatalogItemToRegistryRow(item));
        return;
      }
      if (!item.primaryInstance?.instanceId) return;
      setConfigureToolset(item.primaryInstance);
    },
    []
  );

  const handleCardClick = useCallback(
    (item: ActionCatalogItem) => {
      if (item.rowKind !== 'byToolsetType') return;
      if (item.hasOrgInstance) {
        pushTypeDetail(item);
        return;
      }
      setSetupRow(actionCatalogItemToRegistryRow(item));
    },
    [pushTypeDetail]
  );

  /** A new action is signed in to next, unless there's nothing to sign in to. */
  const handleAdded = useCallback(
    (created?: Record<string, unknown>) => {
      refreshToolsetLists();
      const id = typeof created?._id === 'string' ? created._id : '';
      if (!id || isNoneAuthType(String(created?.authType ?? ''))) return;
      ToolsetsApi.findMyToolsetByInstanceId(id)
        .then((row) => {
          if (row) setConfigureToolset(row);
        })
        .catch(() => undefined);
    },
    [refreshToolsetLists]
  );

  // A new row on each render would reset the open edit form (the panel resets on registryRow).
  const editRegistryRow = useMemo(() => (editAction ? registryRowForInstance(editAction) : null), [editAction]);

  const setupPanel = (
    <ActionSetupPanel
      open={Boolean(setupRow || editAction)}
      scope="personal"
      registryRow={editRegistryRow ?? setupRow}
      editInstance={editAction}
      onOpenChange={(o) => {
        if (o) return;
        setSetupRow(null);
        setEditAction(null);
      }}
      onCreated={(created) => (editAction ? refreshToolsetLists() : handleAdded(created))}
      onDeleted={refreshToolsetLists}
      onNotify={(msg) => addToast({ variant: 'success', title: msg })}
    />
  );

  const handleBackFromType = useCallback(() => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete('toolsetType');
    params.delete('instanceTab');
    const q = params.toString();
    router.replace(q ? `/workspace/actions/personal/?${q}` : '/workspace/actions/personal/');
  }, [router, searchParams]);

  if (flagsLoaded && !actionsEnabled) {
    return null;
  }

  if (toolsetTypeParam) {
    return (
      <>
        <ActionTypeDetailsLayout
          scope="personal"
          registryRow={typeRegistryRow}
          instances={typeListInstances}
          isLoading={typeListLoading}
          listRefreshing={typeListRefreshing}
          onBack={handleBackFromType}
          onAuthenticateInstance={(inst) => setConfigureToolset(inst)}
          onConfigureInstance={(inst) => setConfigureToolset(inst)}
          onAddInstance={typeRegistryRow ? () => setSetupRow(typeRegistryRow) : undefined}
          addInstanceLabel={t('workspace.actions.personal.addOwn')}
          onManageInstance={(inst) => setEditAction(inst)}
          canManageInstance={isPersonalToolset}
          instanceFilter={typeInstanceFilter}
          pagination={typeDetailPagination}
        />
        {setupPanel}
        {configureToolset?.instanceId ? (
          <WorkspaceRightPanel
            open
            onOpenChange={(o) => {
              if (!o) setConfigureToolset(null);
            }}
            title={t('workspace.actions.configPanelTitle')}
            icon={
              <ConnectorIcon
                type={configureToolset.toolsetType || configureToolset.name}
                size={20}
              />
            }
            hideFooter
            headerActions={
              configurePanelDocUrl ? (
                <Flex align="center" gap="1">
                  <IconButton
                    type="button"
                    variant="ghost"
                    color="gray"
                    size="1"
                    aria-label={t('workspace.actions.documentation')}
                    style={{ cursor: 'pointer' }}
                    onClick={() =>
                      window.open(configurePanelDocUrl, '_blank', 'noopener,noreferrer')
                    }
                  >
                    <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
                  </IconButton>
                </Flex>
              ) : undefined
            }
          >
            <UserToolsetConfigDialog
              embedded
              toolset={configureToolset}
              instanceId={configureToolset.instanceId}
              onClose={() => setConfigureToolset(null)}
              onSuccess={refreshToolsetLists}
              onNotify={(msg) => addToast({ variant: 'success', title: msg })}
            />
          </WorkspaceRightPanel>
        ) : null}
      </>
    );
  }

  return (
    <>
      <ActionsCatalogLayout
        title={t('workspace.actions.yourActionsTitle')}
        subtitle={t('workspace.actions.yourActionsSubtitle')}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        tabs={PERSONAL_CATALOG_TABS.map((tab) => ({ value: tab.value, label: t(tab.labelKey) }))}
        activeTab={catalogTab}
        onTabChange={handleTabChange}
        tabFilterMode="userAuth"
        tabCountsOverride={
          catalogTabCounts
            ? {
                all: catalogTabCounts.all,
                authenticated: catalogTabCounts.authenticated,
                not_authenticated: catalogTabCounts.notAuthenticated,
              }
            : null
        }
        leadingSection={
          <ApiActionsSection
            manage={false}
            searchQuery={searchQuery}
            filter={API_ACTION_FILTER_BY_TAB[catalogTab]}
          />
        }
        itemsHeading={t('workspace.actions.appsHeading')}
        items={items}
        resolveCta={resolveCta}
        onCta={handleCta}
        onCardClick={handleCardClick}
        isLoading={isLoading}
        loadingLabel={t('workspace.actions.loading')}
        emptyLabel={t('workspace.actions.empty')}
      />

      {setupPanel}

      {configureToolset?.instanceId ? (
        <WorkspaceRightPanel
          open
          onOpenChange={(o) => {
            if (!o) setConfigureToolset(null);
          }}
          title={t('workspace.actions.configPanelTitle')}
          icon={
            <ConnectorIcon
              type={configureToolset.toolsetType || configureToolset.name}
              size={20}
            />
          }
          hideFooter
          headerActions={
            configurePanelDocUrl ? (
              <Flex align="center" gap="1">
                <IconButton
                  type="button"
                  variant="ghost"
                  color="gray"
                  size="1"
                  aria-label={t('workspace.actions.documentation')}
                  style={{ cursor: 'pointer' }}
                  onClick={() =>
                    window.open(configurePanelDocUrl, '_blank', 'noopener,noreferrer')
                  }
                >
                  <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
                </IconButton>
              </Flex>
            ) : undefined
          }
        >
          <UserToolsetConfigDialog
            embedded
            toolset={configureToolset}
            instanceId={configureToolset.instanceId}
            onClose={() => setConfigureToolset(null)}
            onSuccess={refreshToolsetLists}
            onNotify={(msg) => addToast({ variant: 'success', title: msg })}
          />
        </WorkspaceRightPanel>
      ) : null}
    </>
  );
}

export default function PersonalActionsPage() {
  return (
    <ServiceGate services={['connector']}>
      <Suspense>
        <PersonalActionsPageContent />
      </Suspense>
    </ServiceGate>
  );
}

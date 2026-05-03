'use client';

import { useCallback, useEffect, useMemo, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Flex, IconButton, Text } from '@radix-ui/themes';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { primaryHttpDocumentationUrl } from '@/app/(main)/agents/agent-builder/components/toolset-agent-auth-helpers';
import {
  ToolsetsApi,
  MAX_TOOLSETS_LIST_LIMIT,
  type BuilderSidebarToolset,
  type RegistryToolsetRow,
} from '@/app/(main)/toolsets/api';
import { useWorkspaceToolsetListRefresh } from '../hooks/use-workspace-toolset-list-refresh';
import { useToolsetTypeInstanceList } from '../hooks/use-toolset-type-instance-list';
import { UserToolsetConfigDialog } from '@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog';
import { ActionsCatalogLayout, ActionTypeDetailsLayout, type ActionInstanceAuthTab } from '../components';
import {
  myToolsetsGroupedToCatalogItems,
  type ActionCatalogItem,
  type MyActionsTab,
} from '../types';
import type { ActionCardCta } from '../components/action-card';
import { isToolsetOAuthSuccessMessageType } from '@/app/(main)/toolsets/oauth/toolset-oauth-window-messages';

const PERSONAL_CATALOG_TABS: { value: MyActionsTab; labelKey: string }[] = [
  { value: 'all', labelKey: 'workspace.actions.tabs.all' },
  { value: 'authenticated', labelKey: 'workspace.actions.tabs.authenticated' },
  { value: 'not_authenticated', labelKey: 'workspace.actions.tabs.notAuthenticated' },
];

function PersonalActionsPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const toolsetTypeParam = searchParams.get('toolsetType');
  const instanceTabParam = searchParams.get('instanceTab');
  const instanceFilterTab = useMemo((): ActionInstanceAuthTab => {
    if (instanceTabParam === 'authenticated' || instanceTabParam === 'not_authenticated') {
      return instanceTabParam;
    }
    return 'all';
  }, [instanceTabParam]);

  const catalogTab = useMemo((): MyActionsTab => {
    const tab = searchParams.get('tab');
    if (tab === 'authenticated' || tab === 'not_authenticated') return tab;
    return 'all';
  }, [searchParams]);

  const [myToolsets, setMyToolsets] = useState<BuilderSidebarToolset[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedCatalogSearch, setDebouncedCatalogSearch] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [configureToolset, setConfigureToolset] = useState<BuilderSidebarToolset | null>(null);
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
        includeRegistry: false,
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
    if (toolsetTypeParam) return;
    void load();
  }, [load, refreshKey, toolsetTypeParam]);

  useEffect(() => {
    if (!toolsetTypeParam) return;
    setIsLoading(false);
  }, [toolsetTypeParam]);

  const items = useMemo(() => myToolsetsGroupedToCatalogItems(myToolsets), [myToolsets]);

  /**
   * Type-detail header: toolset metadata from any row of this type (not only the first list row),
   * with fallback to catalog my-toolsets so the title stays the integration name when filters empty.
   */
  const typeRegistryRow = useMemo((): RegistryToolsetRow | null => {
    if (!toolsetTypeParam) return null;
    const q = toolsetTypeParam.toLowerCase();
    const primary =
      typeListInstances.find((i) => (i.toolsetType || '').toLowerCase() === q) ??
      myToolsets.find((i) => (i.toolsetType || '').toLowerCase() === q);
    if (primary) {
      const name =
        primary.toolsetType || primary.normalized_name || primary.name || toolsetTypeParam;
      const authUpper = primary.authType ? String(primary.authType).toUpperCase() : '';
      return {
        name,
        displayName: primary.displayName || name,
        description: primary.description || '',
        category: primary.category || 'app',
        appGroup: '',
        iconPath: primary.iconPath || '',
        supportedAuthTypes: authUpper ? [authUpper] : ['NONE'],
        toolCount: primary.toolCount ?? 0,
        ...(primary.documentationLinks?.length ? { documentationLinks: primary.documentationLinks } : {}),
      };
    }
    return {
      name: toolsetTypeParam,
      displayName: toolsetTypeParam,
      description: '',
      category: 'app',
      appGroup: '',
      iconPath: '',
      supportedAuthTypes: ['NONE'],
      toolCount: 0,
    };
  }, [toolsetTypeParam, typeListInstances, myToolsets]);

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
        return { cta: 'unavailable', label: t('workspace.actions.cta.notConfigured') };
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
      if (item.rowKind === 'byToolsetType' && item.hasOrgInstance) {
        pushTypeDetail(item);
        return;
      }
      if (!item.primaryInstance?.instanceId) return;
      setConfigureToolset(item.primaryInstance);
    },
    [pushTypeDetail]
  );

  const handleCardClick = useCallback(
    (item: ActionCatalogItem) => {
      if (item.rowKind !== 'byToolsetType') return;
      if (item.hasOrgInstance) {
        pushTypeDetail(item);
        return;
      }
    },
    [pushTypeDetail]
  );

  const handleBackFromType = useCallback(() => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete('toolsetType');
    params.delete('instanceTab');
    const q = params.toString();
    router.replace(q ? `/workspace/actions/personal/?${q}` : '/workspace/actions/personal/');
  }, [router, searchParams]);

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
          instanceFilter={typeInstanceFilter}
          pagination={typeDetailPagination}
        />
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
        items={items}
        resolveCta={resolveCta}
        onCta={handleCta}
        onCardClick={handleCardClick}
        isLoading={isLoading}
        loadingLabel={t('workspace.actions.loading')}
        emptyLabel={t('workspace.actions.empty')}
      />

      {isAdmin ? (
        <Flex px="9" pt="2">
          <Text size="1" color="gray">
            {t('workspace.actions.personalAdminHint')}
          </Text>
        </Flex>
      ) : null}

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

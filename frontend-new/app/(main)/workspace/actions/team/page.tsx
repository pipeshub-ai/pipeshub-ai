'use client';

import { useCallback, useEffect, useMemo, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { useToastStore } from '@/lib/store/toast-store';
import { EXTERNAL_LINKS } from '@/lib/constants/external-links';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import {
  ToolsetsApi,
  MAX_TOOLSETS_LIST_LIMIT,
  type BuilderSidebarToolset,
  type RegistryToolsetRow,
} from '@/app/(main)/toolsets/api';
import { useWorkspaceToolsetListRefresh } from '../hooks/use-workspace-toolset-list-refresh';
import { useToolsetTypeInstanceList } from '../hooks/use-toolset-type-instance-list';
import { UserToolsetConfigDialog } from '@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog';
import {
  ActionsCatalogLayout,
  ActionSetupPanel,
  ActionTypeDetailsLayout,
  AdminManageActionPanel,
  type ActionInstanceAuthTab,
} from '../components';
import {
  actionCatalogItemToRegistryRow,
  mergedMyToolsetsCatalogFromIncludeRegistry,
  type ActionCatalogItem,
} from '../types';
import type { ActionCardCta } from '../components/action-card';
import { isToolsetOAuthSuccessMessageType } from '@/app/(main)/toolsets/oauth/toolset-oauth-window-messages';

const TEAM_TABS = [
  { value: 'all', labelKey: 'workspace.actions.tabs.all' },
  { value: 'configured', labelKey: 'workspace.actions.tabs.configured' },
  { value: 'not_configured', labelKey: 'workspace.actions.tabs.notConfigured' },
];

function TeamActionsPageContent() {
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

  /** Fallback for type detail when catalog was never loaded (e.g. deep link). */
  const [registryRows, setRegistryRows] = useState<RegistryToolsetRow[]>([]);
  const [catalogRows, setCatalogRows] = useState<BuilderSidebarToolset[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedCatalogSearch, setDebouncedCatalogSearch] = useState('');
  const [activeTab, setActiveTab] = useState('all');
  const [isLoading, setIsLoading] = useState(true);
  const [createFor, setCreateFor] = useState<RegistryToolsetRow | null>(null);
  const [configureToolset, setConfigureToolset] = useState<BuilderSidebarToolset | null>(null);
  const [manageInstance, setManageInstance] = useState<BuilderSidebarToolset | null>(null);
  const [postCreateNoticeOpen, setPostCreateNoticeOpen] = useState(false);
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
    enabled: Boolean(isAdmin && toolsetTypeParam),
  });

  useEffect(() => {
    if (!isAdmin) return;
    const oauthContextOpen = Boolean(
      toolsetTypeParam || configureToolset?.instanceId || manageInstance?.instanceId
    );
    if (!oauthContextOpen) return;
    const onMsg = (event: MessageEvent) => {
      if (typeof window === 'undefined' || event.origin !== window.location.origin) return;
      if (!isToolsetOAuthSuccessMessageType(event.data?.type)) return;
      refreshToolsetLists();
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [
    configureToolset?.instanceId,
    isAdmin,
    manageInstance?.instanceId,
    refreshToolsetLists,
    toolsetTypeParam,
  ]);

  useEffect(() => {
    if (!isAdmin) {
      router.replace('/workspace/actions/personal/');
    }
  }, [isAdmin, router]);

  useEffect(() => {
    const tab = searchParams.get('tab');
    const valid = TEAM_TABS.map((x) => x.value);
    if (tab && valid.includes(tab)) setActiveTab(tab);
    else setActiveTab('all');
  }, [searchParams]);

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedCatalogSearch(searchQuery.trim()), 300);
    return () => window.clearTimeout(id);
  }, [searchQuery]);

  useEffect(() => {
    if (!toolsetTypeParam) return;
    setConfigureToolset((prev) => {
      if (!prev?.instanceId) return prev;
      const found = typeListInstances.find((i) => i.instanceId === prev.instanceId);
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
  }, [toolsetTypeParam, typeListInstances]);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const { toolsets } = await ToolsetsApi.getAllMyToolsets({
        includeRegistry: true,
        limitPerPage: MAX_TOOLSETS_LIST_LIMIT,
        search: debouncedCatalogSearch || undefined,
      });
      setCatalogRows(toolsets);
    } catch {
      addToast({ variant: 'error', title: t('workspace.actions.loadError') });
    } finally {
      setIsLoading(false);
    }
  }, [addToast, debouncedCatalogSearch, t]);

  useEffect(() => {
    if (!isAdmin || toolsetTypeParam) return;
    void load();
  }, [isAdmin, toolsetTypeParam, load, refreshKey]);

  useEffect(() => {
    if (!isAdmin || !toolsetTypeParam) return;
    setIsLoading(false);
  }, [isAdmin, toolsetTypeParam]);

  useEffect(() => {
    if (!isAdmin || !toolsetTypeParam) return;
    const q = toolsetTypeParam.toLowerCase();
    let cancelled = false;
    void (async () => {
      try {
        const res = await ToolsetsApi.getRegistryToolsets({
          page: 1,
          limit: MAX_TOOLSETS_LIST_LIMIT,
          search: toolsetTypeParam,
          includeTools: false,
          includeToolCount: true,
          groupByCategory: false,
        });
        if (cancelled) return;
        const hit = res.toolsets.find((r) => r.name.toLowerCase() === q);
        setRegistryRows((prev) => {
          if (prev.some((r) => r.name.toLowerCase() === q)) return prev;
          if (hit) return [...prev, hit];
          return prev;
        });
      } catch {
        /* header may fall back to instance metadata */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isAdmin, toolsetTypeParam]);

  const mergedTeamCatalog = useMemo(
    () => mergedMyToolsetsCatalogFromIncludeRegistry(catalogRows),
    [catalogRows]
  );

  const tabFilteredCatalog = useMemo(() => {
    switch (activeTab) {
      case 'configured':
        return mergedTeamCatalog.filter((c) => c.hasOrgInstance);
      case 'not_configured':
        return mergedTeamCatalog.filter((c) => !c.hasOrgInstance);
      default:
        return mergedTeamCatalog;
    }
  }, [activeTab, mergedTeamCatalog]);

  const tabCountsOverride = useMemo(
    () => ({
      all: mergedTeamCatalog.length,
      configured: mergedTeamCatalog.filter((c) => c.hasOrgInstance).length,
      not_configured: mergedTeamCatalog.filter((c) => !c.hasOrgInstance).length,
    }),
    [mergedTeamCatalog]
  );

  const typeRegistryRow = useMemo(() => {
    if (!toolsetTypeParam) return null;
    const q = toolsetTypeParam.toLowerCase();
    const fromMerged = mergedTeamCatalog.find((i) => i.toolsetType.toLowerCase() === q);
    if (fromMerged) return actionCatalogItemToRegistryRow(fromMerged);
    return registryRows.find((r) => r.name.toLowerCase() === q) ?? null;
  }, [mergedTeamCatalog, registryRows, toolsetTypeParam]);

  const handleInstanceTabChange = useCallback(
    (tab: ActionInstanceAuthTab) => {
      const params = new URLSearchParams(searchParams.toString());
      if (tab === 'all') params.delete('instanceTab');
      else params.set('instanceTab', tab);
      const q = params.toString();
      router.replace(q ? `/workspace/actions/team/?${q}` : '/workspace/actions/team/');
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

  const handleTabChange = useCallback(
    (val: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.delete('toolsetType');
      params.delete('instanceTab');
      if (val === 'all') params.delete('tab');
      else params.set('tab', val);
      const q = params.toString();
      router.replace(q ? `/workspace/actions/team/?${q}` : '/workspace/actions/team/');
    },
    [router, searchParams]
  );

  const resolveCta = useCallback(
    (_item: ActionCatalogItem): { cta: ActionCardCta; label: string } => ({
      cta: 'setup',
      label: t('workspace.actions.cta.setup'),
    }),
    [t]
  );

  const handleCta = useCallback((item: ActionCatalogItem) => {
    setCreateFor(actionCatalogItemToRegistryRow(item));
  }, []);

  const handleCardClick = useCallback(
    (item: ActionCatalogItem) => {
      if (item.rowKind !== 'byToolsetType') return;
      if (item.hasOrgInstance) {
        const params = new URLSearchParams(searchParams.toString());
        params.set('toolsetType', item.toolsetType);
        params.delete('instanceTab');
        const q = params.toString();
        router.push(`/workspace/actions/team/?${q}`);
        return;
      }
      setCreateFor(actionCatalogItemToRegistryRow(item));
    },
    [router, searchParams]
  );

  const handleBackFromType = useCallback(() => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete('toolsetType');
    params.delete('instanceTab');
    const q = params.toString();
    router.replace(q ? `/workspace/actions/team/?${q}` : '/workspace/actions/team/');
  }, [router, searchParams]);

  const handleAddFromType = useCallback(() => {
    if (typeRegistryRow) setCreateFor(typeRegistryRow);
  }, [typeRegistryRow]);

  if (!isAdmin) return null;

  if (toolsetTypeParam) {
    return (
      <>
        <ActionTypeDetailsLayout
          scope="team"
          registryRow={typeRegistryRow}
          instances={typeListInstances}
          isLoading={typeListLoading}
          listRefreshing={typeListRefreshing}
          onBack={handleBackFromType}
          onAddInstance={handleAddFromType}
          onOpenDocs={() => window.open(EXTERNAL_LINKS.documentation, '_blank', 'noopener,noreferrer')}
          onAuthenticateInstance={(inst) => {
            setManageInstance(null);
            setConfigureToolset(inst);
          }}
          onConfigureInstance={(inst) => {
            setManageInstance(null);
            setConfigureToolset(inst);
          }}
          onManageInstance={(inst) => {
            setConfigureToolset(null);
            setManageInstance(inst);
          }}
          instanceFilter={typeInstanceFilter}
          pagination={typeDetailPagination}
        />
        <ActionSetupPanel
          open={Boolean(createFor)}
          registryRow={createFor}
          onOpenChange={(o) => {
            if (!o) setCreateFor(null);
          }}
          onCreated={bumpRefreshKey}
          onCreatedUserAuthNotice={() => setPostCreateNoticeOpen(true)}
          onNotify={(msg) => addToast({ variant: 'success', title: msg })}
        />
        <Dialog.Root open={postCreateNoticeOpen} onOpenChange={setPostCreateNoticeOpen}>
          <Dialog.Content style={{ maxWidth: 440 }}>
            <Dialog.Title>{t('workspace.actions.postCreateAuth.title')}</Dialog.Title>
            <Dialog.Description size="2" mb="3">
              {t('workspace.actions.postCreateAuth.body')}
            </Dialog.Description>
            <Flex justify="end">
              <Button color="jade" onClick={() => setPostCreateNoticeOpen(false)}>
                {t('workspace.actions.postCreateAuth.gotIt')}
              </Button>
            </Flex>
          </Dialog.Content>
        </Dialog.Root>
        {configureToolset?.instanceId ? (
          <WorkspaceRightPanel
            open
            onOpenChange={(o) => {
              if (!o) setConfigureToolset(null);
            }}
            title={t('workspace.actions.configPanelTitle')}
            icon={
              configureToolset.iconPath ? (
                <img
                  src={configureToolset.iconPath}
                  alt=""
                  width={20}
                  height={20}
                  style={{ objectFit: 'contain' }}
                />
              ) : (
                <MaterialIcon name="bolt" size={20} color="var(--slate-12)" />
              )
            }
            hideFooter
            headerActions={
              <IconButton
                type="button"
                variant="ghost"
                color="gray"
                size="1"
                aria-label={t('workspace.actions.documentation')}
                onClick={() =>
                  window.open(EXTERNAL_LINKS.documentation, '_blank', 'noopener,noreferrer')
                }
              >
                <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
              </IconButton>
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
        {manageInstance?.instanceId ? (
          <WorkspaceRightPanel
            open
            onOpenChange={(o) => {
              if (!o) setManageInstance(null);
            }}
            title={t('workspace.actions.manage.panelTitle')}
            icon={
              manageInstance.iconPath ? (
                <img
                  src={manageInstance.iconPath}
                  alt=""
                  width={20}
                  height={20}
                  style={{ objectFit: 'contain' }}
                />
              ) : (
                <MaterialIcon name="bolt" size={20} color="var(--slate-12)" />
              )
            }
            hideFooter
            headerActions={
              <IconButton
                type="button"
                variant="ghost"
                color="gray"
                size="1"
                aria-label={t('workspace.actions.documentation')}
                onClick={() =>
                  window.open(EXTERNAL_LINKS.documentation, '_blank', 'noopener,noreferrer')
                }
              >
                <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
              </IconButton>
            }
          >
            <AdminManageActionPanel
              instance={manageInstance}
              onClose={() => setManageInstance(null)}
              onSaved={refreshToolsetLists}
              onDeleted={bumpRefreshKey}
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
        title={t('workspace.actions.title')}
        subtitle={t('workspace.actions.subtitle')}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        tabs={TEAM_TABS.map((tab) => ({ value: tab.value, label: t(tab.labelKey) }))}
        activeTab={activeTab}
        onTabChange={handleTabChange}
        tabFilterMode="orgInstances"
        preFilteredCatalog
        tabCountsOverride={tabCountsOverride}
        trailingAction={
          <NavigateButton label={t('workspace.actions.trailingYourActions')} onClick={() => router.push('/workspace/actions/personal/')} />
        }
        items={tabFilteredCatalog}
        resolveCta={resolveCta}
        onCta={handleCta}
        onCardClick={handleCardClick}
        isLoading={isLoading}
        loadingLabel={t('workspace.actions.loading')}
        emptyLabel={t('workspace.actions.empty')}
      />
      <ActionSetupPanel
        open={Boolean(createFor)}
        registryRow={createFor}
        onOpenChange={(o) => {
          if (!o) setCreateFor(null);
        }}
        onCreated={() => void load()}
        onCreatedUserAuthNotice={() => setPostCreateNoticeOpen(true)}
        onNotify={(msg) => addToast({ variant: 'success', title: msg })}
      />
      <Dialog.Root open={postCreateNoticeOpen} onOpenChange={setPostCreateNoticeOpen}>
        <Dialog.Content style={{ maxWidth: 440 }}>
          <Dialog.Title>{t('workspace.actions.postCreateAuth.title')}</Dialog.Title>
          <Dialog.Description size="2" mb="3">
            {t('workspace.actions.postCreateAuth.body')}
          </Dialog.Description>
          <Flex justify="end">
            <Button color="jade" onClick={() => setPostCreateNoticeOpen(false)}>
              {t('workspace.actions.postCreateAuth.gotIt')}
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}

function NavigateButton({ label, onClick }: { label: string; onClick: () => void }) {
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

export default function TeamActionsPage() {
  return (
    <Suspense>
      <TeamActionsPageContent />
    </Suspense>
  );
}

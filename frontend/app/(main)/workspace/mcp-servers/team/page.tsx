'use client';

import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectFeatureFlagsLoaded,
} from '@/lib/store/feature-flags-store';
import { ConfirmationDialog } from '../../components';
import { useMcpTeamStore } from './store';
import { McpServersApi } from '../api';
import { useMcpOAuthPopup } from '../hooks/use-mcp-oauth-popup';
import { McpAuthDialog } from '../components';
import type { McpMyServerEntry, McpToolInfo } from '../types';
import {
  McpCatalogLayout,
  McpServerDetailsLayout,
  McpInstanceConfigPanel,
} from './components';

type ToolsResult = { tools: McpToolInfo[]; error?: string; loading: boolean };

function TeamMcpServersPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const store = useMcpTeamStore();
  const [isDeleting, setIsDeleting] = useState(false);
  const [busyInstanceId, setBusyInstanceId] = useState<string | null>(null);
  const [authDialogInstance, setAuthDialogInstance] = useState<McpMyServerEntry | null>(null);
  const [toolsState, setToolsState] = useState<Record<string, ToolsResult | undefined>>({});

  const typeId = searchParams.get('typeId');

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/mcp-servers/personal');
    }
  }, [isProfileInitialized, isAdmin, router]);

  useEffect(() => {
    if (flagsLoaded && !mcpEnabled) {
      router.replace('/workspace/general');
    }
  }, [flagsLoaded, mcpEnabled, router]);

  const loadData = useCallback(async () => {
    const s = useMcpTeamStore.getState();
    s.setLoading(true);
    try {
      const [catalogRes, myServersRes] = await Promise.all([
        McpServersApi.getCatalog({ limit: 200 }),
        McpServersApi.getMyMcpServers(false),
      ]);
      s.setTemplates(catalogRes.templates);
      s.setInstances(myServersRes.instances);
    } catch {
      toast.error(t('workspace.mcpServers.toasts.loadError'));
    } finally {
      s.setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!isProfileInitialized || isAdmin === false || !flagsLoaded || !mcpEnabled) return;
    void loadData();
    return () => useMcpTeamStore.getState().reset();
  }, [isProfileInitialized, isAdmin, flagsLoaded, mcpEnabled, loadData]);

  const handleConfirmDelete = useCallback(async () => {
    const target = store.deleteTarget;
    if (!target) return;
    setIsDeleting(true);
    try {
      await McpServersApi.deleteInstance(target._id);
      toast.success(t('workspace.mcpServers.toasts.deleted'));
      store.closeDeleteDialog();
      store.closeConfigPanel();
      void loadData();
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(t('workspace.mcpServers.toasts.deleteError'), detail ? { description: detail } : undefined);
    } finally {
      setIsDeleting(false);
    }
  }, [store, loadData, t]);

  // ── Type-detail page (?typeId=) ──

  const detailsTemplate = useMemo(
    () => store.templates.find((tpl) => tpl.typeId === typeId) ?? null,
    [store.templates, typeId]
  );
  const detailsHeader = useMemo(
    () =>
      detailsTemplate
        ? {
            typeId: detailsTemplate.typeId,
            displayName: detailsTemplate.displayName,
            description: detailsTemplate.description,
            icon: detailsTemplate.icon,
          }
        : null,
    [detailsTemplate]
  );
  const detailsInstances = useMemo(
    () => store.instances.filter((i) => i.typeId === typeId),
    [store.instances, typeId]
  );
  const showDetailsPage = Boolean(typeId);

  const handleManageTemplate = useCallback(
    (typeIdToOpen: string) => {
      router.push(`/workspace/mcp-servers/team/?typeId=${encodeURIComponent(typeIdToOpen)}`);
    },
    [router]
  );

  const handleBackToList = useCallback(() => {
    router.push('/workspace/mcp-servers/team/');
  }, [router]);

  // ── Auth: OAuth popup + api_token/headers dialog (mirrors the personal page) ──

  const { startOAuthPopup } = useMcpOAuthPopup({
    verifyAuthenticated: async () => {
      const res = await McpServersApi.getMyMcpServers(false);
      const target = res.instances.find((i) => i._id === busyInstanceId);
      return Boolean(target?.isAuthenticated);
    },
    onVerified: () => {
      setBusyInstanceId(null);
      void loadData();
      toast.success(t('workspace.mcpServers.toasts.authenticated'));
    },
    onFailed: () => {
      setBusyInstanceId(null);
    },
  });

  const handleAuthenticate = useCallback(
    (instance: McpMyServerEntry) => {
      if (instance.authMode === 'oauth') {
        setBusyInstanceId(instance._id);
        void startOAuthPopup(instance._id);
        return;
      }
      setAuthDialogInstance(instance);
    },
    [startOAuthPopup]
  );

  const handleReauthenticate = useCallback(
    async (instance: McpMyServerEntry) => {
      if (instance.authMode === 'oauth') {
        setBusyInstanceId(instance._id);
        try {
          await McpServersApi.reauthenticate(instance._id);
          await startOAuthPopup(instance._id);
        } catch (error) {
          setBusyInstanceId(null);
          const detail = isProcessedError(error) ? error.message : undefined;
          toast.error(t('workspace.mcpServers.toasts.authenticateError'), detail ? { description: detail } : undefined);
        }
        return;
      }
      setAuthDialogInstance(instance);
    },
    [startOAuthPopup, t]
  );

  const handleDisconnect = useCallback(
    async (instance: McpMyServerEntry) => {
      setBusyInstanceId(instance._id);
      try {
        await McpServersApi.removeCredentials(instance._id);
        toast.success(t('workspace.mcpServers.toasts.disconnected'));
        await loadData();
      } catch (error) {
        const detail = isProcessedError(error) ? error.message : undefined;
        toast.error(t('workspace.mcpServers.toasts.disconnectError'), detail ? { description: detail } : undefined);
      } finally {
        setBusyInstanceId(null);
      }
    },
    [loadData, t]
  );

  // ── Tool discovery (on-demand, per instance) ──

  const handleDiscoverTools = useCallback(
    async (instance: McpMyServerEntry) => {
      setToolsState((prev) => ({ ...prev, [instance._id]: { tools: [], loading: true } }));
      try {
        const res = await McpServersApi.getInstanceTools(instance._id);
        setToolsState((prev) => ({ ...prev, [instance._id]: { tools: res.tools, loading: false } }));
      } catch (error) {
        const detail = isProcessedError(error) ? error.message : t('workspace.mcpServers.toasts.discoveryError');
        setToolsState((prev) => ({ ...prev, [instance._id]: { tools: [], error: detail, loading: false } }));
      }
    },
    [t]
  );

  if (!isProfileInitialized || isAdmin === false || !flagsLoaded || !mcpEnabled) return null;

  return (
    <>
      {showDetailsPage ? (
        <McpServerDetailsLayout
          header={detailsHeader}
          instances={detailsInstances}
          isLoading={store.isLoading}
          onBack={handleBackToList}
          onAddInstance={() => detailsTemplate && store.openCreateFromTemplate(detailsTemplate)}
          onManageInstance={(instance) => store.openEditInstance(instance)}
          onDeleteInstance={(instance) => store.openDeleteDialog(instance)}
          onAuthenticate={handleAuthenticate}
          onReauthenticate={(instance) => void handleReauthenticate(instance)}
          onDisconnect={(instance) => void handleDisconnect(instance)}
          busyInstanceId={busyInstanceId}
          onRefreshAll={() => void loadData()}
          toolsState={toolsState}
          onDiscoverTools={(instance) => void handleDiscoverTools(instance)}
        />
      ) : (
        <McpCatalogLayout
          templates={store.templates}
          instances={store.instances}
          isLoading={store.isLoading}
          searchQuery={store.searchQuery}
          onSearchChange={store.setSearchQuery}
          onAddCustom={store.openCreateCustom}
          onSetupTemplate={store.openCreateFromTemplate}
          onAddInstanceForTemplate={store.openCreateFromTemplate}
          onManageTemplate={(template) => handleManageTemplate(template.typeId)}
          onEditInstance={(instance) => store.openEditInstance(instance)}
          onDeleteInstance={(instance) => store.openDeleteDialog(instance)}
          onRefresh={() => void loadData()}
        />
      )}

      <McpInstanceConfigPanel
        state={store.configPanel}
        templates={store.templates}
        instances={store.instances}
        onOpenChange={(open) => {
          if (!open) store.closeConfigPanel();
        }}
        onSaved={() => void loadData()}
        onRequestDelete={(instance) => store.openDeleteDialog(instance)}
        busyInstanceId={busyInstanceId}
        onAuthenticate={handleAuthenticate}
        onReauthenticate={(instance) => void handleReauthenticate(instance)}
        onDisconnect={(instance) => void handleDisconnect(instance)}
      />

      <McpAuthDialog
        instance={authDialogInstance}
        open={authDialogInstance !== null}
        onOpenChange={(open) => {
          if (!open) setAuthDialogInstance(null);
        }}
        onAuthenticated={() => void loadData()}
      />

      <ConfirmationDialog
        open={store.deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) store.closeDeleteDialog();
        }}
        title={t('workspace.mcpServers.deleteDialog.title')}
        message={t('workspace.mcpServers.deleteDialog.body', { name: store.deleteTarget?.name ?? '' })}
        confirmLabel={t('workspace.mcpServers.cta.delete')}
        confirmVariant="danger"
        isLoading={isDeleting}
        confirmLoadingLabel={t('action.deleting')}
        onConfirm={() => void handleConfirmDelete()}
      />
    </>
  );
}

export default function TeamMcpServersPage() {
  return (
    <ServiceGate services={['connector']}>
      <Suspense>
        <TeamMcpServersPageContent />
      </Suspense>
    </ServiceGate>
  );
}

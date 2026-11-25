'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api';
import { ServiceGate } from '@/app/components/ui/service-gate';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectFeatureFlagsKnown,
  selectFeatureFlagsLoaded,
} from '@/lib/store/feature-flags-store';
import { ConfirmationDialog, WorkspaceHeaderIconButton } from '../../components';
import { useMcpPersonalStore } from './store';
import { McpServersApi } from '../api';
import { mcpOAuthFailureMessage, useMcpOAuthPopup } from '../hooks/use-mcp-oauth-popup';
import { isPersonalMcpInstance, type McpMyServerEntry } from '../types';
import { offeredForPersonalServers } from '../catalog-replacement';
import { McpInstanceConfigPanel } from '../team/components';
import { McpAuthDialog, McpAddServerDialog } from './components';
import { McpAddServerButton, McpServerPage, McpServersLayout } from '../components';
import { reloadMcpLogos } from '../logo-store';

const LIST_PATH = '/workspace/mcp-servers/personal/';

function PersonalMcpServersPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  // A failed read's stand-in never redirects: only an answer that says it is off (UIADOPT1-FU-1).
  const flagsKnown = useFeatureFlagsStore(selectFeatureFlagsKnown);
  const store = useMcpPersonalStore();
  const [busyInstanceId, setBusyInstanceId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const serverId = searchParams.get('serverId');
  const justAdded = searchParams.get('added') === '1';
  const selectedServer = useMemo(
    () => (serverId ? store.instances.find((i) => i._id === serverId) ?? null : null),
    [serverId, store.instances]
  );

  // Loads overlap (a refresh during a disconnect) and can answer out of order: only the latest applies.
  const loadRequestIdRef = useRef(0);
  const loadData = useCallback(async () => {
    const requestId = ++loadRequestIdRef.current;
    const s = useMcpPersonalStore.getState();
    s.setLoading(true);
    try {
      const [myServersRes, catalogRes] = await Promise.all([
        McpServersApi.getMyMcpServers(true),
        McpServersApi.getCatalog({ limit: 200 }),
      ]);
      if (requestId !== loadRequestIdRef.current) return;
      s.setInstances(myServersRes.instances);
      s.setTemplates(catalogRes.templates);
    } catch {
      if (requestId !== loadRequestIdRef.current) return;
      toast.error(t('workspace.mcpServers.toasts.loadError'));
    } finally {
      if (requestId === loadRequestIdRef.current) s.setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (flagsKnown && !mcpEnabled) {
      router.replace('/workspace/general');
    }
  }, [flagsKnown, mcpEnabled, router]);

  useEffect(() => {
    if (!flagsLoaded || !mcpEnabled) return;
    void loadData();
    return () => useMcpPersonalStore.getState().reset();
  }, [flagsLoaded, mcpEnabled, loadData]);

  const refresh = useCallback(() => {
    void loadData();
    void reloadMcpLogos();
  }, [loadData]);

  const openServer = useCallback(
    (instance: McpMyServerEntry) => router.push(`${LIST_PATH}?serverId=${encodeURIComponent(instance._id)}`),
    [router]
  );

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
    onFailed: (failure) => {
      setBusyInstanceId(null);
      toast.error(mcpOAuthFailureMessage(t, failure));
      // The stored tokens are untouched by a failed sign-in, but show the list as it is now.
      void loadData();
    },
  });

  // A first sign-in and a new one take the same path: a new sign-in replaces the stored tokens
  // only when it succeeds, so a blocked or cancelled popup leaves the working connection as it was.
  const handleAuthenticate = useCallback(
    (instance: McpMyServerEntry) => {
      if (instance.authMode === 'oauth') {
        setBusyInstanceId(instance._id);
        void startOAuthPopup(instance._id);
        return;
      }
      store.openAuthDialog(instance);
    },
    [store, startOAuthPopup]
  );

  const handleRemoveCredentials = useCallback(
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

  const handleConfirmDelete = useCallback(async () => {
    const target = store.deleteTarget;
    if (!target) return;
    setIsDeleting(true);
    try {
      await McpServersApi.deleteInstance(target._id);
      toast.success(t('workspace.mcpServers.toasts.deleted'));
      store.closeDeleteDialog();
      store.closeConfigPanel();
      if (serverId === target._id) router.push(LIST_PATH);
      void loadData();
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(t('workspace.mcpServers.toasts.deleteError'), detail ? { description: detail } : undefined);
    } finally {
      setIsDeleting(false);
    }
  }, [store, loadData, t, serverId, router]);

  if (!flagsLoaded || !mcpEnabled) return null;

  // Only a person's own servers are theirs to change.
  const own = (instance: McpMyServerEntry) => isPersonalMcpInstance(instance);

  return (
    <>
      {selectedServer ? (
        <McpServerPage
          key={selectedServer._id}
          instance={selectedServer}
          templates={store.templates}
          isAdmin={false}
          rulesEditable
          isBusy={busyInstanceId === selectedServer._id}
          onBack={() => router.push(LIST_PATH)}
          onAuthenticate={() => handleAuthenticate(selectedServer)}
          onReauthenticate={() => handleAuthenticate(selectedServer)}
          onDisconnect={() => handleRemoveCredentials(selectedServer)}
          onEdit={own(selectedServer) ? () => store.openEditInstance(selectedServer) : undefined}
          justAdded={justAdded}
          onDismissAdded={() => router.replace(`${LIST_PATH}?serverId=${encodeURIComponent(selectedServer._id)}`)}
          onDelete={own(selectedServer) ? () => store.openDeleteDialog(selectedServer) : undefined}
        />
      ) : (
        <McpServersLayout
          title={t('workspace.mcpServers.personal.title')}
          subtitle={t('workspace.mcpServers.personal.subtitle')}
          actions={
            <>
              <McpAddServerButton onClick={store.openAddDialog} />
              <WorkspaceHeaderIconButton icon="refresh" onClick={refresh} aria-label={t('action.refresh')} />
            </>
          }
          isLoading={store.isLoading}
          searchQuery={store.searchQuery}
          onSearchChange={store.setSearchQuery}
          instances={store.instances}
          templates={store.templates}
          catalog={offeredForPersonalServers(store.templates)}
          catalogDescription={t('workspace.mcpServers.catalog.personalDescription')}
          emptyTitle={t('workspace.mcpServers.personal.emptyTitle')}
          emptyDescription={t('workspace.mcpServers.personal.emptyDescription')}
          isAdmin={false}
          busyInstanceId={busyInstanceId}
          onOpen={openServer}
          onConnect={handleAuthenticate}
          onReconnect={handleAuthenticate}
          onEdit={(instance) => (own(instance) ? store.openEditInstance(instance) : openServer(instance))}
          onAdd={store.openCreateFromTemplate}
        />
      )}

      <McpAddServerDialog
        open={store.addDialogOpen}
        templates={store.templates}
        onOpenChange={(open) => {
          if (!open) store.closeAddDialog();
        }}
        onPickTemplate={store.openCreateFromTemplate}
        onPickCustom={store.openCreateCustom}
      />

      <McpInstanceConfigPanel
        scope="personal"
        state={store.configPanel}
        templates={store.templates}
        instances={store.instances}
        customStdioAllowed={false}
        onOpenChange={(open) => {
          if (!open) store.closeConfigPanel();
        }}
        onSaved={({ id, created }) => {
          void loadData();
          // A new server opens on its own page, where its sign-in is the next step.
          if (created) router.push(`${LIST_PATH}?serverId=${encodeURIComponent(id)}&added=1`);
        }}
      />

      <McpAuthDialog
        instance={store.authDialog.instance}
        template={store.templates.find((tpl) => tpl.typeId === store.authDialog.instance?.typeId) ?? null}
        open={store.authDialog.open}
        onOpenChange={(open) => {
          if (!open) store.closeAuthDialog();
        }}
        onAuthenticated={() => void loadData()}
      />

      <ConfirmationDialog
        open={store.deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) store.closeDeleteDialog();
        }}
        title={t('workspace.mcpServers.deleteDialog.title')}
        message={t('workspace.mcpServers.deleteDialog.personalBody', { name: store.deleteTarget?.name ?? '' })}
        confirmLabel={t('workspace.mcpServers.cta.delete')}
        confirmVariant="danger"
        isLoading={isDeleting}
        confirmLoadingLabel={t('action.deleting')}
        onConfirm={() => void handleConfirmDelete()}
      />
    </>
  );
}

export default function PersonalMcpServersPage() {
  return (
    <ServiceGate services={['connector']}>
      <Suspense>
        <PersonalMcpServersPageContent />
      </Suspense>
    </ServiceGate>
  );
}

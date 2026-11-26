'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectFeatureFlagsKnown,
  selectFeatureFlagsLoaded,
} from '@/lib/store/feature-flags-store';
import { EnterpriseFeatureNotice, isMcpInstanceReadOnly } from '@/config';
import { ConfirmationDialog, WorkspaceHeaderIconButton } from '../../components';
import { orgInstancesWithStatus, useMcpTeamStore } from './store';
import { McpServersApi } from '../api';
import { mcpOAuthFailureMessage, useMcpOAuthPopup } from '../hooks/use-mcp-oauth-popup';
import { McpAddServerButton, McpAuthDialog, McpServerPage, McpServersLayout } from '../components';
import type { McpMyServerEntry } from '../types';
import { isOfferedForNewServers } from '../catalog-replacement';
import { reloadMcpLogos } from '../logo-store';
import { McpInstanceConfigPanel } from './components';

const LIST_PATH = '/workspace/mcp-servers/team/';

function TeamMcpServersPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  // A failed read's stand-in never redirects: only an answer that says it is off (UIADOPT1-FU-1).
  const flagsKnown = useFeatureFlagsStore(selectFeatureFlagsKnown);
  const store = useMcpTeamStore();
  const [isDeleting, setIsDeleting] = useState(false);
  const [busyInstanceId, setBusyInstanceId] = useState<string | null>(null);
  const [authDialogInstance, setAuthDialogInstance] = useState<McpMyServerEntry | null>(null);

  const serverId = searchParams.get('serverId');
  const justAdded = searchParams.get('added') === '1';
  // A link to one server's admin section (the chat's "approve its tools"): `?serverId=<id>&tab=hub`.
  const focusAdminSection = searchParams.get('tab') === 'hub';
  // Links written before a server had its own page: `?instance=<id>` opens that page.
  const legacyInstance = searchParams.get('instance');
  const selectedServer = useMemo(
    () => (serverId ? store.instances.find((i) => i._id === serverId) ?? null : null),
    [serverId, store.instances]
  );

  useEffect(() => {
    if (!legacyInstance || serverId) return;
    const tab = focusAdminSection ? '&tab=hub' : '';
    router.replace(`${LIST_PATH}?serverId=${encodeURIComponent(legacyInstance)}${tab}`);
  }, [legacyInstance, serverId, focusAdminSection, router]);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/mcp-servers/personal');
    }
  }, [isProfileInitialized, isAdmin, router]);

  useEffect(() => {
    if (flagsKnown && !mcpEnabled) {
      router.replace('/workspace/general');
    }
  }, [flagsKnown, mcpEnabled, router]);

  // Loads overlap (a refresh during a delete) and can answer out of order: only the latest applies.
  const loadRequestIdRef = useRef(0);
  const loadData = useCallback(async () => {
    const requestId = ++loadRequestIdRef.current;
    const s = useMcpTeamStore.getState();
    s.setLoading(true);
    try {
      const [catalogRes, myServersRes, allInstancesRes] = await Promise.all([
        McpServersApi.getCatalog({ limit: 200 }),
        McpServersApi.getMyMcpServers(false),
        McpServersApi.listInstances(),
      ]);
      if (requestId !== loadRequestIdRef.current) return;
      s.setTemplates(catalogRes.templates);
      s.setCustomStdioAllowed(catalogRes.customStdioAllowed === true);
      // The admin's own personal servers live on their personal page, not in the org catalog.
      s.setInstances(orgInstancesWithStatus(allInstancesRes.instances, myServersRes.instances));
    } catch {
      if (requestId !== loadRequestIdRef.current) return;
      toast.error(t('workspace.mcpServers.toasts.loadError'));
    } finally {
      if (requestId === loadRequestIdRef.current) s.setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!isProfileInitialized || isAdmin === false || !flagsLoaded || !mcpEnabled) return;
    void loadData();
    return () => useMcpTeamStore.getState().reset();
  }, [isProfileInitialized, isAdmin, flagsLoaded, mcpEnabled, loadData]);

  const refresh = useCallback(() => {
    void loadData();
    void reloadMcpLogos();
  }, [loadData]);

  const openServer = useCallback(
    (instance: McpMyServerEntry) => router.push(`${LIST_PATH}?serverId=${encodeURIComponent(instance._id)}`),
    [router]
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
      setAuthDialogInstance(instance);
    },
    [startOAuthPopup]
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

  if (!isProfileInitialized || isAdmin === false || !flagsLoaded || !mcpEnabled) return null;

  const canEdit = (instance: McpMyServerEntry) => !isMcpInstanceReadOnly(instance);

  return (
    <>
      {selectedServer ? (
        <McpServerPage
          key={selectedServer._id}
          instance={selectedServer}
          templates={store.templates}
          isAdmin
          rulesEditable={false}
          isBusy={busyInstanceId === selectedServer._id}
          onBack={() => router.push(LIST_PATH)}
          onAuthenticate={() => handleAuthenticate(selectedServer)}
          onReauthenticate={() => handleAuthenticate(selectedServer)}
          onDisconnect={() => handleDisconnect(selectedServer)}
          onEdit={canEdit(selectedServer) ? () => store.openEditInstance(selectedServer) : undefined}
          justAdded={justAdded}
          onDismissAdded={() => router.replace(`${LIST_PATH}?serverId=${encodeURIComponent(selectedServer._id)}`)}
          onDelete={canEdit(selectedServer) ? () => store.openDeleteDialog(selectedServer) : undefined}
          focusAdminSection={focusAdminSection}
        />
      ) : (
        <McpServersLayout
          title={t('workspace.mcpServers.team.title')}
          subtitle={t('workspace.mcpServers.team.subtitle')}
          actions={
            <>
              <Button
                size="2"
                variant="soft"
                color="gray"
                className="touch-hit"
                onClick={() => router.push('/workspace/mcp-servers/personal/')}
                style={{ cursor: 'pointer' }}
              >
                {t('workspace.sidebar.nav.yourMcpServers')}
                <MaterialIcon name="arrow_forward" size={16} color="var(--gray-11)" />
              </Button>
              <McpAddServerButton onClick={store.openCreateCustom} />
              <WorkspaceHeaderIconButton icon="refresh" onClick={refresh} aria-label={t('action.refresh')} />
            </>
          }
          isLoading={store.isLoading}
          searchQuery={store.searchQuery}
          onSearchChange={store.setSearchQuery}
          instances={store.instances}
          templates={store.templates}
          catalog={store.templates.filter(isOfferedForNewServers)}
          catalogDescription={t('workspace.mcpServers.catalog.description')}
          emptyTitle={t('workspace.mcpServers.team.emptyTitle')}
          emptyDescription={t('workspace.mcpServers.team.emptyDescription')}
          isAdmin
          busyInstanceId={busyInstanceId}
          onOpen={openServer}
          onConnect={handleAuthenticate}
          onReconnect={handleAuthenticate}
          onEdit={(instance) => (canEdit(instance) ? store.openEditInstance(instance) : openServer(instance))}
          onAdd={store.openCreateFromTemplate}
          footer={
            <EnterpriseFeatureNotice
              title={t('workspace.mcpServers.enterprise.userServersTitle')}
              description={t('workspace.mcpServers.enterprise.userServersDescription')}
            />
          }
        />
      )}

      <McpInstanceConfigPanel
        state={store.configPanel}
        templates={store.templates}
        instances={store.instances}
        customStdioAllowed={store.customStdioAllowed}
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
        instance={authDialogInstance}
        template={store.templates.find((tpl) => tpl.typeId === authDialogInstance?.typeId) ?? null}
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

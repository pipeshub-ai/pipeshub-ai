'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { orgInstancesWithStatus, useMcpTeamStore } from './store';
import { McpServersApi } from '../api';
import { mcpOAuthFailureMessage, useMcpOAuthPopup } from '../hooks/use-mcp-oauth-popup';
import { McpAuthDialog, McpDisconnectDialog } from '../components';
import type { McpMyServerEntry, McpPersonalInstanceSummary, McpToolInfo } from '../types';
import { isPersonalMcpInstance } from '../types';
import { isOfferedForNewServers, replacementFor } from '../catalog-replacement';
import { UsersApi } from '../../users/api';
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
  const [disconnecting, setDisconnecting] = useState<McpMyServerEntry | null>(null);
  const [toolsState, setToolsState] = useState<Record<string, ToolsResult | undefined>>({});
  const [ownerNames, setOwnerNames] = useState<Record<string, string>>({});

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

  // Only the newest load's owner lookup may set the names: an older one can answer last.
  const ownerLookup = useRef(0);

  const loadData = useCallback(async () => {
    const s = useMcpTeamStore.getState();
    s.setLoading(true);
    try {
      const [catalogRes, myServersRes, allInstancesRes] = await Promise.all([
        McpServersApi.getCatalog({ limit: 200 }),
        McpServersApi.getMyMcpServers(false),
        McpServersApi.listInstances({ includePersonal: true }),
      ]);
      s.setTemplates(catalogRes.templates);
      s.setCustomStdioAllowed(catalogRes.customStdioAllowed === true);
      // The admin's own personal servers live on their personal page, not in the org catalog.
      s.setInstances(orgInstancesWithStatus(allInstancesRes.instances, myServersRes.instances));
      const userCreated: McpPersonalInstanceSummary[] = allInstancesRes.instances.filter((i) => isPersonalMcpInstance(i));
      s.setUserCreatedInstances(userCreated);
      const ownerIds = [...new Set(userCreated.map((i) => i.createdBy))];
      const lookup = ++ownerLookup.current;
      if (ownerIds.length === 0) {
        setOwnerNames({});
      } else {
        void UsersApi.getUsersByIds(ownerIds)
          .then((users) => Object.fromEntries(users.map((u) => [u.userId, u.name || u.email || u.userId])))
          .catch(() => ({}))
          .then((names) => {
            if (lookup === ownerLookup.current) setOwnerNames(names);
          });
      }
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
            notice: isOfferedForNewServers(detailsTemplate)
              ? null
              : t('workspace.mcpServers.replacedNotice', {
                  name: replacementFor(detailsTemplate, store.templates)?.displayName ?? detailsTemplate.replacedBy,
                }),
          }
        : null,
    [detailsTemplate, store.templates, t]
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
    onFailed: (failure) => {
      setBusyInstanceId(null);
      toast.error(mcpOAuthFailureMessage(t, failure));
      // The stored tokens are untouched by a failed sign-in, but show the list as it is now.
      void loadData();
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
        // A new sign-in replaces the stored tokens only when it succeeds, so a blocked or
        // cancelled popup leaves the working connection as it was.
        setBusyInstanceId(instance._id);
        void startOAuthPopup(instance._id);
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
          onAddInstance={
            detailsTemplate && isOfferedForNewServers(detailsTemplate)
              ? () => store.openCreateFromTemplate(detailsTemplate)
              : undefined
          }
          onManageInstance={(instance) => store.openEditInstance(instance)}
          onDeleteInstance={(instance) => store.openDeleteDialog(instance)}
          onAuthenticate={handleAuthenticate}
          onReauthenticate={(instance) => void handleReauthenticate(instance)}
          onDisconnect={setDisconnecting}
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
          userCreatedInstances={store.userCreatedInstances}
          ownerNames={ownerNames}
          onDeleteUserCreated={(instance) => store.openDeleteDialog(instance)}
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
        onSaved={() => void loadData()}
        onRequestDelete={(instance) => store.openDeleteDialog(instance)}
        busyInstanceId={busyInstanceId}
        onAuthenticate={handleAuthenticate}
        onReauthenticate={(instance) => void handleReauthenticate(instance)}
        onDisconnect={(instance) => void handleDisconnect(instance)}
      />

      <McpDisconnectDialog
        instance={disconnecting}
        onOpenChange={(open) => {
          if (!open) setDisconnecting(null);
        }}
        onConfirm={() => {
          if (disconnecting) void handleDisconnect(disconnecting);
          setDisconnecting(null);
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
        message={t(
          isPersonalMcpInstance(store.deleteTarget)
            ? 'workspace.mcpServers.deleteDialog.personalBody'
            : 'workspace.mcpServers.deleteDialog.body',
          { name: store.deleteTarget?.name ?? '' }
        )}
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

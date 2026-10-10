'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api';
import { ServiceGate } from '@/app/components/ui/service-gate';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectFeatureFlagsLoaded,
} from '@/lib/store/feature-flags-store';
import { ConfirmationDialog } from '../../components';
import { useMcpPersonalStore } from './store';
import { McpServersApi } from '../api';
import { mcpOAuthFailureMessage, useMcpOAuthPopup } from '../hooks/use-mcp-oauth-popup';
import type { McpMyServerEntry } from '../types';
import { McpInstanceConfigPanel } from '../team/components';
import { McpPersonalLayout, McpAuthDialog, McpAddServerDialog } from './components';
import { McpDisconnectDialog, McpToolRulesDialog } from '../components';
import { ruleToolsFromInfo } from '../tool-rules';

export default function PersonalMcpServersPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const store = useMcpPersonalStore();
  const [busyInstanceId, setBusyInstanceId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [rulesFor, setRulesFor] = useState<McpMyServerEntry | null>(null);
  const [disconnecting, setDisconnecting] = useState<McpMyServerEntry | null>(null);
  const rulesTools = useMemo(() => ruleToolsFromInfo(rulesFor?.tools ?? []), [rulesFor]);

  const loadData = useCallback(async () => {
    const s = useMcpPersonalStore.getState();
    s.setLoading(true);
    try {
      const [myServersRes, catalogRes] = await Promise.all([
        McpServersApi.getMyMcpServers(true),
        McpServersApi.getCatalog({ limit: 200 }),
      ]);
      s.setInstances(myServersRes.instances);
      s.setTemplates(catalogRes.templates);
    } catch {
      toast.error(t('workspace.mcpServers.toasts.loadError'));
    } finally {
      s.setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (flagsLoaded && !mcpEnabled) {
      router.replace('/workspace/general');
    }
  }, [flagsLoaded, mcpEnabled, router]);

  useEffect(() => {
    if (!flagsLoaded || !mcpEnabled) return;
    void loadData();
    return () => useMcpPersonalStore.getState().reset();
  }, [flagsLoaded, mcpEnabled, loadData]);

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
      store.openAuthDialog(instance);
    },
    [store, startOAuthPopup]
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
      store.openAuthDialog(instance);
    },
    [store, startOAuthPopup, t]
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
      void loadData();
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(t('workspace.mcpServers.toasts.deleteError'), detail ? { description: detail } : undefined);
    } finally {
      setIsDeleting(false);
    }
  }, [store, loadData, t]);

  if (!flagsLoaded || !mcpEnabled) return null;

  return (
    <ServiceGate services={['connector']}>
      <McpPersonalLayout
        instances={store.instances}
        templates={store.templates}
        isLoading={store.isLoading}
        searchQuery={store.searchQuery}
        busyInstanceId={busyInstanceId}
        onSearchChange={store.setSearchQuery}
        onRefresh={() => void loadData()}
        onAddServer={store.openAddDialog}
        onSetUp={store.openCreateFromTemplate}
        onAuthenticate={handleAuthenticate}
        onReauthenticate={(instance) => void handleReauthenticate(instance)}
        onRemoveCredentials={setDisconnecting}
        onToolApprovals={setRulesFor}
        onEdit={store.openEditInstance}
        onDelete={store.openDeleteDialog}
      />

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
        onSaved={() => void loadData()}
        onRequestDelete={store.openDeleteDialog}
        busyInstanceId={busyInstanceId}
        onAuthenticate={handleAuthenticate}
        onReauthenticate={(instance) => void handleReauthenticate(instance)}
        onDisconnect={(instance) => void handleRemoveCredentials(instance)}
      />

      <McpDisconnectDialog
        instance={disconnecting}
        onOpenChange={(open) => {
          if (!open && busyInstanceId !== disconnecting?._id) setDisconnecting(null);
        }}
        isLoading={disconnecting !== null && busyInstanceId === disconnecting._id}
        onConfirm={() => {
          const target = disconnecting;
          if (!target) return;
          void handleRemoveCredentials(target).finally(() => setDisconnecting(null));
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

      {rulesFor && (
        <McpToolRulesDialog
          open
          onOpenChange={(open) => {
            if (!open) setRulesFor(null);
          }}
          target={{ kind: 'personal', instanceId: rulesFor._id }}
          serverName={rulesFor.name}
          tools={rulesTools}
        />
      )}

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
    </ServiceGate>
  );
}

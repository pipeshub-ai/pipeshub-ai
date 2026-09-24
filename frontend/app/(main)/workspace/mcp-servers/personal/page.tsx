'use client';

import { useCallback, useEffect, useState } from 'react';
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
import { useMcpPersonalStore } from './store';
import { McpServersApi } from '../api';
import { useMcpOAuthPopup } from '../hooks/use-mcp-oauth-popup';
import type { McpMyServerEntry } from '../types';
import { McpPersonalLayout, McpAuthDialog } from './components';

export default function PersonalMcpServersPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const store = useMcpPersonalStore();
  const [busyInstanceId, setBusyInstanceId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    const s = useMcpPersonalStore.getState();
    s.setLoading(true);
    try {
      const res = await McpServersApi.getMyMcpServers(true);
      s.setInstances(res.instances);
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
      store.openAuthDialog(instance);
    },
    [store, startOAuthPopup]
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

  if (!flagsLoaded || !mcpEnabled) return null;

  return (
    <ServiceGate services={['connector']}>
      <McpPersonalLayout
        instances={store.instances}
        isLoading={store.isLoading}
        searchQuery={store.searchQuery}
        busyInstanceId={busyInstanceId}
        onSearchChange={store.setSearchQuery}
        onRefresh={() => void loadData()}
        onAuthenticate={handleAuthenticate}
        onReauthenticate={(instance) => void handleReauthenticate(instance)}
        onRemoveCredentials={(instance) => void handleRemoveCredentials(instance)}
      />

      <McpAuthDialog
        instance={store.authDialog.instance}
        open={store.authDialog.open}
        onOpenChange={(open) => {
          if (!open) store.closeAuthDialog();
        }}
        onAuthenticated={() => void loadData()}
      />
    </ServiceGate>
  );
}

'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useAIModelsStore } from './store';
import { AIModelsApi } from './api';
import type { AIModelProvider, ConfiguredModel } from './types';
import { DestructiveTypedConfirmationDialog } from '@/app/(main)/workspace/components';
import { ProviderGrid, ModelConfigDialog } from './components';

export default function AIModelsPage() {
  const { t } = useTranslation();
  const store = useAIModelsStore();
  const [isDeleting, setIsDeleting] = useState(false);

  const loadProviders = useCallback(async () => {
    const s = useAIModelsStore.getState();
    s.setLoadingProviders(true);
    try {
      const data = await AIModelsApi.getRegistry();
      s.setProviders(data.providers);
    } catch {
      toast.error(t('workspace.aiModels.toastLoadProvidersError'));
    } finally {
      s.setLoadingProviders(false);
    }
  }, [t]);

  const loadModels = useCallback(async () => {
    const s = useAIModelsStore.getState();
    s.setLoadingModels(true);
    try {
      const data = await AIModelsApi.getAllModels();
      s.setConfiguredModels(data.models as unknown as Record<string, ConfiguredModel[]>);
    } catch {
      toast.error(t('workspace.aiModels.toastLoadModelsError'));
    } finally {
      s.setLoadingModels(false);
    }
  }, [t]);

  useEffect(() => {
    void loadProviders();
    void loadModels();
    return () => useAIModelsStore.getState().reset();
  }, [loadProviders, loadModels]);

  const handleRefresh = useCallback(() => {
    void loadProviders();
    void loadModels();
  }, [loadProviders, loadModels]);

  const handleAdd = useCallback((provider: AIModelProvider, capability: string) => {
    useAIModelsStore.getState().openAddDialog(provider, capability);
  }, []);

  const handleEdit = useCallback((provider: AIModelProvider, capability: string, model: ConfiguredModel) => {
    useAIModelsStore.getState().openEditDialog(provider, capability, model);
  }, []);

  const handleSetDefault = useCallback(
    async (modelType: string, modelKey: string) => {
      try {
        await AIModelsApi.setDefault(modelType, modelKey);
        toast.success(t('workspace.aiModels.toastDefaultUpdated'));
        await loadModels();
      } catch {
        toast.error(t('workspace.aiModels.toastDefaultError'));
      }
    },
    [loadModels, t]
  );

  const handleDelete = useCallback(async () => {
    const target = useAIModelsStore.getState().deleteTarget;
    if (!target) return;
    setIsDeleting(true);
    try {
      await AIModelsApi.deleteProvider(target.modelType, target.modelKey);
      toast.success(t('workspace.aiModels.toastDeleted', { name: target.modelName }));
      useAIModelsStore.getState().closeDeleteDialog();
      await loadModels();
    } catch {
      toast.error(t('workspace.aiModels.toastDeleteError'));
    } finally {
      setIsDeleting(false);
    }
  }, [loadModels, t]);

  const isLoading = store.isLoadingProviders || store.isLoadingModels;
  const deleteKeyword = store.deleteTarget?.modelName ?? '';

  return (
    <ServiceGate services={['query']}>
      <ProviderGrid
        providers={store.providers}
        configuredModels={store.configuredModels}
        searchQuery={store.searchQuery}
        onSearchChange={store.setSearchQuery}
        mainSection={store.mainSection}
        onMainSectionChange={store.setMainSection}
        capabilitySection={store.capabilitySection}
        onCapabilitySectionChange={store.setCapabilitySection}
        onAdd={handleAdd}
        onEdit={handleEdit}
        onSetDefault={handleSetDefault}
        onDelete={(mt, mk, name) => store.openDeleteDialog(mt, mk, name)}
        isLoading={isLoading}
        onRefresh={handleRefresh}
      />

      <ModelConfigDialog
        open={store.dialogOpen}
        mode={store.dialogMode}
        provider={store.dialogProvider}
        capability={store.dialogCapability}
        editModel={store.dialogEditModel}
        onClose={store.closeDialog}
        onSaved={loadModels}
      />

      <DestructiveTypedConfirmationDialog
        open={store.deleteDialogOpen}
        onOpenChange={(open) => {
          if (!open) store.closeDeleteDialog();
        }}
        heading={t('workspace.aiModels.deleteDialogTitle')}
        body={
          <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
            {t('workspace.aiModels.deleteTypedConfirmBody', {
              name: store.deleteTarget?.modelName ?? '',
            })}
          </Text>
        }
        confirmationKeyword={deleteKeyword}
        confirmInputLabel={t('workspace.aiModels.typeModelNameToConfirm', {
          keyword: deleteKeyword,
        })}
        primaryButtonText={t('workspace.aiModels.delete')}
        cancelLabel={t('workspace.aiModels.cancel')}
        isLoading={isDeleting}
        confirmLoadingLabel={t('action.deleting')}
        onConfirm={() => void handleDelete()}
      />
    </ServiceGate>
  );
}

'use client';

import React, { useCallback, useEffect } from 'react';
import { Box, Button, Dialog, Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { useAIModelsStore } from './store';
import { AIModelsApi } from './api';
import type { AIModelProvider, ConfiguredModel } from './types';
import { ProviderGrid, ModelConfigDialog } from './components';
import deleteFooterStyles from './delete-dialog-footer.module.css';

export default function AIModelsPage() {
  const { t } = useTranslation();
  const store = useAIModelsStore();
  const { addToast } = useToastStore();

  const loadProviders = useCallback(async () => {
    const s = useAIModelsStore.getState();
    s.setLoadingProviders(true);
    try {
      const data = await AIModelsApi.getRegistry();
      s.setProviders(data.providers);
    } catch {
      addToast({ title: t('workspace.aiModels.toastLoadProvidersError'), variant: 'error' });
    } finally {
      s.setLoadingProviders(false);
    }
  }, [addToast, t]);

  const loadModels = useCallback(async () => {
    const s = useAIModelsStore.getState();
    s.setLoadingModels(true);
    try {
      const data = await AIModelsApi.getAllModels();
      s.setConfiguredModels(data.models as unknown as Record<string, ConfiguredModel[]>);
    } catch {
      addToast({ title: t('workspace.aiModels.toastLoadModelsError'), variant: 'error' });
    } finally {
      s.setLoadingModels(false);
    }
  }, [addToast, t]);

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
        addToast({ title: t('workspace.aiModels.toastDefaultUpdated'), variant: 'success' });
        await loadModels();
      } catch {
        addToast({ title: t('workspace.aiModels.toastDefaultError'), variant: 'error' });
      }
    },
    [addToast, loadModels, t]
  );

  const handleDelete = useCallback(async () => {
    const target = useAIModelsStore.getState().deleteTarget;
    if (!target) return;
    try {
      await AIModelsApi.deleteProvider(target.modelType, target.modelKey);
      addToast({ title: t('workspace.aiModels.toastDeleted', { name: target.modelName }), variant: 'success' });
      useAIModelsStore.getState().closeDeleteDialog();
      await loadModels();
    } catch {
      addToast({ title: t('workspace.aiModels.toastDeleteError'), variant: 'error' });
    }
  }, [addToast, loadModels, t]);

  const isLoading = store.isLoadingProviders || store.isLoadingModels;

  return (
    <>
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

      <Dialog.Root
        open={store.deleteDialogOpen}
        onOpenChange={(o) => {
          if (!o) store.closeDeleteDialog();
        }}
      >
        <Dialog.Content style={{ maxWidth: 400 }}>
          <Dialog.Title>{t('workspace.aiModels.deleteDialogTitle')}</Dialog.Title>
          <Dialog.Description size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.aiModels.deleteDialogDescription', {
              name: store.deleteTarget?.modelName ?? '',
            })}
          </Dialog.Description>
          <Flex
            direction={{ initial: 'column', sm: 'row' }}
            align={{ initial: 'stretch', sm: 'center' }}
            gap="3"
            wrap="wrap"
            justify={{ initial: 'start', sm: 'end' }}
            style={{ marginTop: 16, width: '100%' }}
          >
            <Box width={{ initial: '100%', sm: 'auto' }} style={{ minWidth: 0 }}>
              <Dialog.Close>
                <Button
                  variant="soft"
                  color="gray"
                  className={deleteFooterStyles.footerButton}
                  style={{ cursor: 'pointer' }}
                >
                  {t('workspace.aiModels.cancel')}
                </Button>
              </Dialog.Close>
            </Box>
            <Box width={{ initial: '100%', sm: 'auto' }} style={{ minWidth: 0 }}>
              <Button
                color="red"
                className={deleteFooterStyles.footerButton}
                onClick={handleDelete}
                style={{ cursor: 'pointer' }}
              >
                {t('workspace.aiModels.delete')}
              </Button>
            </Box>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}

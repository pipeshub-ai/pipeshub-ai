'use client';

import React, { useCallback, useEffect } from 'react';
import { Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { useToastStore } from '@/lib/store/toast-store';
import { useAIModelsStore } from './store';
import { AIModelsApi } from './api';
import type { AIModelProvider, ConfiguredModel } from './types';
import { ProviderGrid, ModelConfigDialog } from './components';

export default function AIModelsPage() {
  const store = useAIModelsStore();
  const { addToast } = useToastStore();

  const loadProviders = useCallback(async () => {
    store.setLoadingProviders(true);
    try {
      const data = await AIModelsApi.getRegistry();
      store.setProviders(data.providers);
    } catch {
      addToast({ title: 'Failed to load AI model providers', variant: 'error' });
    } finally {
      store.setLoadingProviders(false);
    }
  }, []);

  const loadModels = useCallback(async () => {
    store.setLoadingModels(true);
    try {
      const data = await AIModelsApi.getAllModels();
      store.setConfiguredModels(data.models as unknown as Record<string, ConfiguredModel[]>);
    } catch {
      addToast({ title: 'Failed to load configured models', variant: 'error' });
    } finally {
      store.setLoadingModels(false);
    }
  }, []);

  useEffect(() => {
    loadProviders();
    loadModels();
    return () => store.reset();
  }, []);

  const handleAdd = useCallback(
    (provider: AIModelProvider, capability: string) => {
      store.openAddDialog(provider, capability);
    },
    []
  );

  const handleEdit = useCallback(
    (provider: AIModelProvider, capability: string, model: ConfiguredModel) => {
      store.openEditDialog(provider, capability, model);
    },
    []
  );

  const handleSetDefault = useCallback(async (modelType: string, modelKey: string) => {
    try {
      await AIModelsApi.setDefault(modelType, modelKey);
      addToast({ title: 'Default model updated', variant: 'success' });
      loadModels();
    } catch {
      addToast({ title: 'Failed to set default model', variant: 'error' });
    }
  }, []);

  const handleDelete = useCallback(async () => {
    const target = store.deleteTarget;
    if (!target) return;
    try {
      await AIModelsApi.deleteProvider(target.modelType, target.modelKey);
      addToast({ title: `Deleted ${target.modelName}`, variant: 'success' });
      store.closeDeleteDialog();
      loadModels();
    } catch {
      addToast({ title: 'Failed to delete model', variant: 'error' });
    }
  }, [store.deleteTarget]);

  const isLoading = store.isLoadingProviders || store.isLoadingModels;

  return (
    <>
      <ProviderGrid
        providers={store.providers}
        configuredModels={store.configuredModels}
        searchQuery={store.searchQuery}
        onSearchChange={store.setSearchQuery}
        activeTab={store.filterTab}
        onTabChange={store.setFilterTab}
        onAdd={handleAdd}
        onEdit={handleEdit}
        onSetDefault={handleSetDefault}
        onDelete={(mt, mk, name) => store.openDeleteDialog(mt, mk, name)}
        isLoading={isLoading}
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
        onOpenChange={(o) => { if (!o) store.closeDeleteDialog(); }}
      >
        <Dialog.Content style={{ maxWidth: 400 }}>
          <Dialog.Title>Delete Model</Dialog.Title>
          <Dialog.Description size="2" style={{ color: 'var(--gray-11)' }}>
            Are you sure you want to delete &ldquo;{store.deleteTarget?.modelName}&rdquo;? This
            action cannot be undone.
          </Dialog.Description>
          <Flex gap="3" justify="end" style={{ marginTop: 16 }}>
            <Dialog.Close>
              <Button variant="soft" color="gray" style={{ cursor: 'pointer' }}>
                Cancel
              </Button>
            </Dialog.Close>
            <Button color="red" onClick={handleDelete} style={{ cursor: 'pointer' }}>
              Delete
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}

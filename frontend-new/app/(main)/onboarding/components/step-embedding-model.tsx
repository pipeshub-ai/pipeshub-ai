'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Flex, Box, Text, Button, Dialog } from '@radix-ui/themes';
import { useOnboardingStore } from '../store';
import { useToastStore } from '@/lib/store/toast-store';
import { AIModelsApi } from '@/app/(main)/workspace/ai-models/api';
import type { AIModelProvider, ConfiguredModel, CapabilitySection } from '@/app/(main)/workspace/ai-models/types';
import type { MainSection } from '@/app/(main)/workspace/ai-models/store';
import { ProviderGrid, ModelConfigDialog } from '@/app/(main)/workspace/ai-models/components';
import type { OnboardingStepId } from '../types';

interface StepEmbeddingModelProps {
  onSuccess: (nextStep: OnboardingStepId | null) => void;
  systemStepIndex: number;
  totalSystemSteps: number;
}

export function StepEmbeddingModel({
  systemStepIndex,
  totalSystemSteps,
}: StepEmbeddingModelProps) {
  const { markStepCompleted, unmarkStepCompleted } = useOnboardingStore();
  const { addToast } = useToastStore();

  const [providers, setProviders] = useState<AIModelProvider[]>([]);
  const [configuredModels, setConfiguredModels] = useState<Record<string, ConfiguredModel[]>>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [mainSection, setMainSection] = useState<MainSection>('providers');
  const [capabilitySection, setCapabilitySection] = useState<CapabilitySection>('embedding');
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [loadingModels, setLoadingModels] = useState(true);
  const [modelsLoaded, setModelsLoaded] = useState(false);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogMode, setDialogMode] = useState<'add' | 'edit'>('add');
  const [dialogProvider, setDialogProvider] = useState<AIModelProvider | null>(null);
  const [dialogCapability, setDialogCapability] = useState<string | null>(null);
  const [dialogEditModel, setDialogEditModel] = useState<ConfiguredModel | null>(null);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    modelType: string;
    modelKey: string;
    modelName: string;
  } | null>(null);

  const loadProviders = useCallback(async () => {
    setLoadingProviders(true);
    try {
      const data = await AIModelsApi.getRegistry({ capability: 'embedding' });
      setProviders(data.providers);
    } catch {
      addToast({ title: 'Failed to load embedding providers', variant: 'error' });
    } finally {
      setLoadingProviders(false);
    }
  }, [addToast]);

  const loadModels = useCallback(async () => {
    setLoadingModels(true);
    try {
      const data = await AIModelsApi.getAllModels();
      setConfiguredModels(data.models as unknown as Record<string, ConfiguredModel[]>);
      setModelsLoaded(true);
    } catch {
      addToast({ title: 'Failed to load configured models', variant: 'error' });
      setModelsLoaded(true);
    } finally {
      setLoadingModels(false);
    }
  }, [addToast]);

  useEffect(() => {
    loadProviders();
    loadModels();
  }, [loadProviders, loadModels]);

  useEffect(() => {
    if (!modelsLoaded) return;
    const embeddings = configuredModels.embedding ?? [];
    if (embeddings.length > 0) {
      markStepCompleted('embedding-model');
    } else {
      unmarkStepCompleted('embedding-model');
    }
  }, [configuredModels, modelsLoaded, markStepCompleted, unmarkStepCompleted]);

  const closeDialog = useCallback(() => {
    setDialogOpen(false);
    setDialogProvider(null);
    setDialogCapability(null);
    setDialogEditModel(null);
  }, []);

  const handleAdd = useCallback((provider: AIModelProvider, capability: string) => {
    setDialogMode('add');
    setDialogProvider(provider);
    setDialogCapability(capability);
    setDialogEditModel(null);
    setDialogOpen(true);
  }, []);

  const handleEdit = useCallback(
    (provider: AIModelProvider, capability: string, model: ConfiguredModel) => {
      setDialogMode('edit');
      setDialogProvider(provider);
      setDialogCapability(capability);
      setDialogEditModel(model);
      setDialogOpen(true);
    },
    []
  );

  const handleSetDefault = useCallback(
    async (modelType: string, modelKey: string) => {
      try {
        await AIModelsApi.setDefault(modelType, modelKey);
        addToast({ title: 'Default model updated', variant: 'success' });
        loadModels();
      } catch {
        addToast({ title: 'Failed to set default model', variant: 'error' });
      }
    },
    [addToast, loadModels]
  );

  const openDeleteDialog = useCallback((modelType: string, modelKey: string, modelName: string) => {
    setDeleteTarget({ modelType, modelKey, modelName });
    setDeleteDialogOpen(true);
  }, []);

  const closeDeleteDialog = useCallback(() => {
    setDeleteDialogOpen(false);
    setDeleteTarget(null);
  }, []);

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    try {
      await AIModelsApi.deleteProvider(deleteTarget.modelType, deleteTarget.modelKey);
      addToast({ title: `Deleted ${deleteTarget.modelName}`, variant: 'success' });
      closeDeleteDialog();
      loadModels();
    } catch {
      addToast({ title: 'Failed to delete model', variant: 'error' });
    }
  }, [deleteTarget, addToast, closeDeleteDialog, loadModels]);

  const handleRefresh = useCallback(() => {
    void loadProviders();
    void loadModels();
  }, [loadProviders, loadModels]);

  const isLoading = loadingProviders || loadingModels;
  const embedCount = configuredModels.embedding?.length ?? 0;

  return (
    <>
      <Box
        style={{
          backgroundColor: 'var(--gray-2)',
          border: '1px solid var(--gray-4)',
          borderRadius: 'var(--radius-3)',
          width: 'min(1100px, 100%)',
          maxHeight: '100%',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <Box
          style={{
            flexShrink: 0,
            padding: '24px 24px 16px',
            borderBottom: '1px solid var(--gray-4)',
          }}
        >
          <Text
            as="div"
            size="1"
            style={{ color: 'var(--gray-9)', marginBottom: '4px', letterSpacing: '0.02em' }}
          >
            System Configuration
          </Text>
          <Text as="div" size="4" weight="bold" style={{ color: 'var(--gray-12)' }}>
            Step {systemStepIndex}/{totalSystemSteps}: Configure Embedding Model*
          </Text>
        </Box>

        <Box style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '24px' }}>
          <Flex direction="column" gap="5">
            {!isLoading && embedCount === 0 && (
              <Text size="2" style={{ color: 'var(--gray-11)', display: 'block' }}>
                Add an embedding provider below to enable semantic search across documents, or use
                Next to continue and configure this later in workspace settings.
              </Text>
            )}
            <ProviderGrid
              layout="embedded"
              providers={providers}
              configuredModels={configuredModels}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              mainSection={mainSection}
              onMainSectionChange={setMainSection}
              capabilitySection={capabilitySection}
              onCapabilitySectionChange={setCapabilitySection}
              onAdd={handleAdd}
              onEdit={handleEdit}
              onSetDefault={handleSetDefault}
              onDelete={openDeleteDialog}
              isLoading={isLoading}
              onRefresh={handleRefresh}
            />
          </Flex>
        </Box>
      </Box>

      <ModelConfigDialog
        open={dialogOpen}
        mode={dialogMode}
        provider={dialogProvider}
        capability={dialogCapability}
        editModel={dialogEditModel}
        onClose={closeDialog}
        onSaved={loadModels}
      />

      <Dialog.Root
        open={deleteDialogOpen}
        onOpenChange={(o) => {
          if (!o) closeDeleteDialog();
        }}
      >
        <Dialog.Content style={{ maxWidth: 400 }}>
          <Dialog.Title>Delete Model</Dialog.Title>
          <Dialog.Description size="2" style={{ color: 'var(--gray-11)' }}>
            Are you sure you want to delete &ldquo;{deleteTarget?.modelName}&rdquo;? This action
            cannot be undone.
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

'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useOnboardingStore } from '../store';
import { DestructiveTypedConfirmationDialog } from '@/app/(main)/workspace/components';
import { toast } from '@/lib/store/toast-store';
import { AIModelsApi } from '@/app/(main)/workspace/ai-models/api';
import type { AIModelProvider, ConfiguredModel, CapabilitySection } from '@/app/(main)/workspace/ai-models/types';
import type { MainSection } from '@/app/(main)/workspace/ai-models/store';
import { ProviderGrid, ModelConfigDialog } from '@/app/(main)/workspace/ai-models/components';

/** If the registry includes `providerId: 'default'`, show it first; otherwise preserve API order. */
function orderDefaultEmbeddingProviderFirst(providers: AIModelProvider[]): AIModelProvider[] {
  const list = [...providers];
  const idx = list.findIndex((p) => p.providerId === 'default');
  if (idx <= 0) return list;
  const [row] = list.splice(idx, 1);
  list.unshift(row);
  return list;
}

interface StepEmbeddingModelProps {
  systemStepIndex: number;
  totalSystemSteps: number;
  /** Set `true` from the onboarding footer when the user clicks Skip (system default path). */
  embeddingDefaultDialog: boolean;
  setEmbeddingDefaultDialog: React.Dispatch<React.SetStateAction<boolean>>;
  /** Fired after the embedding registry loads so the footer can show Skip only when `default` exists. */
  onRegistryHasSystemDefaultEmbedding?: (hasDefault: boolean) => void;
}

export function StepEmbeddingModel({
  systemStepIndex,
  totalSystemSteps,
  embeddingDefaultDialog,
  setEmbeddingDefaultDialog,
  onRegistryHasSystemDefaultEmbedding,
}: StepEmbeddingModelProps) {
  const { t } = useTranslation();
  const { markStepCompleted, unmarkStepCompleted } = useOnboardingStore();
  const [isDeleting, setIsDeleting] = useState(false);

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
      const raw = data.providers ?? [];
      setProviders(orderDefaultEmbeddingProviderFirst(raw));
      onRegistryHasSystemDefaultEmbedding?.(raw.some((p) => p.providerId === 'default'));
    } catch {
      toast.error(t('onboarding.failedToLoadEmbeddingProviders'));
      onRegistryHasSystemDefaultEmbedding?.(false);
    } finally {
      setLoadingProviders(false);
    }
  }, [onRegistryHasSystemDefaultEmbedding, t]);

  const loadModels = useCallback(async () => {
    setLoadingModels(true);
    try {
      const data = await AIModelsApi.getAllModels();
      setConfiguredModels(data.models as unknown as Record<string, ConfiguredModel[]>);
      setModelsLoaded(true);
    } catch {
      toast.error(t('onboarding.failedToLoadConfiguredModels'));
      setModelsLoaded(true);
    } finally {
      setLoadingModels(false);
    }
  }, [t]);

  useEffect(() => {
    loadProviders();
    loadModels();
  }, [loadProviders, loadModels]);

  useEffect(() => {
    if (!embeddingDefaultDialog) return;
    if (loadingProviders) return;
    const defaultFromRegistry = providers.find((p) => p.providerId === 'default');
    if (!defaultFromRegistry) {
      toast.error(t('onboarding.embeddingDefaultNotInRegistry'));
      setEmbeddingDefaultDialog(false);
      return;
    }
    setDialogMode('add');
    setDialogProvider(defaultFromRegistry);
    setDialogCapability('embedding');
    setDialogEditModel(null);
    setDialogOpen(true);
  }, [
    embeddingDefaultDialog,
    loadingProviders,
    providers,
    setEmbeddingDefaultDialog,
    t,
  ]);

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
    setEmbeddingDefaultDialog(false);
  }, [setEmbeddingDefaultDialog]);

  const handleModelConfigSaved = useCallback(() => {
    void loadModels();
    setEmbeddingDefaultDialog(false);
  }, [loadModels, setEmbeddingDefaultDialog]);

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
        toast.success('Default model updated');
        loadModels();
      } catch {
        toast.error('Failed to set default model');
      }
    },
    [loadModels]
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
    setIsDeleting(true);
    try {
      await AIModelsApi.deleteProvider(deleteTarget.modelType, deleteTarget.modelKey);
      toast.success(t('workspace.aiModels.toastDeleted', { name: deleteTarget.modelName }));
      closeDeleteDialog();
      loadModels();
    } catch {
      toast.error(t('workspace.aiModels.toastDeleteError'));
    } finally {
      setIsDeleting(false);
    }
  }, [deleteTarget, closeDeleteDialog, loadModels, t]);

  const handleRefresh = useCallback(() => {
    void loadProviders();
    void loadModels();
  }, [loadProviders, loadModels]);

  const isLoading = loadingProviders || loadingModels;
  const embedCount = configuredModels.embedding?.length ?? 0;
  const registryHasSystemDefaultEmbedding = providers.some((p) => p.providerId === 'default');
  const deleteKeyword = deleteTarget?.modelName ?? '';

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
                {registryHasSystemDefaultEmbedding
                  ? t('onboarding.embeddingStepHint')
                  : t('onboarding.embeddingStepHintNoRegistryDefault')}
              </Text>
            )}
            <ProviderGrid
              layout="embedded"
              hideCapabilityBadges
              showEmbeddingBuiltinPlaceholder={false}
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
        onSaved={handleModelConfigSaved}
      />

      <DestructiveTypedConfirmationDialog
        open={deleteDialogOpen}
        onOpenChange={(open) => {
          if (!open) closeDeleteDialog();
        }}
        heading={t('workspace.aiModels.deleteDialogTitle')}
        body={
          <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
            {t('workspace.aiModels.deleteTypedConfirmBody', {
              name: deleteTarget?.modelName ?? '',
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
    </>
  );
}

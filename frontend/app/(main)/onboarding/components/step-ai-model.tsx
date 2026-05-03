'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api/api-error';
import { DestructiveTypedConfirmationDialog } from '@/app/(main)/workspace/components';
import { useOnboardingStore } from '../store';
import { AIModelsApi } from '@/app/(main)/workspace/ai-models/api';
import type { AIModelProvider, ConfiguredModel, CapabilitySection } from '@/app/(main)/workspace/ai-models/types';
import type { MainSection } from '@/app/(main)/workspace/ai-models/store';
import { ProviderGrid, ModelConfigDialog } from '@/app/(main)/workspace/ai-models/components';
interface StepAiModelProps {
  systemStepIndex: number;
  totalSystemSteps: number;
}

export function StepAiModel({ systemStepIndex, totalSystemSteps }: StepAiModelProps) {
  const { t } = useTranslation();
  const { markStepCompleted, unmarkStepCompleted } = useOnboardingStore();
  const [isDeleting, setIsDeleting] = useState(false);

  const [providers, setProviders] = useState<AIModelProvider[]>([]);
  const [configuredModels, setConfiguredModels] = useState<Record<string, ConfiguredModel[]>>({});
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

  const [searchQuery, setSearchQuery] = useState('');
  const [mainSection, setMainSection] = useState<MainSection>('providers');
  const [capabilitySection, setCapabilitySection] = useState<CapabilitySection>('text_generation');

  const loadProviders = useCallback(async () => {
    setLoadingProviders(true);
    try {
      const data = await AIModelsApi.getRegistry({ capability: 'text_generation' });
      setProviders(data.providers);
    } catch {
      toast.error(t('onboarding.stepAiModel.loadProvidersError'));
    } finally {
      setLoadingProviders(false);
    }
  }, []);

  const loadModels = useCallback(async () => {
    setLoadingModels(true);
    try {
      const data = await AIModelsApi.getAllModels();
      setConfiguredModels(data.models as unknown as Record<string, ConfiguredModel[]>);
      setModelsLoaded(true);
    } catch {
      toast.error(t('onboarding.stepAiModel.loadModelsError'));
      setModelsLoaded(true);
    } finally {
      setLoadingModels(false);
    }
  }, []);

  useEffect(() => {
    loadProviders();
    loadModels();
  }, [loadProviders, loadModels]);

  useEffect(() => {
    if (!modelsLoaded) return;
    const llms = configuredModels.llm ?? [];
    if (llms.length > 0) {
      markStepCompleted('ai-model');
    } else {
      unmarkStepCompleted('ai-model');
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
        toast.success(t('onboarding.stepAiModel.setDefaultSuccess'));
        loadModels();
      } catch {
        toast.error(t('onboarding.stepAiModel.setDefaultError'));
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
    } catch (error: unknown) {
      const detail =
        isProcessedError(error) && error.message.trim() ? error.message.trim() : undefined;
      toast.error(t('workspace.aiModels.toastDeleteError'), {
        ...(detail ? { description: detail } : {}),
      });
    } finally {
      setIsDeleting(false);
    }
  }, [deleteTarget, closeDeleteDialog, loadModels, t]);

  const handleRefresh = useCallback(() => {
    void loadProviders();
    void loadModels();
  }, [loadProviders, loadModels]);

  const isLoading = loadingProviders || loadingModels;
  const llmCount = configuredModels.llm?.length ?? 0;
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
            padding: '12px 16px 12px',
            borderBottom: '1px solid var(--gray-4)',
          }}
        >
          <Text
            as="div"
            size="1"
            style={{ color: 'var(--gray-9)', marginBottom: '4px', letterSpacing: '0.02em' }}
          >
            {t('onboarding.systemConfig')}
          </Text>
          <Text as="div" size="4" weight="bold" style={{ color: 'var(--gray-12)' }}>
            {t('onboarding.stepHeading', { current: systemStepIndex, total: totalSystemSteps, name: t('onboarding.stepAiModel.stepName') })}
          </Text>
        </Box>

        
        <Box style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px 20px' }}>
          {!isLoading && llmCount === 0 && (
            <Text size="2" style={{ color: 'var(--gray-11)', marginBottom: '12px', display: 'block' }}>
              {t('onboarding.stepAiModel.addLlmHint')}
            </Text>
          )}
          <ProviderGrid
            layout="embedded"
            hideCapabilityBadges
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

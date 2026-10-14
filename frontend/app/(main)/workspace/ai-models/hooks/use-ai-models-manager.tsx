'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api/api-error';
import { DestructiveTypedConfirmationDialog } from '@/app/(main)/workspace/components';
import { AIModelsApi } from '../api';
import type { AIModelProvider, CapabilitySection, ConfiguredModel } from '../types';
import { CAPABILITY_TO_MODEL_TYPE } from '../types';
import type { MainSection } from '../store';
import { ModelConfigDialog, type ModelConfigSaveResult } from '../components/model-config-dialog';
import { RotateCredentialsDialog } from '../components/rotate-credentials-dialog';

interface UseAiModelsManagerOptions {
  enabled?: boolean;
  registryCapability?: string;
  initialCapabilitySection?: CapabilitySection;
  orderProviders?: (providers: AIModelProvider[]) => AIModelProvider[];
  onProvidersLoaded?: (providers: AIModelProvider[]) => void;
  providerErrorKey: string;
  modelsErrorKey: string;
  setDefaultSuccessKey?: string;
  setDefaultErrorKey?: string;
  onModelSaved?: (result: ModelConfigSaveResult) => void;
}

export function useAiModelsManager({
  enabled = true,
  registryCapability,
  initialCapabilitySection = 'text_generation',
  orderProviders,
  onProvidersLoaded,
  providerErrorKey,
  modelsErrorKey,
  setDefaultSuccessKey = 'workspace.aiModels.toastDefaultUpdated',
  setDefaultErrorKey = 'workspace.aiModels.toastDefaultError',
  onModelSaved,
}: UseAiModelsManagerOptions) {
  const { t } = useTranslation();
  const [providers, setProviders] = useState<AIModelProvider[]>([]);
  const [configuredModels, setConfiguredModels] = useState<Record<string, ConfiguredModel[]>>({});
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [loadingModels, setLoadingModels] = useState(true);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [mainSection, setMainSection] = useState<MainSection>('providers');
  const [capabilitySection, setCapabilitySection] = useState<CapabilitySection>(initialCapabilitySection);
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
  const [isDeleting, setIsDeleting] = useState(false);
  const [rotateTarget, setRotateTarget] = useState<ConfiguredModel | null>(null);

  const loadProviders = useCallback(async () => {
    setLoadingProviders(true);
    try {
      const data = await AIModelsApi.getRegistry(
        registryCapability ? { capability: registryCapability } : undefined,
      );
      const next = orderProviders ? orderProviders(data.providers ?? []) : (data.providers ?? []);
      setProviders(next);
      onProvidersLoaded?.(data.providers ?? []);
    } catch {
      toast.error(t(providerErrorKey));
      onProvidersLoaded?.([]);
    } finally {
      setLoadingProviders(false);
    }
  }, [onProvidersLoaded, orderProviders, providerErrorKey, registryCapability, t]);

  const loadModels = useCallback(async () => {
    setLoadingModels(true);
    try {
      const data = await AIModelsApi.getAllModels();
      setConfiguredModels(data.models as unknown as Record<string, ConfiguredModel[]>);
      setModelsLoaded(true);
    } catch {
      toast.error(t(modelsErrorKey));
      setModelsLoaded(true);
    } finally {
      setLoadingModels(false);
    }
  }, [modelsErrorKey, t]);

  useEffect(() => {
    if (!enabled) return;
    void loadProviders();
    void loadModels();
  }, [enabled, loadProviders, loadModels]);

  const openAdd = useCallback((provider: AIModelProvider, capability: string) => {
    setDialogMode('add');
    setDialogProvider(provider);
    setDialogCapability(capability);
    setDialogEditModel(null);
    setDialogOpen(true);
  }, []);

  const openEdit = useCallback((provider: AIModelProvider, capability: string, model: ConfiguredModel) => {
    setDialogMode('edit');
    setDialogProvider(provider);
    setDialogCapability(capability);
    setDialogEditModel(model);
    setDialogOpen(true);
  }, []);

  const closeDialog = useCallback(() => {
    setDialogOpen(false);
    setDialogProvider(null);
    setDialogCapability(null);
    setDialogEditModel(null);
  }, []);

  const handleSetDefault = useCallback(async (modelType: string, modelKey: string) => {
    try {
      await AIModelsApi.setDefault(modelType, modelKey);
      toast.success(t(setDefaultSuccessKey));
      await loadModels();
    } catch {
      toast.error(t(setDefaultErrorKey));
    }
  }, [loadModels, setDefaultErrorKey, setDefaultSuccessKey, t]);

  const openDelete = useCallback((modelType: string, modelKey: string, modelName: string) => {
    setDeleteTarget({ modelType, modelKey, modelName });
    setDeleteDialogOpen(true);
  }, []);

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await AIModelsApi.deleteProvider(deleteTarget.modelType, deleteTarget.modelKey);
      toast.success(t('workspace.aiModels.toastDeleted', { name: deleteTarget.modelName }));
      setDeleteDialogOpen(false);
      setDeleteTarget(null);
      await loadModels();
    } catch (error: unknown) {
      const detail = isProcessedError(error) && error.message.trim() ? error.message.trim() : undefined;
      toast.error(t('workspace.aiModels.toastDeleteError'), {
        ...(detail ? { description: detail } : {}),
      });
    } finally {
      setIsDeleting(false);
    }
  }, [deleteTarget, loadModels, t]);

  const existingModelsCount = useMemo(() => {
    if (dialogMode !== 'add' || !dialogCapability) return 0;
    const modelType = CAPABILITY_TO_MODEL_TYPE[dialogCapability];
    if (!modelType) return 0;
    return configuredModels[modelType]?.length ?? 0;
  }, [configuredModels, dialogCapability, dialogMode]);

  const deleteKeyword = deleteTarget?.modelName ?? '';

  const dialogs = (
    <>
      <ModelConfigDialog
        open={dialogOpen}
        mode={dialogMode}
        provider={dialogProvider}
        capability={dialogCapability}
        editModel={dialogEditModel}
        existingModelsCount={existingModelsCount}
        onClose={closeDialog}
        onSaved={(result) => {
          onModelSaved?.(result);
          void loadModels();
        }}
      />
      <DestructiveTypedConfirmationDialog
        open={deleteDialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteDialogOpen(false);
            setDeleteTarget(null);
          }
        }}
        heading={t('workspace.aiModels.deleteDialogTitle')}
        body={
          <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
            {t('workspace.aiModels.deleteTypedConfirmBody', { name: deleteKeyword })}
          </Text>
        }
        confirmationKeyword={deleteKeyword}
        confirmInputLabel={t('workspace.aiModels.typeModelNameToConfirm', { keyword: deleteKeyword })}
        primaryButtonText={t('workspace.aiModels.delete')}
        cancelLabel={t('workspace.aiModels.cancel')}
        isLoading={isDeleting}
        confirmLoadingLabel={t('action.deleting')}
        onConfirm={() => void handleDelete()}
      />
      <RotateCredentialsDialog
        model={rotateTarget}
        onClose={() => setRotateTarget(null)}
        onSaved={() => { void loadModels(); }}
      />
    </>
  );

  return {
    providers,
    configuredModels,
    loadingProviders,
    loadingModels,
    modelsLoaded,
    isLoading: loadingProviders || loadingModels,
    searchQuery,
    setSearchQuery,
    mainSection,
    setMainSection,
    capabilitySection,
    setCapabilitySection,
    loadProviders,
    loadModels,
    openAdd,
    openEdit,
    openDelete,
    openRotate: setRotateTarget,
    handleSetDefault,
    dialogs,
  };
}

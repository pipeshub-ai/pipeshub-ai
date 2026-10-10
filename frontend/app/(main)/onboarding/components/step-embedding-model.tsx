'use client';

import React, { useCallback, useEffect } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useOnboardingStore } from '../store';
import { toast } from '@/lib/store/toast-store';
import { ProviderGrid } from '@/app/(main)/workspace/ai-models/components';
import type { AIModelProvider } from '@/app/(main)/workspace/ai-models/types';
import { useAiModelsManager } from '@/app/(main)/workspace/ai-models/hooks/use-ai-models-manager';

const MODEL_ADDED_TOAST_DURATION_MS = 6000;

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
  embeddingDefaultDialog: boolean;
  setEmbeddingDefaultDialog: React.Dispatch<React.SetStateAction<boolean>>;
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
  const onProvidersLoaded = useCallback((providers: AIModelProvider[]) => {
    onRegistryHasSystemDefaultEmbedding?.(providers.some((provider) => provider.providerId === 'default'));
  }, [onRegistryHasSystemDefaultEmbedding]);

  const manager = useAiModelsManager({
    registryCapability: 'embedding',
    initialCapabilitySection: 'embedding',
    orderProviders: orderDefaultEmbeddingProviderFirst,
    onProvidersLoaded,
    providerErrorKey: 'onboarding.failedToLoadEmbeddingProviders',
    modelsErrorKey: 'onboarding.failedToLoadConfiguredModels',
    onModelSaved: (result) => {
      if (result.mode === 'add') {
        toast.success(t('onboarding.toastModelAddedEmbedding', { name: result.modelName }), {
          duration: MODEL_ADDED_TOAST_DURATION_MS,
        });
      }
      setEmbeddingDefaultDialog(false);
    },
  });

  useEffect(() => {
    if (!embeddingDefaultDialog || manager.loadingProviders) return;
    const defaultFromRegistry = manager.providers.find((provider) => provider.providerId === 'default');
    if (!defaultFromRegistry) {
      toast.error(t('onboarding.embeddingDefaultNotInRegistry'));
      setEmbeddingDefaultDialog(false);
      return;
    }
    manager.openAdd(defaultFromRegistry, 'embedding');
    setEmbeddingDefaultDialog(false);
  }, [
    embeddingDefaultDialog,
    manager.loadingProviders,
    manager.openAdd,
    manager.providers,
    setEmbeddingDefaultDialog,
    t,
  ]);

  useEffect(() => {
    if (!manager.modelsLoaded) return;
    const embeddings = manager.configuredModels.embedding ?? [];
    if (embeddings.length > 0) markStepCompleted('embedding-model');
    else unmarkStepCompleted('embedding-model');
  }, [manager.configuredModels, manager.modelsLoaded, markStepCompleted, unmarkStepCompleted]);

  const embedCount = manager.configuredModels.embedding?.length ?? 0;
  const registryHasSystemDefaultEmbedding = manager.providers.some((provider) => provider.providerId === 'default');

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
            System Configuration
          </Text>
          <Text as="div" size="4" weight="bold" style={{ color: 'var(--gray-12)' }}>
            Step {systemStepIndex}/{totalSystemSteps}: Configure Embedding Model*
          </Text>
        </Box>

        <Box style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '12px' }}>
          <Flex direction="column" gap="5">
            {!manager.isLoading && embedCount === 0 && (
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
              providers={manager.providers}
              configuredModels={manager.configuredModels}
              searchQuery={manager.searchQuery}
              onSearchChange={manager.setSearchQuery}
              mainSection={manager.mainSection}
              onMainSectionChange={manager.setMainSection}
              capabilitySection={manager.capabilitySection}
              onCapabilitySectionChange={manager.setCapabilitySection}
              onAdd={manager.openAdd}
              onEdit={manager.openEdit}
              onSetDefault={manager.handleSetDefault}
              onDelete={manager.openDelete}
              onRotate={manager.openRotate}
              isLoading={manager.isLoading}
              onRefresh={() => {
                void manager.loadProviders();
                void manager.loadModels();
              }}
            />
          </Flex>
        </Box>
      </Box>
      {manager.dialogs}
    </>
  );
}

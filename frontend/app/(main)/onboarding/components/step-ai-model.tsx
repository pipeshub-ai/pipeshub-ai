'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { useOnboardingStore } from '../store';
import { ProviderGrid, ModelRolesSection } from '@/app/(main)/workspace/ai-models/components';
import { useAiModelsManager } from '@/app/(main)/workspace/ai-models/hooks/use-ai-models-manager';

const MODEL_ADDED_TOAST_DURATION_MS = 6000;

interface StepAiModelProps {
  systemStepIndex: number;
  totalSystemSteps: number;
  nextGateRef?: React.MutableRefObject<(() => boolean) | null>;
}

export function StepAiModel({ systemStepIndex, totalSystemSteps, nextGateRef }: StepAiModelProps) {
  const { t } = useTranslation();
  const { markStepCompleted, unmarkStepCompleted } = useOnboardingStore();
  const [rolesAcknowledged, setRolesAcknowledged] = useState(false);
  const [rolesHighlighted, setRolesHighlighted] = useState(false);
  const rolesSectionRef = useRef<HTMLDivElement>(null);

  const manager = useAiModelsManager({
    registryCapability: 'text_generation',
    providerErrorKey: 'onboarding.stepAiModel.loadProvidersError',
    modelsErrorKey: 'onboarding.stepAiModel.loadModelsError',
    setDefaultSuccessKey: 'onboarding.stepAiModel.setDefaultSuccess',
    setDefaultErrorKey: 'onboarding.stepAiModel.setDefaultError',
    onModelSaved: (result) => {
      if (result.mode !== 'add') return;
      toast.success(t('onboarding.toastModelAddedAi', { name: result.modelName }), {
        duration: MODEL_ADDED_TOAST_DURATION_MS,
      });
    },
  });

  useEffect(() => {
    if (!manager.modelsLoaded) return;
    const llms = manager.configuredModels.llm ?? [];
    if (llms.length > 0) markStepCompleted('ai-model');
    else unmarkStepCompleted('ai-model');
  }, [manager.configuredModels, manager.modelsLoaded, markStepCompleted, unmarkStepCompleted]);

  const llmCount = (manager.configuredModels.llm ?? []).length;
  useEffect(() => {
    if (!nextGateRef) return;
    if (llmCount > 0 && !rolesAcknowledged) {
      nextGateRef.current = () => {
        rolesSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        setRolesHighlighted(true);
        return false;
      };
    } else {
      nextGateRef.current = null;
    }
    return () => {
      if (nextGateRef) nextGateRef.current = null;
    };
  }, [llmCount, rolesAcknowledged, nextGateRef]);

  useEffect(() => {
    if (rolesAcknowledged) setRolesHighlighted(false);
  }, [rolesAcknowledged]);

  const refresh = useCallback(() => {
    void manager.loadProviders();
    void manager.loadModels();
  }, [manager]);

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
          {!manager.isLoading && llmCount === 0 && (
            <Text size="2" style={{ color: 'var(--gray-11)', marginBottom: '12px', display: 'block' }}>
              {t('onboarding.stepAiModel.addLlmHint')}
            </Text>
          )}
          <ProviderGrid
            layout="embedded"
            hideCapabilityBadges
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
            onRefresh={refresh}
          />

          {llmCount > 0 && (
            <Box ref={rolesSectionRef} style={{ marginTop: '16px' }}>
              <ModelRolesSection
                configuredModels={manager.configuredModels}
                onRolesUpdated={manager.loadModels}
                variant="onboarding"
                highlighted={rolesHighlighted}
                onAcknowledgedChange={(v) => setRolesAcknowledged(v)}
              />
            </Box>
          )}
        </Box>
      </Box>
      {manager.dialogs}
    </>
  );
}

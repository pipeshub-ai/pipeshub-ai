'use client';

import React, { useEffect, useState } from 'react';
import { Badge, Box, Dialog, Flex, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import {
  ModelSelectorPanel,
  getReasoningEffortLabel,
} from '@/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel';
import { useChatStore, ASSISTANT_CTX, isModelReasoningCapable } from '@/chat/store';
import { DEFAULT_REASONING_EFFORT, type ModelOverride } from '@/chat/types';
import { PanelCard, PanelHeader, PanelRow } from './panel-section';

export function ChatDefaultsCard() {
  const { t } = useTranslation();
  const [isDialogOpen, setIsDialogOpen] = useState(false);

  const settings = useChatStore((s) => s.settings);
  const setSelectedModelForCtx = useChatStore((s) => s.setSelectedModelForCtx);
  const hydrateReasoningEffortForCtx = useChatStore((s) => s.hydrateReasoningEffortForCtx);

  useEffect(() => {
    hydrateReasoningEffortForCtx(ASSISTANT_CTX);
  }, [hydrateReasoningEffortForCtx]);

  const displayModel = settings.selectedModels[ASSISTANT_CTX] ?? settings.defaultModels[ASSISTANT_CTX] ?? null;
  const displayModelLabel = displayModel
    ? displayModel.modelFriendlyName || displayModel.modelName
    : t('chat.aiModelsTooltip', { defaultValue: 'AI model' });

  const supportsReasoning = isModelReasoningCapable(ASSISTANT_CTX, displayModel);
  const reasoningEffortOverride = settings.reasoningEffort[ASSISTANT_CTX] ?? null;
  const reasoningEffortLabel = supportsReasoning
    ? getReasoningEffortLabel(t, reasoningEffortOverride ?? DEFAULT_REASONING_EFFORT)
    : null;

  const handleModelSelect = (model: ModelOverride) => {
    setSelectedModelForCtx(ASSISTANT_CTX, model);
  };

  return (
    <PanelCard>
      <PanelHeader title={t('chat.projects.workspace.chatDefaultsTitle', { defaultValue: 'Chat defaults' })} />
      <Box style={{ padding: '0 var(--space-4)' }}>
        <PanelRow
          label={t('chat.projects.workspace.modelLabel', { defaultValue: 'Model' })}
          value={displayModelLabel}
          onClick={() => setIsDialogOpen(true)}
          isFirst
        />
        {reasoningEffortLabel && (
          <PanelRow
            label={t('chat.reasoningEffort.label', { defaultValue: 'Reasoning effort' })}
            value={
              <Badge color="jade" variant="soft">
                {reasoningEffortLabel}
              </Badge>
            }
            onClick={() => setIsDialogOpen(true)}
          />
        )}
      </Box>

      <Dialog.Root open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <Dialog.Content style={{ maxWidth: '26rem', width: '100%', padding: 'var(--space-5)' }}>
          <VisuallyHidden>
            <Dialog.Title>{t('chat.aiModelsTooltip', { defaultValue: 'AI model' })}</Dialog.Title>
          </VisuallyHidden>
          <Flex direction="column" style={{ height: '60vh' }}>
            <ModelSelectorPanel selectedModel={displayModel} onModelSelect={handleModelSelect} />
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </PanelCard>
  );
}

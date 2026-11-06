'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Switch, Text } from '@radix-ui/themes';
import { useFeatureFlagsStore, selectChatCompareEnabled } from '@/lib/store/feature-flags-store';
import { ASSISTANT_CTX, useChatStore } from '../../store';
import type { AvailableLlmModel, ModelOverride } from '../../types';
import { sameModel, useCompareStore } from '../compare-store';
import { MAX_COMPARE_MODELS } from '../types';

/**
 * Compare is offered in the plain assistant, in any mode but image: flag on,
 * at least two configured models, and not in a saved agent's chat.
 */
export function useCompareAvailable(ctxKey: string): boolean {
  const flagOn = useFeatureFlagsStore(selectChatCompareEnabled);
  const textMode = useChatStore((s) => s.settings.queryMode !== 'image');
  const modelCount = useChatStore((s) => s.settings.availableModels[ASSISTANT_CTX]?.models.length ?? 0);
  return flagOn && ctxKey === ASSISTANT_CTX && textMode && modelCount >= MAX_COMPARE_MODELS;
}

const toOverride = (m: AvailableLlmModel): ModelOverride => ({
  modelKey: m.modelKey,
  modelName: m.modelName,
  modelFriendlyName: m.modelFriendlyName || m.modelName,
  modelProvider: m.provider,
});

export function CompareToggle({
  models,
  selectedModel,
}: {
  models: AvailableLlmModel[];
  selectedModel: ModelOverride | null;
}) {
  const { t } = useTranslation();
  const enabled = useCompareStore((s) => s.enabled);
  const picked = useCompareStore((s) => s.models);

  const onChange = (on: boolean) => {
    const store = useCompareStore.getState();
    store.setEnabled(on);
    if (!on || store.models.length > 0) return;
    // Start from the model in use plus the first other one, so Compare works with a single switch.
    const current = selectedModel ?? (models[0] ? toOverride(models[0]) : null);
    const other = models.map(toOverride).find((m) => !current || !sameModel(m, current));
    store.setModels([current, other].filter((m): m is ModelOverride => Boolean(m)));
  };

  return (
    <Flex
      direction="column"
      gap="1"
      data-testid="compare-toggle"
      style={{
        padding: 'var(--space-3) var(--space-4)',
        borderRadius: 'var(--radius-1)',
        border: '1px solid var(--olive-3)',
        backgroundColor: 'var(--olive-2)',
      }}
    >
      <Flex align="center" justify="between" gap="3">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }} id="compare-toggle-label">
          {t('chat.compare.toggle')}
        </Text>
        <Switch
          size="1"
          checked={enabled}
          onCheckedChange={onChange}
          aria-labelledby="compare-toggle-label"
          style={{ cursor: 'pointer' }}
        />
      </Flex>
      {enabled && (
        <>
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {picked.length < MAX_COMPARE_MODELS
              ? t('chat.compare.pickTwo', { max: MAX_COMPARE_MODELS })
              : t('chat.compare.pickedHint')}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('chat.compare.costNote')}
          </Text>
        </>
      )}
    </Flex>
  );
}

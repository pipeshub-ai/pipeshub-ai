import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import testI18n from '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock('next/image', () => ({ default: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/themeable-asset-icon', () => ({ ThemeableAssetIcon: () => null }));
vi.mock('@/chat/utils/fetch-models-for-context', async () => {
  const { useChatStore } = await import('@/chat/store');
  return {
    fetchModelsForContext: vi.fn(async (ctxKey: string) =>
      useChatStore.getState().settings.availableModels[ctxKey]?.models ?? []),
    OrgModelsFetchError: class extends Error {},
  };
});

const { useChatStore, ASSISTANT_CTX } = await import('@/chat/store');
const { ModelSelectorPanel } = await import(
  '@/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel'
);
const { AUTO_MODEL, autoRoutedProperties, getAutoRoutedLabel } = await import('@/chat/utils/auto-model');
type AvailableLlmModel = import('@/chat/types').AvailableLlmModel;

function llm(modelKey: string, modelName: string): AvailableLlmModel {
  return {
    modelType: 'llm',
    provider: 'openAI',
    modelKey,
    modelName,
    modelFriendlyName: modelName,
    isMultimodal: false,
    isReasoning: true,
    isDefault: false,
  };
}

const initialSettings = useChatStore.getState().settings;

beforeEach(() => {
  useChatStore.setState({ settings: { ...initialSettings, availableModels: {}, selectedModels: {} } });
});
afterEach(() => cleanup());

function renderPanel(
  models: AvailableLlmModel[],
  props: { allowAuto?: boolean; decisionModelConfigured?: boolean } = {},
) {
  const { decisionModelConfigured, ...panelProps } = props;
  useChatStore.getState().setDecisionModelConfigured(decisionModelConfigured !== false);
  useChatStore.getState().setAvailableModelsForCtx(ASSISTANT_CTX, models);
  const onModelSelect = vi.fn();
  render(
    <Theme>
      <ModelSelectorPanel selectedModel={null} onModelSelect={onModelSelect} {...panelProps} />
    </Theme>,
  );
  return onModelSelect;
}

describe('ModelSelectorPanel Auto row', () => {
  it('offers Auto when there are two or more models and stores the auto selection', async () => {
    const onModelSelect = renderPanel([llm('k1', 'gpt-5'), llm('k2', 'gpt-5-mini')]);
    const row = await screen.findByTestId('chat-model-auto-row');
    expect(row.textContent).toContain('Picks a model and effort for each question');
    fireEvent.click(row);
    expect(onModelSelect).toHaveBeenCalledWith(AUTO_MODEL);
  });

  it('hides Auto when no decision model is configured', async () => {
    renderPanel([llm('k1', 'gpt-5'), llm('k2', 'gpt-5-mini')], { decisionModelConfigured: false });
    await waitFor(() => expect(screen.getByText('gpt-5')).toBeTruthy());
    expect(screen.queryByTestId('chat-model-auto-row')).toBeNull();
  });

  it('hides Auto with a single model', async () => {
    renderPanel([llm('k1', 'gpt-5')]);
    await waitFor(() => expect(screen.getByText('gpt-5')).toBeTruthy());
    expect(screen.queryByTestId('chat-model-auto-row')).toBeNull();
  });

  it('hides Auto where a fixed model must be chosen', async () => {
    renderPanel([llm('k1', 'gpt-5'), llm('k2', 'gpt-5-mini')], { allowAuto: false });
    await waitFor(() => expect(screen.getByText('gpt-5')).toBeTruthy());
    expect(screen.queryByTestId('chat-model-auto-row')).toBeNull();
  });
});

describe('Auto footer label', () => {
  const t = testI18n.t.bind(testI18n) as never;

  it('names the model and effort Auto picked, with the reason as tooltip', () => {
    const result = getAutoRoutedLabel(t, {
      modelKey: 'k1',
      modelName: 'gpt-5',
      modelFriendlyName: 'GPT-5',
      chatMode: 'quick',
      reasoningEffort: 'high',
      autoRouted: true,
      autoReason: 'classified_heavy',
    });
    expect(result?.label).toBe('Auto · GPT-5 · High');
    expect(result?.tooltip).toBe('Complex model · High reasoning');
  });

  it('lists every property that drove the pick', () => {
    const t = testI18n.t.bind(testI18n) as never;
    expect(autoRoutedProperties(t, 'classified_heavy+reasoning; kept pinned', 'high')).toBe(
      'Complex model · High reasoning · Kept from earlier',
    );
    expect(autoRoutedProperties(t, 'greeting', 'low')).toBe('Short greeting · Light model · Low reasoning');
    expect(autoRoutedProperties(t, 'classified_standard', 'medium')).toBe('Standard model · Medium reasoning');
    expect(autoRoutedProperties(t, 'classifier_timeout', null)).toBe('Could not classify · Default model');
  });

  it('is null for a message answered by a hand-picked model', () => {
    expect(getAutoRoutedLabel(t, { modelKey: 'k1', modelName: 'gpt-5', chatMode: 'quick' })).toBeNull();
  });
});

describe('reopening a chat without a saved model', () => {
  it('stays on Auto when the last answer was Auto-routed', async () => {
    const { pickModelInfoFromConversationBundle } = await import(
      '@/chat/utils/apply-conversation-model-info'
    );
    const info = pickModelInfoFromConversationBundle({
      messages: [
        { modelInfo: { modelKey: 'k1', modelName: 'gpt-5', chatMode: 'quick', autoRouted: true } },
      ],
    });
    expect(info?.modelKey).toBe('auto');
    expect(info?.modelName).toBe('auto');
  });
});

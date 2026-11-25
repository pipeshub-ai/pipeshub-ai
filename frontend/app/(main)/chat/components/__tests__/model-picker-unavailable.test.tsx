/**
 * The model picker shows a model an edition's reason (`modelUnavailableReasons`) names as unavailable, with that
 * reason, and does not let it be picked; every other model is picked as before. This build names none.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ChatApi } from '@/chat/api';
import { modelUnavailableReasons } from '@/config';
import type { AvailableLlmModel } from '@/chat/types';

const MODELS: AvailableLlmModel[] = [
  { modelType: 'llm', provider: 'bedrock', modelName: 'anthropic.claude-x', modelKey: 'm-eu', isMultimodal: false, isReasoning: false, isDefault: true, modelFriendlyName: 'Claude EU' },
  { modelType: 'llm', provider: 'openAI', modelName: 'gpt-5', modelKey: 'm-public', isMultimodal: false, isReasoning: false, isDefault: false, modelFriendlyName: 'GPT-5' },
];

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({
  fetchModelsForContext: vi.fn(() => Promise.resolve(MODELS)),
  OrgModelsFetchError: class extends Error {},
}));

import { ASSISTANT_CTX, useChatStore } from '@/chat/store';
import { ModelSelectorPanel } from '../chat-panel/expansion-panels/model-selector/model-selector-panel';

describe('model picker: unavailable models', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en-US');
    useChatStore.setState((s) => ({
      settings: { ...s.settings, availableModels: { ...s.settings.availableModels, [ASSISTANT_CTX]: { models: MODELS, fetchedAt: Date.now() } } },
    }));
  });

  it('this build names no reason of its own', () => {
    expect(modelUnavailableReasons([{ modelKey: 'm-public', modelName: 'gpt-5', allowed: false }]).size).toBe(0);
  });

  it('a model an edition names shows as unavailable with its reason and cannot be picked', async () => {
    vi.spyOn(ChatApi, 'fetchUnavailableModels').mockResolvedValue({ group: new Set(), reasons: new Map([['m-public::gpt-5', 'chat.modelUnavailableGroup']]) });
    const onModelSelect = vi.fn();
    render(
      <Theme>
        <ModelSelectorPanel selectedModel={null} onModelSelect={onModelSelect} />
      </Theme>,
    );
    const unavailable = await screen.findByTestId('chat-model-unavailable');
    expect(unavailable.getAttribute('aria-disabled')).toBe('true');
    expect(unavailable.textContent).toContain('GPT-5');
    expect(unavailable.textContent).toContain(i18n.t('chat.modelUnavailableGroup'));
    fireEvent.click(unavailable);
    expect(onModelSelect).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Claude EU'));
    await waitFor(() => expect(onModelSelect).toHaveBeenCalledWith(expect.objectContaining({ modelKey: 'm-eu' })));
  });
});

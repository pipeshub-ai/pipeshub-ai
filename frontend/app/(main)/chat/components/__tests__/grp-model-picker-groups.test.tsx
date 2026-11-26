/**
 * G-41 (C20.42): the composer's model picker shows a model the org limits to
 * groups the person is in none of as unavailable, with the reason, and does not let it be picked.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ChatApi, groupBlocked } from '@/chat/api';
import type { AvailableLlmModel } from '@/chat/types';

const MODELS: AvailableLlmModel[] = [
  { modelType: 'llm', provider: 'openAI', modelName: 'gpt-5', modelKey: 'm-open', isMultimodal: false, isReasoning: false, isDefault: true, modelFriendlyName: 'GPT-5' },
  { modelType: 'llm', provider: 'anthropic', modelName: 'claude-x', modelKey: 'm-premium', isMultimodal: false, isReasoning: false, isDefault: false, modelFriendlyName: 'Claude Premium' },
];

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({
  fetchModelsForContext: vi.fn(() => Promise.resolve(MODELS)),
  OrgModelsFetchError: class extends Error {},
}));

import { ASSISTANT_CTX, useChatStore } from '@/chat/store';
import { ModelSelectorPanel } from '../chat-panel/expansion-panels/model-selector/model-selector-panel';

describe('G-41 model picker groups (GRP-UI-9, GRP-UI-10)', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en-US');
    useChatStore.setState((s) => ({
      settings: { ...s.settings, availableModels: { ...s.settings.availableModels, [ASSISTANT_CTX]: { models: MODELS, fetchedAt: Date.now() } } },
    }));
  });

  it('GRP-UI-9: the access answer names only the model keys the person\'s groups do not allow', () => {
    const blocked = groupBlocked([
      { modelKey: 'm-premium', modelName: 'claude-x', groupAllowed: false },
      { modelKey: 'm-open', modelName: 'gpt-5' },
      { modelKey: 'm-other', modelName: 'x', groupAllowed: true },
      'junk',
    ]);
    expect([...blocked]).toEqual(['m-premium']);
    expect(groupBlocked(undefined).size).toBe(0);
  });

  it('GRP-UI-10: a model outside the person\'s groups shows the reason and cannot be picked', async () => {
    vi.spyOn(ChatApi, 'fetchUnavailableModels').mockResolvedValue({ group: new Set(['m-premium']), reasons: new Map() });
    const onModelSelect = vi.fn();
    render(
      <Theme>
        <ModelSelectorPanel selectedModel={null} onModelSelect={onModelSelect} />
      </Theme>,
    );
    const unavailable = await screen.findByTestId('chat-model-unavailable');
    expect(unavailable.getAttribute('aria-disabled')).toBe('true');
    expect(unavailable.textContent).toContain('Claude Premium');
    expect(unavailable.textContent).toContain(i18n.t('chat.modelUnavailableGroup'));
    fireEvent.click(unavailable);
    expect(onModelSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('GPT-5'));
    await waitFor(() => expect(onModelSelect).toHaveBeenCalledWith(expect.objectContaining({ modelKey: 'm-open' })));
  });
});

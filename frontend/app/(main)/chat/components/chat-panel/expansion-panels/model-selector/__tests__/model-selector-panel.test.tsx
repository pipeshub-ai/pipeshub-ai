import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';

const { MODELS, state } = vi.hoisted(() => {
  const MODELS = [
    {
      modelType: 'llm',
      provider: 'openAI',
      modelName: 'gpt-4o',
      modelKey: 'k1',
      modelFriendlyName: 'GPT 4o',
      isMultimodal: true,
      isReasoning: false,
      isDefault: true,
    },
    {
      modelType: 'llm',
      provider: 'anthropic',
      modelName: 'thinker',
      modelKey: 'k2',
      modelFriendlyName: 'Thinker',
      isMultimodal: false,
      isReasoning: true,
      isDefault: false,
    },
  ];
  const state = {
    settings: {
      availableModels: { assistant: { models: MODELS } },
      reasoningEffort: {} as Record<string, string | null>,
      agentDefaultReasoningEffort: {} as Record<string, string | null>,
    },
    setReasoningEffortForCtx: (() => {}) as (ctx: string, value: string | null) => void,
  };
  return { MODELS, state };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: string | Record<string, string>) => {
      if (typeof opts === 'string') return opts;
      const text = opts?.defaultValue ?? key;
      return text.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => opts?.[name] ?? '');
    },
  }),
}));
// The panel shows the edition's extra rows (such as Auto) from @/config; this suite covers the configured
// models, so it takes the OSS default, which adds none.
vi.mock('@/config', async () => ({
  ModelSelectorExtraOptions: (await import('../model-selector-extra-options')).ModelSelectorExtraOptions,
}));
vi.mock('@/chat/store', () => ({
  useChatStore: (select: (s: typeof state) => unknown) => select(state),
  ctxKeyFromAgent: () => 'assistant',
  ASSISTANT_CTX: 'assistant',
}));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({
  fetchModelsForContext: vi.fn(() => Promise.resolve(MODELS)),
  OrgModelsFetchError: class extends Error {},
}));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (select: (s: unknown) => unknown) => select({ profile: { isAdmin: true } }),
  selectIsAdmin: (s: { profile?: { isAdmin?: boolean } }) => s.profile?.isAdmin ?? null,
}));
vi.mock('@/lib/navigation', () => ({
  Link: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock('@/app/components/ui/themeable-asset-icon', () => ({ ThemeableAssetIcon: () => <span /> }));

const { ModelSelectorPanel } = await import('../model-selector-panel');

function renderPanel(selected: (typeof MODELS)[number]) {
  const onModelSelect = vi.fn();
  render(
    <ModelSelectorPanel
      selectedModel={{
        modelKey: selected.modelKey,
        modelName: selected.modelName,
        modelFriendlyName: selected.modelFriendlyName,
        modelProvider: selected.provider,
      }}
      onModelSelect={onModelSelect}
    />,
  );
  return onModelSelect;
}

beforeEach(() => {
  state.settings.reasoningEffort = {};
  state.setReasoningEffortForCtx = vi.fn();
});

describe('Configured models', () => {
  it('is one radio group, a radio per model named by its name', () => {
    renderPanel(MODELS[0]);
    // A non-reasoning model: the effort pills are not shown.
    const groups = screen.getAllByRole('radiogroup');
    expect(groups).toHaveLength(1);
    expect(groups[0].getAttribute('aria-label')).toBe('Configured models');
    const first = screen.getByRole('radio', { name: 'GPT 4o' });
    const second = screen.getByRole('radio', { name: 'Thinker' });
    expect(first.getAttribute('aria-checked')).toBe('true');
    expect(second.getAttribute('aria-checked')).toBe('false');
    // Tab reaches the group once, and lands on the chosen model.
    expect(groups[0].getAttribute('tabindex')).toBe('0');
    expect(second.getAttribute('tabindex')).toBe('-1');
    groups[0].focus();
    expect(document.activeElement).toBe(first);
  });

  it('moves the choice with the arrow keys', async () => {
    const onModelSelect = renderPanel(MODELS[0]);
    const first = screen.getByRole('radio', { name: 'GPT 4o' });
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    await waitFor(() =>
      expect(onModelSelect).toHaveBeenCalledWith(expect.objectContaining({ modelKey: 'k2', modelName: 'thinker' })),
    );
    expect(document.activeElement).toBe(screen.getByRole('radio', { name: 'Thinker' }));
  });

  it('selects a model when its card is clicked', () => {
    const onModelSelect = renderPanel(MODELS[0]);
    fireEvent.click(screen.getByText('Thinker'));
    expect(onModelSelect).toHaveBeenCalledWith(expect.objectContaining({ modelKey: 'k2' }));
  });

  it('links to the models page', () => {
    renderPanel(MODELS[0]);
    const link = screen.getByRole('link', { name: 'See more models' });
    expect(link.getAttribute('href')).toBe('/workspace/ai-models/');
  });

  it('shows the provider and soft tags, Default on the accent colour', () => {
    renderPanel(MODELS[0]);
    expect(screen.getByText('by OpenAI')).toBeTruthy();
    const tag = screen.getByText('Default');
    expect(tag.className).toContain('rt-variant-soft');
    expect(tag.getAttribute('data-accent-color')).not.toBe('jade');
    expect(screen.getByText('Multimodal').className).toContain('rt-high-contrast');
  });

  it('shows no effort setting for a non-reasoning model', () => {
    renderPanel(MODELS[0]);
    expect(screen.queryByRole('radiogroup', { name: 'Reasoning effort' })).toBeNull();
    expect(screen.queryByTestId('reasoning-effort-selector')).toBeNull();
    expect(screen.queryByText('This model answers in one pass, with no effort setting.')).toBeNull();
    expect(screen.queryByRole('slider')).toBeNull();
  });

  it('puts one effort pill row above the list for a reasoning model, outside every card', () => {
    const onModelSelect = renderPanel(MODELS[1]);
    const effort = screen.getByRole('radiogroup', { name: 'Reasoning effort' });
    const models = screen.getByRole('radiogroup', { name: 'Configured models' });
    expect(screen.getAllByRole('radiogroup', { name: 'Reasoning effort' })).toHaveLength(1);
    expect(effort.compareDocumentPosition(models) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    for (const card of screen.getAllByTestId('chat-model-option')) {
      expect(within(card).queryByRole('radiogroup', { name: 'Reasoning effort' })).toBeNull();
    }
    expect(screen.queryByRole('slider')).toBeNull();
    fireEvent.click(within(effort).getByRole('radio', { name: 'Medium' }));
    expect(state.setReasoningEffortForCtx).toHaveBeenCalledWith('assistant', 'medium');
    expect(onModelSelect).not.toHaveBeenCalled();
  });

  it('pressing the picked pill again clears the pick', () => {
    state.settings.reasoningEffort = { assistant: 'low' };
    renderPanel(MODELS[1]);
    const low = screen.getByRole('radio', { name: 'Low' });
    expect(low.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(low);
    expect(state.setReasoningEffortForCtx).toHaveBeenCalledWith('assistant', null);
  });

  it('pins the highlighted default when nothing is picked', () => {
    renderPanel(MODELS[1]);
    fireEvent.click(screen.getByRole('radio', { name: 'High' }));
    expect(state.setReasoningEffortForCtx).toHaveBeenCalledWith('assistant', 'high');
  });
});

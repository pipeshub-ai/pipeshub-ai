/**
 * P2.2 DN-5 (rendered): an answer's model badge reads the loaded catalogs from the chat store and
 * shows the configured display name for a message that stored only the raw model name.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { useChatStore } from '../../../store';
import { MessageActions } from '../message-actions';
import type { AvailableLlmModel, ModelInfo } from '../../../types';

// MessageActions forks through the app router (main #4225); this test renders no router.
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

describe('DN-5 message model badge', () => {
  it('shows the catalog display name, not the stored raw model name', () => {
    useChatStore.getState().setAvailableModelsForCtx('assistant', [
      { modelKey: 'k1', modelName: 'gpt-4o', provider: 'openAI', modelFriendlyName: 'Team GPT' } as unknown as AvailableLlmModel,
    ]);
    const modelInfo = { chatMode: 'quick', modelKey: 'k1', modelName: 'gpt-4o', modelFriendlyName: 'gpt-4o' } as ModelInfo;
    render(h(Theme, null, h(MessageActions, { content: 'answer', modelInfo })));
    expect(screen.getByText('Team GPT')).toBeTruthy();
    expect(screen.queryByText('gpt-4o')).toBeNull();
  });
});

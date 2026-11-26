/**
 * U2: "My AI accounts". A person sets no model key of their own here: runs use
 * the models the admin configured, shared with everyone. The page keeps the
 * person's Claude sign-in, their coding engine and what they may use.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { MyAiAccounts } from '../components/my-ai-accounts';
import * as Extras from '../components/my-ai-accounts-extras';
import { useMyAccountsStore } from '../store';
import { freshState, installFake, type FakeState } from './fake-harness';

// This build's slots, whatever config.ts binds: an edition that fills them tests its own page.
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  MyAiAccountsNotice: (await import('../components/my-ai-accounts-extras')).MyAiAccountsNotice,
  MyAiAccountsSections: (await import('../components/my-ai-accounts-extras')).MyAiAccountsSections,
}));

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

let state: FakeState;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  state = freshState();
  installFake(apiClient, state);
  useMyAccountsStore.getState().reset();
  useFeatureFlagsStore.setState({ engineRuns: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderPage = () => render(<Theme><MyAiAccounts /></Theme>);

describe('MyAiAccounts', () => {
  it('UA-1: a labelled loading status until what the person may use is read', async () => {
    let release!: () => void;
    state.hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderPage();
    expect(screen.getAllByRole('status')[0].textContent).toContain('Loading');
    await act(async () => {
      release();
    });
    expect(await screen.findByRole('table', { name: 'What you can use' })).toBeTruthy();
  });

  it('UA-2: no section, row, button or call for a key of the person\'s own', async () => {
    renderPage();
    await screen.findByRole('table', { name: 'What you can use' });
    expect(screen.queryByRole('button', { name: /key/i })).toBeNull();
    expect(screen.queryByText('No key added')).toBeNull();
    for (const provider of ['OpenAI', 'Anthropic', 'Gemini']) {
      expect(screen.queryByRole('group', { name: provider })).toBeNull();
      expect(screen.queryByRole('button', { name: `Add ${provider} key` })).toBeNull();
    }
    expect(state.calls.some((call) => call.url.includes('/model-accounts/me'))).toBe(false);
    expect(screen.getByText(i18n.t('aiAccounts.my.subtitle'))).toBeTruthy();
    // BETA: the page's title carries the Beta badge.
    expect(screen.getByText(i18n.t('common.beta.label'))).toBeTruthy();
  });

  it('UA-3: the sign-in, coding engine and access sections stay; with no coding-agent runs, no note', async () => {
    renderPage();
    expect(await screen.findByText('Coding engine sign-in')).toBeTruthy();
    expect(await screen.findByRole('table', { name: 'What you can use' })).toBeTruthy();
    cleanup();
    useFeatureFlagsStore.setState({ engineRuns: false });
    renderPage();
    await screen.findByRole('table', { name: 'What you can use' });
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('UA-4: the page\'s default slots render nothing', () => {
    expect(render(<><Extras.MyAiAccountsNotice /><Extras.MyAiAccountsSections /></>).container.innerHTML).toBe('');
  });

  it('UA-10: the access list names the offering and each row’s availability or reason', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'What you can use' });
    expect(screen.getByText(/Installation: Managed service/)).toBeTruthy();
    const prohibited = table.querySelector('[data-rule-id="openai-codex-chatgpt-auth-tokens"]') as HTMLElement;
    expect(prohibited.textContent).toContain("The provider's terms do not allow this.");
    const bedrock = table.querySelector('[data-rule-id="bedrock-cloud-org"]') as HTMLElement;
    expect(bedrock.textContent).toContain('cannot use this provider yet');
    const offering = table.querySelector('[data-rule-id="anthropic-platform-paid-claude-code"]') as HTMLElement;
    expect(offering.textContent).toContain('Not available in this kind of installation.');
    const allowed = table.querySelector('[data-rule-id="openai-api-key-org"]') as HTMLElement;
    expect(within(allowed).getByText('Available')).toBeTruthy();
    // As the server reports a row of a kind this deployment does not offer.
    state.access.rules = (state.access.rules as Array<Record<string, unknown>>).map((rule) =>
      rule.accountKind === 'user_api_key' ? { ...rule, built: false } : rule,
    );
    cleanup();
    renderPage();
    const again = await screen.findByRole('table', { name: 'What you can use' });
    const personal = again.querySelector('[data-rule-id="openai-api-key-user"]') as HTMLElement;
    expect(personal.textContent).toContain('PipesHub does not support this yet.');
  });

});

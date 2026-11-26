/**
 * SIGNIN MA-S1..MA-S9: "Coding engine sign-in" on My AI accounts. Off until
 * the org allows it; the consent text before the person turns it on; the
 * account and plan Claude Code reported, never a token; disconnect asks
 * first; ChatGPT is shown and waits for OpenAI.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useToastStore } from '@/lib/store/toast-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { EngineSignInSection } from '../components/engine-signin-section';
import { MyAiAccounts } from '../components/my-ai-accounts';
import { useMyAccountsStore } from '../store';
import { anthropicLink, toEngineSignInView } from '../engine-signin-api';
import { freshState, installFake, type FakeState } from './fake-harness';

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
  useToastStore.setState({ toasts: [] });
  useFeatureFlagsStore.setState({ engineRuns: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderSection = () => render(<Theme><EngineSignInSection /></Theme>);
const claudeRow = async () => screen.findByRole('group', { name: 'Claude account' });

describe('EngineSignInSection', () => {
  it('MA-S1: not allowed by the org: said so, and nothing can be switched on', async () => {
    renderSection();
    const row = await claudeRow();
    expect(row.getAttribute('data-signin-state')).toBe('not_allowed');
    expect(within(row).getByText('Not allowed by your organization')).toBeTruthy();
    expect(within(row).getByText(/Your admin has not allowed personal Claude accounts/)).toBeTruthy();
    expect(within(row).queryByRole('button')).toBeNull();
  });

  it('MA-S2: available: the consent text names what PipesHub can and cannot do, before the switch', async () => {
    state.signin.org = true;
    renderSection();
    const row = await claudeRow();
    expect(row.getAttribute('data-signin-state')).toBe('available');
    expect(within(row).getByText('PipesHub can')).toBeTruthy();
    expect(within(row).getByText('PipesHub cannot')).toBeTruthy();
    expect(within(row).getByText(/See, keep or copy your password or login/)).toBeTruthy();
    expect(within(row).getByText(/shared conversations, scheduled runs/)).toBeTruthy();
    // SIGNIN-5: one sign-in per conversation, not per task.
    expect(
      within(row).getByText(
        /You'll sign in once per conversation; the sign-in ends after 30 minutes idle or when you disconnect\./,
      ),
    ).toBeTruthy();
    expect(within(row).getByText(/It ends 12 hours after you signed in at the latest\./)).toBeTruthy();
    expect(within(row).queryByText(/each new task/)).toBeNull();
    expect(within(row).getByText('Your organization allows Team and Enterprise plans only.')).toBeTruthy();
    const on = within(row).getByRole('button', { name: 'Use my Claude account for my tasks' });
    expect((on as HTMLElement).style.minHeight).toBe('44px');
  });

  it('MA-S3: turning it on is one PUT; the row then says a task will ask for sign-in', async () => {
    state.signin.org = true;
    renderSection();
    fireEvent.click(within(await claudeRow()).getByRole('button', { name: 'Use my Claude account for my tasks' }));
    await waitFor(() => expect(within(screen.getByRole('group', { name: 'Claude account' })).getByText('On: sign in when a task starts')).toBeTruthy());
    expect(state.calls.filter((c) => c.method === 'PUT').map((c) => c.body)).toEqual([{ enabled: true }]);
    expect(useToastStore.getState().toasts.length).toBe(1);
  });

  it('MA-S4: connected: the email and plan Claude Code reported, and when', async () => {
    Object.assign(state.signin, { org: true, enabled: true, email: 'ada@example.com', plan: 'team', signedInAt: '2026-10-18T15:02:00.000Z' });
    renderSection();
    const row = await claudeRow();
    expect(row.getAttribute('data-signin-state')).toBe('connected');
    expect(within(row).getByTestId('claude-signed-in-as').textContent).toBe('Signed in as ada@example.com · Claude Team');
    expect(within(row).getByText('Connected')).toBeTruthy();
  });

  it('MA-S5: disconnect asks first, then ends the sign-in and links to Claude settings to revoke there too', async () => {
    Object.assign(state.signin, { org: true, enabled: true, email: 'ada@example.com', plan: 'max' });
    renderSection();
    fireEvent.click(within(await claudeRow()).getByRole('button', { name: 'Disconnect' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText(/signed out and wiped within a minute/)).toBeTruthy();
    expect(state.calls.some((c) => c.method === 'DELETE')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(screen.getByRole('group', { name: 'Claude account' }).getAttribute('data-signin-state')).toBe('disconnected'));
    const link = within(screen.getByRole('group', { name: 'Claude account' })).getByRole('link', { name: 'open your Claude settings' });
    expect(link.getAttribute('href')).toBe('https://claude.ai/settings');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.queryByText('ada@example.com', { exact: false })).toBeNull();
  });

  it('MA-S6: ChatGPT is shown, waiting for OpenAI, and cannot be connected', async () => {
    renderSection();
    const row = await screen.findByRole('group', { name: 'ChatGPT account' });
    expect(within(row).getByText('Waiting for OpenAI')).toBeTruthy();
    expect((within(row).getByRole('button', { name: 'Connect' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('MA-S7: a service account is told it cannot use one', async () => {
    Object.assign(state.signin, { org: true, serviceAccount: true });
    renderSection();
    expect(within(await claudeRow()).getByText('Service accounts cannot use a personal Claude account.')).toBeTruthy();
  });

  it('MA-S8: an outage reads "try again", never "not allowed"', async () => {
    state.fail['GET /engine-accounts/me'] = { statusCode: 503, message: 'down' };
    renderSection();
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByText('Not allowed by your organization')).toBeNull();
  });

  it('MA-S9: the section sits above "What you can use", and only while coding-agent runs are on', async () => {
    const { unmount } = render(<Theme><MyAiAccounts /></Theme>);
    const title = await screen.findByText('Coding engine sign-in');
    const access = await screen.findByText('What you can use', { selector: 'span' });
    expect(title.compareDocumentPosition(access) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    unmount();
    useFeatureFlagsStore.setState({ engineRuns: false });
    render(<Theme><MyAiAccounts /></Theme>);
    await screen.findByText('What you can use', { selector: 'span' });
    expect(screen.queryByText('Coding engine sign-in')).toBeNull();
  });
});

describe('engine sign-in API parsing', () => {
  it('MA-S10: only https links on Anthropic\'s sign-in hosts are shown', () => {
    expect(anthropicLink('https://claude.com/cai/oauth/authorize?state=s')).toBe('https://claude.com/cai/oauth/authorize?state=s');
    expect(anthropicLink('https://claude.ai/oauth/authorize?x=1')).toBe('https://claude.ai/oauth/authorize?x=1');
    for (const bad of ['http://claude.com/x', 'https://claude.com.evil.example/x', 'https://evil.example/claude.com', 'https://user:p@claude.com/x', 'https://claude.com:8443/x', 'javascript:alert(1)', 'nonsense', null]) {
      expect(anthropicLink(bad as string | null), String(bad)).toBeNull();
    }
  });

  it('MA-S12 (SIGNIN-5): the consent text says the deployment\'s own idle minutes and hours, plural-aware', async () => {
    state.signin.org = true;
    state.signin.keep = { idleMinutes: 45, maxHours: 6 };
    renderSection();
    let row = await claudeRow();
    expect(within(row).getByText(/the sign-in ends after 45 minutes idle or when you disconnect\./)).toBeTruthy();
    expect(within(row).getByText(/It ends 6 hours after you signed in at the latest\./)).toBeTruthy();
    expect(within(row).queryByText(/30 minutes/)).toBeNull();
    cleanup();
    state.signin.keep = { idleMinutes: 1, maxHours: 1 };
    renderSection();
    row = await claudeRow();
    expect(within(row).getByText(/ends after 1 minute idle/)).toBeTruthy();
    expect(within(row).getByText(/It ends 1 hour after/)).toBeTruthy();
    await i18n.changeLanguage('cs-CZ');
    try {
      expect(i18n.t('engineSignIn.cannot.keep', { count: 3 })).toContain('po 3 minutách');
      expect(i18n.t('engineSignIn.cannot.keepMax', { count: 5 })).toContain('5 hodin');
      expect(i18n.t('engineSignIn.cannot.keepMax', { count: 2 })).toContain('2 hodiny');
    } finally {
      await i18n.changeLanguage('en-US');
    }
  });

  it('MA-S11: the view keeps the fields it knows, nothing else (no token reaches a component)', () => {
    const view = toEngineSignInView({ claude: { state: 'connected', enabled: true, email: 'a@b.c', plan: 'max', accessToken: 'sk-ant-oat01-x', consumerPlans: true } });
    expect(JSON.stringify(view)).not.toContain('sk-ant');
    expect(view.claude).toMatchObject({ state: 'connected', email: 'a@b.c', plan: 'max', consumerPlans: true });
    expect(toEngineSignInView(null).claude.state).toBe('not_allowed');
  });
});

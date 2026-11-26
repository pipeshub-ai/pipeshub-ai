/**
 * ENGPICK: the coding-engine pickers of the settings pages. "My AI accounts"
 * holds the person's choice; AI Access → Engine profile the org's default.
 * Only engines the org allows are listed; one it cannot use now is disabled
 * and its reason is part of the option a screen reader announces.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useToastStore } from '@/lib/store/toast-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { MyAiAccounts } from '../components/my-ai-accounts';
import { useMyAccountsStore } from '../store';
import { OrgAiAccess } from '../../ai-access/components/org-ai-access';
import { CODING_ENGINES_CHANGED } from '../../ai-access/components/org-coding-engine';
import { freshState, installFake, type FakeState } from './fake-harness';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean; isInitialized: boolean }) => unknown) =>
    selector({ isAdmin: true, isInitialized: true }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
  // G-41: the group access section on the same page reads who is saving (its lock-out warning).
  selectProfile: () => ({ userId: 'u-admin' }),
  selectIsProfileInitialized: (s: { isInitialized: boolean }) => s.isInitialized,
}));

let state: FakeState;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
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

const options = (select: HTMLElement) =>
  within(select)
    .getAllByRole('option')
    .map((o) => ({ label: o.textContent, disabled: (o as HTMLOptionElement).disabled }));
const puts = (path: string) => state.calls.filter((c) => c.method === 'PUT' && c.url.endsWith(path)).map((c) => c.body);

describe('My AI accounts → Coding engine', () => {
  const picker = () => screen.findByRole('combobox', { name: 'Coding engine' });

  it('MY-1: a labelled loading status until the engines arrive', async () => {
    let release!: () => void;
    state.hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    render(<Theme><MyAiAccounts /></Theme>);
    expect(screen.queryByRole('combobox', { name: 'Coding engine' })).toBeNull();
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0);
    await act(async () => {
      release();
    });
    expect(await picker()).toBeTruthy();
  });

  it('MY-2: org default first, the allowed engines, none last; a disabled engine announces its reason; the helper describes it', async () => {
    render(<Theme><MyAiAccounts /></Theme>);
    const select = await picker();
    expect(options(select)).toEqual([
      { label: 'Organization default (Codex)', disabled: false },
      { label: 'opencode', disabled: false },
      { label: 'Codex', disabled: false },
      { label: "Claude Code – Not available in this kind of installation with your organization's models", disabled: true },
      { label: 'None – run code in the built-in sandbox', disabled: false },
    ]);
    expect((select as HTMLSelectElement).value).toBe('org_default');
    const helper = document.getElementById(select.getAttribute('aria-describedby')?.split(' ')[0] ?? '');
    expect(helper?.textContent).toBe('Used in Agent mode when a question needs code, files or many steps of work.');
  });

  it('recs2: on a deployment without the engine sandbox (Helm today) every engine is disabled with the plain reason', async () => {
    state.coding = {
      ...state.coding,
      engines: ['opencode', 'codex', 'claude_code'].map((kind) => ({
        kind,
        available: false,
        reason: { code: 'ENGINE_SANDBOX_MISSING' },
      })),
    };
    render(<Theme><MyAiAccounts /></Theme>);
    const select = await picker();
    const reason = 'Coding engines need the sandbox add-on, not installed on this deployment';
    expect(options(select).slice(1, 4)).toEqual([
      { label: `opencode – ${reason}`, disabled: true },
      { label: `Codex – ${reason}`, disabled: true },
      { label: `Claude Code – ${reason}`, disabled: true },
    ]);
  });

  it('MY-3: picking an engine saves it for this person', async () => {
    render(<Theme><MyAiAccounts /></Theme>);
    fireEvent.change(await picker(), { target: { value: 'opencode' } });
    await waitFor(() => expect(puts('/model-accounts/coding-engine')).toEqual([{ choice: 'opencode' }]));
    await waitFor(async () => expect(((await picker()) as HTMLSelectElement).value).toBe('opencode'));
    fireEvent.change(await picker(), { target: { value: 'none' } });
    await waitFor(() => expect(puts('/model-accounts/coding-engine')).toEqual([{ choice: 'opencode' }, { choice: 'none' }]));
  });

  it('MY-4: a refused save shows the server’s reason and keeps the stored choice', async () => {
    state.fail['PUT /model-accounts/coding-engine'] = { statusCode: 403, message: 'This offering may not use this account and route' };
    render(<Theme><MyAiAccounts /></Theme>);
    fireEvent.change(await picker(), { target: { value: 'codex' } });
    const alert = await screen.findByText('This offering may not use this account and route');
    expect(alert.getAttribute('role')).toBe('alert');
    expect(((await picker()) as HTMLSelectElement).value).toBe('org_default');
  });

  it('MY-5: an outage reads "try again", never an empty choice', async () => {
    state.fail['GET /model-accounts/coding-engine'] = { statusCode: 503, message: 'down' };
    render(<Theme><MyAiAccounts /></Theme>);
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Try again' }).length).toBeGreaterThan(0));
    expect(screen.queryByRole('combobox', { name: 'Coding engine' })).toBeNull();
  });
});

describe('AI Access → Default coding engine for the org', () => {
  const picker = () => screen.findByRole('combobox', { name: 'Default coding engine for the org' });

  it('ORG-1: None and the allowed engines, the disabled one with its reason; a change saves the org default', async () => {
    render(<Theme><OrgAiAccess /></Theme>);
    const select = await picker();
    expect(options(select).map((o) => o.label)).toEqual([
      'None',
      'opencode',
      'Codex',
      "Claude Code – Not available in this kind of installation with your organization's models",
    ]);
    expect((select as HTMLSelectElement).value).toBe('codex');
    fireEvent.change(select, { target: { value: 'opencode' } });
    await waitFor(() => expect(puts('/admin/coding-engine')).toEqual([{ defaultEngine: 'opencode' }]));
    fireEvent.change(await picker(), { target: { value: 'none' } });
    await waitFor(() => expect(puts('/admin/coding-engine')).toEqual([{ defaultEngine: 'opencode' }, { defaultEngine: null }]));
  });

  it('ORG-2: a refused save shows the server’s reason under the select', async () => {
    state.fail['PUT /admin/coding-engine'] = { statusCode: 400, message: 'The agent engine profile asks for more than allowed: engine' };
    render(<Theme><OrgAiAccess /></Theme>);
    fireEvent.change(await picker(), { target: { value: 'opencode' } });
    expect(await screen.findByText('The agent engine profile asks for more than allowed: engine')).toBeTruthy();
  });

  it('ORG-3: a setting that changes the allowed engines makes the card read them again', async () => {
    render(<Theme><OrgAiAccess /></Theme>);
    await picker();
    const reads = () => state.calls.filter((c) => c.method === 'GET' && c.url.endsWith('/model-accounts/coding-engine')).length;
    const before = reads();
    act(() => {
      window.dispatchEvent(new Event(CODING_ENGINES_CHANGED));
    });
    await waitFor(() => expect(reads()).toBe(before + 1));
  });

});

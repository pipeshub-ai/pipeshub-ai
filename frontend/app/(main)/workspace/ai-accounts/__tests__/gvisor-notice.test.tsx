/**
 * GV-UI: coding engines need gVisor on a
 * production install. On the admin's engine settings, an engine the server
 * cannot sandbox at its floor names why in its option, and a notice above says
 * what to install with a link to the guide; while the dev/test override is on,
 * a warning stays there instead. A run the worker refused for it reads the
 * same sentence in the chat.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useToastStore } from '@/lib/store/toast-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { OrgAiAccess } from '../../ai-access/components/org-ai-access';
import { useMyAccountsStore } from '../store';
import { toCodingEngineView } from '../api';
import { GVISOR_INSTALL_GUIDE, GVISOR_SYSCALL_FILTER_GUIDE } from '../components/engine-isolation-notice';
import { runErrorText } from '../../../chat/harness-errors';
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

const REFUSED = { available: false, reason: { code: 'ENGINE_ISOLATION_UNAVAILABLE' } };
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

const picker = () => screen.findByRole('combobox', { name: 'Default coding engine for the org' });

describe('GV-UI: the engines’ sandbox on the admin’s engine settings', () => {
  it('GV-UI-1: production without runsc: each engine names gVisor and the notice links the install guide', async () => {
    state.coding = { ...state.coding, engines: ['opencode', 'codex'].map((kind) => ({ kind, ...REFUSED })) };
    render(<Theme><OrgAiAccess /></Theme>);
    const select = await picker();
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'None',
      'opencode – Needs the gVisor sandbox runtime on this server',
      'Codex – Needs the gVisor sandbox runtime on this server',
    ]);
    const notice = screen
      .getAllByRole('alert')
      .find((n) => n.textContent?.includes('Coding engines need the gVisor sandbox runtime on this server'));
    expect(notice).toBeTruthy();
    const link = within(notice as HTMLElement).getByRole('link', { name: 'install guide' });
    expect(link.getAttribute('href')).toBe(GVISOR_INSTALL_GUIDE);
    expect(GVISOR_INSTALL_GUIDE).toMatch(/^https:\/\//);
    expect(screen.queryByText('Coding engines run without gVisor — weaker isolation')).toBeNull();
  });

  it('GV-UI-2: the dev/test override: engines are available and the weaker-isolation warning stays', async () => {
    state.coding = { ...state.coding, withoutGvisor: true };
    render(<Theme><OrgAiAccess /></Theme>);
    await picker();
    const warning = await screen.findByText('Coding engines run without gVisor — weaker isolation');
    expect(warning.closest('[role="status"]')).toBeTruthy();
    expect(screen.queryByText(/need the gVisor sandbox runtime/)).toBeNull();
  });

  it('GV-UI-3: gVisor present (production, nothing refused): no notice', async () => {
    render(<Theme><OrgAiAccess /></Theme>);
    await picker();
    expect(screen.queryByText(/gVisor/)).toBeNull();
  });

  it('GV-UI-4: the view reads the override flag; absent is false', () => {
    expect(toCodingEngineView({ engines: [], orgDefault: null, choice: 'none', withoutGvisor: true }).withoutGvisor).toBe(true);
    expect(toCodingEngineView({ engines: [], orgDefault: null, choice: 'none' }).withoutGvisor).toBe(false);
    expect(toCodingEngineView({ engines: [{ kind: 'codex', ...REFUSED }], orgDefault: null, choice: 'none' }).engines).toEqual([
      { kind: 'codex', available: false, reason: 'engine_isolation_unavailable' },
    ]);
  });

  it('GV-UI-6: gVisor without its own syscall filter: engines stay available and a warning names the fix (R32-SECCOMP)', async () => {
    state.coding = { ...state.coding, gvisorSyscallFilter: false };
    render(<Theme><OrgAiAccess /></Theme>);
    const select = await picker();
    expect(within(select).getAllByRole('option').every((o) => !o.textContent?.includes('Needs'))).toBe(true);
    const warning = await screen.findByText(/without its own syscall filter/);
    const status = warning.closest('[role="status"]') as HTMLElement;
    expect(status).toBeTruthy();
    const link = within(status).getByRole('link', { name: 'how to turn it on' });
    expect(link.getAttribute('href')).toBe(GVISOR_SYSCALL_FILTER_GUIDE);
    expect(GVISOR_SYSCALL_FILTER_GUIDE).toBe(`${GVISOR_INSTALL_GUIDE}#syscall-filter`);
  });

  it('GV-UI-7: the filter on, or not said: no warning; the view reads true, false or null', async () => {
    state.coding = { ...state.coding, gvisorSyscallFilter: true };
    render(<Theme><OrgAiAccess /></Theme>);
    await picker();
    expect(screen.queryByText(/syscall filter/)).toBeNull();
    const view = (extra: object) => toCodingEngineView({ engines: [], orgDefault: null, choice: 'none', ...extra });
    expect(view({ gvisorSyscallFilter: false }).gvisorSyscallFilter).toBe(false);
    expect(view({ gvisorSyscallFilter: true }).gvisorSyscallFilter).toBe(true);
    expect(view({}).gvisorSyscallFilter).toBeNull();
  });

  it('GV-UI-5: a run the worker refused for it reads the admin’s sentence with the current guide, in every locale', async () => {
    expect(runErrorText('gvisor_unavailable')).toBe(
      `Coding engines need the gVisor sandbox runtime on this server. Ask your admin to install it — install guide: ${GVISOR_INSTALL_GUIDE}`,
    );
    try {
      for (const locale of Object.keys(i18n.options.resources ?? {})) {
        await i18n.changeLanguage(locale);
        expect(runErrorText('gvisor_unavailable'), locale).toContain(GVISOR_INSTALL_GUIDE);
      }
    } finally {
      await i18n.changeLanguage('en-US');
    }
  });
});

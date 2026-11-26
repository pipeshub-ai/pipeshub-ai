/**
 * DEEPROUTE: AI Access → Engine profile → "Hand long work to the coding engine". The org's switch, on by default:
 * research across many sources, planning, long reports and other many-step work go to the coding engine on their
 * own. A change saves `PUT /admin/coding-engine {autoRoute}`; a failed save says so under the switch.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { useToastStore } from '@/lib/store/toast-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { OrgAiAccess } from '../../ai-access/components/org-ai-access';
import { toCodingEngineView } from '../api';
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
  useToastStore.setState({ toasts: [] });
  useFeatureFlagsStore.setState({ engineRuns: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const puts = () => state.calls.filter((c) => c.method === 'PUT' && c.url.endsWith('/admin/coding-engine')).map((c) => c.body);
const toggle = () => screen.findByRole('switch', { name: 'Hand long work to the coding engine' });

describe('DEEPROUTE: the org switch for handing long work to the coding engine', () => {
  it('AR-1: on by default, with the helper that says what goes and what stays', async () => {
    render(<Theme><OrgAiAccess /></Theme>);
    const control = await toggle();
    expect(control.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText(/Simple questions stay in the chat/)).toBeTruthy();
  });

  it('AR-2: turning it off saves autoRoute: false only, and on again saves true', async () => {
    render(<Theme><OrgAiAccess /></Theme>);
    fireEvent.click(await toggle());
    await waitFor(() => expect(puts()).toEqual([{ autoRoute: false }]));
    await waitFor(async () => expect((await toggle()).getAttribute('aria-checked')).toBe('false'));
    fireEvent.click(await toggle());
    await waitFor(() => expect(puts()).toEqual([{ autoRoute: false }, { autoRoute: true }]));
  });

  it('AR-3: a failed save says so under the switch and keeps what was stored', async () => {
    state.fail['PUT /admin/coding-engine'] = { statusCode: 500, message: 'down' };
    render(<Theme><OrgAiAccess /></Theme>);
    fireEvent.click(await toggle());
    expect(await screen.findByText(i18n.t('aiAccounts.common.failed'))).toBeTruthy();
    expect((await toggle()).getAttribute('aria-checked')).toBe('true');
  });

  it('AR-4: a server older than DEEPROUTE does not say, and never hands off on its own: it reads off', () => {
    expect(toCodingEngineView({ engines: [], orgDefault: null, choice: 'org_default' }).autoRoute).toBe(false);
    expect(toCodingEngineView({ engines: [], orgDefault: null, choice: 'org_default', autoRoute: true }).autoRoute).toBe(true);
  });
});

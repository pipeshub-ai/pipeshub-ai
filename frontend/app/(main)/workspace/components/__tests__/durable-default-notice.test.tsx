/**
 * BETA (was UX1 LB-14): where the operator runs the agent harness but the org has it off in Labs (its default), org
 * admins are told it is in beta and off by default, with a link to Labs, until one of them answers. Members never see it.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { DurableDefaultNotice, LABS_SETTINGS_CHANGED } from '../durable-default-notice';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
let profile = { isAdmin: true as boolean | null, isInitialized: true };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof profile) => unknown) => selector(profile),
  selectIsAdmin: (s: typeof profile) => s.isAdmin,
  selectIsProfileInitialized: (s: typeof profile) => s.isInitialized,
}));
let reason: string | null = 'org_disabled';
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: { agentHarness: { reason: string } | null }) => unknown) =>
    selector({ agentHarness: reason === null ? null : { reason } }),
}));

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);
let notice: string | undefined;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
beforeEach(() => {
  profile = { isAdmin: true, isInitialized: true };
  reason = 'org_disabled';
  notice = undefined;
  get.mockImplementation(async () => ({
    data: { fileUploadMaxSizeBytes: 1, featureFlags: {}, notices: notice ? { durableAgentDefault: notice } : {} },
  }));
  post.mockImplementation(async (_url: string, body: unknown) => {
    notice = (body as { notices: { durableAgentDefault: string } }).notices.durableAgentDefault;
    return { data: {} };
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const show = () => render(React.createElement(Theme, null, React.createElement(DurableDefaultNotice)));

describe('DurableDefaultNotice (BETA)', () => {
  it('tells an admin the harness is in Labs, Beta and off by default, with a real link to Labs', async () => {
    show();
    const region = await screen.findByRole('region', { name: i18n.t('workspace.labs.durableNotice.title') });
    expect(region.textContent).toContain(i18n.t('common.beta.label'));
    expect(region.textContent).toContain('off by default');
    expect(region.textContent).toContain('Labs');
    // UX3 SE-7: it says what changes, never that nothing else does.
    expect(region.textContent).not.toContain('works as before');
    expect(region.querySelectorAll('li')).toHaveLength(4);
    const link = screen.getByRole('link', { name: i18n.t('workspace.labs.durableNotice.openLabs') });
    expect(link.getAttribute('href')).toMatch(/^\/workspace\/labs\/?$/);
    // It never claims the harness is on, nor offers to change the setting from here.
    expect(region.textContent).not.toContain('is now the default');
    expect(screen.queryByRole('button', { name: /new default|keep it off/i })).toBeNull();
  });

  it('shows while a legacy answer is still pending', async () => {
    notice = 'pending';
    show();
    expect(await screen.findByTestId('durable-default-notice')).toBeTruthy();
  });

  it('"Dismiss" records dismissed, hides the notice, and it never shows again', async () => {
    const changed = vi.fn();
    window.addEventListener(LABS_SETTINGS_CHANGED, changed);
    show();
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('workspace.labs.durableNotice.dismiss') }));
    await waitFor(() => expect(screen.queryByTestId('durable-default-notice')).toBeNull());
    expect(post).toHaveBeenCalledWith(
      '/api/v1/configurationManager/platform/settings',
      { notices: { durableAgentDefault: 'dismissed' } },
      { suppressErrorToast: true },
    );
    expect(changed).toHaveBeenCalledTimes(1);
    window.removeEventListener(LABS_SETTINGS_CHANGED, changed);
    cleanup();
    show();
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId('durable-default-notice')).toBeNull();
  });

  it('a failed dismiss keeps the notice and says so', async () => {
    post.mockRejectedValueOnce(new Error('down'));
    show();
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('workspace.labs.durableNotice.dismiss') }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByTestId('durable-default-notice')).toBeTruthy();
  });

  it('stays hidden once an admin chose in Labs', async () => {
    for (const answered of ['adopted', 'kept', 'dismissed']) {
      notice = answered;
      show();
      await waitFor(() => expect(get).toHaveBeenCalled());
      expect(screen.queryByTestId('durable-default-notice')).toBeNull();
      cleanup();
      get.mockClear();
    }
  });

  it('is never shown to a member, nor where the harness is on, not run, or stopped', async () => {
    profile = { isAdmin: false, isInitialized: true };
    show();
    expect(get).not.toHaveBeenCalled();
    cleanup();
    profile = { isAdmin: true, isInitialized: true };
    for (const other of [null, 'enabled', 'infrastructure_unavailable', 'kill_switch']) {
      reason = other;
      show();
      expect(screen.queryByTestId('durable-default-notice')).toBeNull();
      cleanup();
    }
    expect(get).not.toHaveBeenCalled();
  });
});

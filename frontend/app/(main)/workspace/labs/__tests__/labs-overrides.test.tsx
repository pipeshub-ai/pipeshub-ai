/**
 * UX1 LB-13: the Labs page saves only the flags the admin changed, marks a
 * flag that differs from its release default, and resets one to its default.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import LabsPage from '../page';
import { labsChanges, overridesAfter, useLabsStore } from '../store';
import type { AvailableFlag } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean; isInitialized: boolean }) => unknown) =>
    selector({ isAdmin: true, isInitialized: true }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
  selectIsProfileInitialized: (s: { isInitialized: boolean }) => s.isInitialized,
}));
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: { fetchFlags: () => void }) => unknown) => selector({ fetchFlags: () => undefined }),
}));
vi.mock('../../connectors/components/vector-store-actions', () => ({ VectorStoreActions: () => null }));

const FLAGS: AvailableFlag[] = [
  { key: 'ENABLE_MCP', label: 'Enable MCP Servers', description: '', defaultEnabled: false },
  { key: 'ENABLE_SKILLS', label: 'Enable Skills', description: '', defaultEnabled: true },
  { key: 'ENABLE_AGENT_HARNESS', label: 'Enable Durable Agent Harness', description: '', defaultEnabled: true, requiresAgentHarness: true },
];

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useLabsStore.setState({ isLoading: true, overrides: {} });
  get.mockImplementation(async (url: string) => {
    if (url.endsWith('/feature-flags/available')) return { data: { flags: FLAGS } };
    return {
      data: {
        fileUploadMaxSizeBytes: 30 * 1024 * 1024,
        featureFlags: { ENABLE_MCP: false, ENABLE_SKILLS: true, ENABLE_AGENT_HARNESS: false },
        featureFlagOverrides: { ENABLE_AGENT_HARNESS: false },
      },
    };
  });
  post.mockResolvedValue({ data: {} });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const page = () => render(React.createElement(Theme, null, React.createElement(LabsPage)));

describe('Labs saves only what changed (UX1 LB-13)', () => {
  it('sends only the flag the admin switched, not the whole list', async () => {
    page();
    fireEvent.click(await screen.findByRole('switch', { name: 'Enable MCP Servers' }));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]?.[1]).toEqual({
      featureFlags: { ENABLE_MCP: true },
      expected: { featureFlags: { ENABLE_MCP: false } },
    });
  });

  it('marks a flag changed from its default and resets it to the default', async () => {
    page();
    expect(await screen.findByTestId('labs-changed-ENABLE_AGENT_HARNESS')).toBeTruthy();
    expect(screen.queryByTestId('labs-changed-ENABLE_MCP')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reset Enable Durable Agent Harness to its default' }));
    expect(screen.getByRole('switch', { name: 'Enable Durable Agent Harness' }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]?.[1]).toEqual({
      featureFlags: { ENABLE_AGENT_HARNESS: true },
      expected: { featureFlags: { ENABLE_AGENT_HARNESS: false } },
    });
    await waitFor(() => expect(screen.queryByTestId('labs-changed-ENABLE_AGENT_HARNESS')).toBeNull());
  });
});

describe('Labs marks the agent harness flags Beta (BETA)', () => {
  it('shows the Beta badge on a harness flag and on no other flag', async () => {
    page();
    const harness = await screen.findByTestId('labs-flag-ENABLE_AGENT_HARNESS');
    expect(within(harness).getByText(i18n.t('common.beta.label'))).toBeTruthy();
    expect(within(screen.getByTestId('labs-flag-ENABLE_MCP')).queryByText(i18n.t('common.beta.label'))).toBeNull();
    expect(screen.getAllByText(i18n.t('common.beta.label'))).toHaveLength(1);
  });
});

describe('labsChanges and overridesAfter', () => {
  const saved = { fileSizeLimitMb: 30 as const, featureFlags: { ENABLE_MCP: false, ENABLE_SKILLS: true }, resets: [] };

  it('a reset sends the default even when the value did not change', () => {
    const form = { ...saved, featureFlags: { ...saved.featureFlags }, resets: ['ENABLE_SKILLS'] };
    expect(labsChanges(form, saved, FLAGS)).toEqual({
      featureFlags: { ENABLE_SKILLS: true },
      expected: { featureFlags: { ENABLE_SKILLS: true } },
    });
  });

  it('the upload limit is sent only when it changed', () => {
    const form = { ...saved, fileSizeLimitMb: 50 as const, featureFlags: { ...saved.featureFlags } };
    expect(labsChanges(form, saved, FLAGS)).toEqual({ fileUploadMaxSizeBytes: 50 * 1024 * 1024 });
  });

  it('a flag sent at its default has no override afterwards', () => {
    expect(overridesAfter({ ENABLE_MCP: true }, { ENABLE_MCP: false, ENABLE_SKILLS: false }, FLAGS)).toEqual({
      ENABLE_SKILLS: false,
    });
  });
});

/**
 * UX2 LC-8: a Labs save says what the page showed; when another admin saved
 * first, the page says so, shows what is stored now, keeps this admin's
 * unsaved changes and marks the flags someone else changed. Saving again works.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import LabsPage from '../page';
import { labsChanges, useLabsStore } from '../store';
import { isLabsChanged, type AvailableFlag } from '../api';

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
];

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);
const MB = 1024 * 1024;

/** The processed error the API client rejects with for Node's 409 `labs_changed`. */
const labsChanged = {
  statusCode: 409,
  originalError: { response: { data: { error: { code: 'HTTP_CONFLICT', reasonCode: 'labs_changed' } } } },
};

let stored: { featureFlags: Record<string, boolean>; featureFlagOverrides: Record<string, boolean> };

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useLabsStore.setState({ isLoading: true, overrides: {}, conflicts: [] });
  stored = { featureFlags: { ENABLE_MCP: false, ENABLE_SKILLS: true }, featureFlagOverrides: {} };
  get.mockImplementation(async (url: string) => {
    if (url.endsWith('/feature-flags/available')) return { data: { flags: FLAGS } };
    return { data: { fileUploadMaxSizeBytes: 30 * MB, ...stored } };
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const page = () => render(React.createElement(Theme, null, React.createElement(LabsPage)));

describe('Labs when someone else saved first (UX2 LC-8)', () => {
  it('sends what the page showed for every setting it sends', () => {
    const saved = { fileSizeLimitMb: 30 as const, featureFlags: { ENABLE_MCP: false, ENABLE_SKILLS: true }, resets: [] };
    const form = { fileSizeLimitMb: 50 as const, featureFlags: { ENABLE_MCP: true, ENABLE_SKILLS: true }, resets: [] };
    expect(labsChanges(form, saved, FLAGS, 30 * MB)).toEqual({
      fileUploadMaxSizeBytes: 50 * MB,
      featureFlags: { ENABLE_MCP: true },
      expected: { featureFlags: { ENABLE_MCP: false }, fileUploadMaxSizeBytes: 30 * MB },
    });
  });

  it('recognises only the labs_changed refusal', () => {
    expect(isLabsChanged(labsChanged)).toBe(true);
    expect(isLabsChanged({ statusCode: 409 })).toBe(false);
    expect(isLabsChanged({ ...labsChanged, statusCode: 500 })).toBe(false);
    expect(isLabsChanged(null)).toBe(false);
  });

  it('says so, shows what is stored, keeps the admin’s change and marks the other admin’s; saving again works', async () => {
    page();
    fireEvent.click(await screen.findByRole('switch', { name: 'Enable MCP Servers' }));
    // Another admin turned Skills off and MCP on after this page loaded.
    stored = { featureFlags: { ENABLE_MCP: true, ENABLE_SKILLS: false }, featureFlagOverrides: { ENABLE_MCP: true, ENABLE_SKILLS: false } };
    post.mockRejectedValueOnce(labsChanged);
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    const alert = await screen.findByTestId('labs-conflict');
    expect(alert.getAttribute('role')).toBe('alert');
    expect(alert.textContent).toContain('Someone else changed Labs');
    expect(screen.getByTestId('labs-conflict-ENABLE_SKILLS')).toBeTruthy();
    expect(screen.getByTestId('labs-conflict-ENABLE_MCP')).toBeTruthy();
    expect(screen.getByRole('switch', { name: 'Enable Skills' }).getAttribute('aria-checked')).toBe('false');
    // The admin's own unsaved change stays on the page.
    expect(screen.getByRole('switch', { name: 'Enable MCP Servers' }).getAttribute('aria-checked')).toBe('true');

    // MCP is already what this admin wants: nothing is left to send for it, so turn it off and save.
    fireEvent.click(screen.getByRole('switch', { name: 'Enable MCP Servers' }));
    post.mockResolvedValueOnce({ data: {} });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1]?.[1]).toEqual({
      featureFlags: { ENABLE_MCP: false },
      expected: { featureFlags: { ENABLE_MCP: true } },
    });
    await waitFor(() => expect(screen.queryByTestId('labs-conflict')).toBeNull());
  });
});

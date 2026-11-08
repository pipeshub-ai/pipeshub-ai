import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const api = vi.hoisted(() => ({ getRoles: vi.fn(), updateRoles: vi.fn() }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../api', async (importActual) => {
  const actual = await importActual<typeof import('../api')>();
  return { ...actual, AIModelsApi: { ...actual.AIModelsApi, ...api } };
});
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { ModelRolesSection } from '../components/model-roles-section';
import type { ConfiguredModel } from '../types';
import { clearSessionCache } from '@/lib/api/session-cache';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';

const MODELS = {
  llm: [
    { modelKey: 'k1', provider: 'openAI', modelFriendlyName: 'GPT', isDefault: true },
    { modelKey: 'k2', provider: 'openAI', modelFriendlyName: 'Mini' },
  ],
} as unknown as Record<string, ConfiguredModel[]>;

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderSection(props: Partial<React.ComponentProps<typeof ModelRolesSection>> = {}) {
  const view = renderInTheme(<ModelRolesSection configuredModels={MODELS} {...props} />);
  await settle();
  return view;
}

async function pickIndexingModel(label: string) {
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
  await settle();
  fireEvent.click(screen.getByRole('option', { name: label }));
  await settle();
}

describe('ModelRolesSection cache', () => {
  beforeEach(() => {
    installBrowserShims();
    vi.useFakeTimers();
    vi.clearAllMocks();
    clearSessionCache();
    api.getRoles.mockResolvedValue({ status: 'ok', modelRoles: {} });
    api.updateRoles.mockResolvedValue({});
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('loads the roles once per session', async () => {
    (await renderSection()).unmount();
    await renderSection();

    expect(api.getRoles).toHaveBeenCalledTimes(1);
  });

  it('shows a saved indexing role at once, without reading the roles again', async () => {
    await renderSection();

    await pickIndexingModel('Mini');

    expect(api.updateRoles).toHaveBeenCalledWith({ indexing: { modelType: 'llm', modelKey: 'k2' } });
    expect(screen.getByRole('combobox').textContent).toContain('Mini');
    expect(api.getRoles).toHaveBeenCalledTimes(1);
  });

  it('a model change made on another tab refreshes the roles when the section is shown again', async () => {
    (await renderSection()).unmount();
    api.getRoles.mockResolvedValue({ status: 'ok', modelRoles: { indexing: { modelType: 'llm', modelKey: 'k2' } } });

    revalidateSwrNamespace(SWR_NS.aiModelConfig);
    await renderSection();
    await settle();

    expect(api.getRoles).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('combobox').textContent).toContain('Mini');
  });

  it('an assignment from an earlier session unblocks onboarding', async () => {
    api.getRoles.mockResolvedValue({ status: 'ok', modelRoles: { indexing: { modelType: 'llm', modelKey: 'k1' } } });
    const onAcknowledgedChange = vi.fn();

    await renderSection({ variant: 'onboarding', onAcknowledgedChange });

    expect(onAcknowledgedChange).toHaveBeenCalledWith(true);
  });

  it('a failed roles read is silent and falls back to the default model', async () => {
    api.getRoles.mockRejectedValue(new Error('down'));

    await renderSection();

    expect(toast.error).not.toHaveBeenCalled();
    expect(screen.getByRole('combobox').textContent).toContain('workspace.aiModels.roles.useDefaultModel');
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { act, cleanup, renderHook } from '@testing-library/react';

// The real AIModelsApi runs (with its cache invalidation); only the HTTP calls are stubbed.
const api = vi.hoisted(() => ({ getRegistry: vi.fn(), getAllModels: vi.fn(), setDefault: vi.fn() }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('@/lib/api', () => ({
  apiClient: {
    get: async (url: string, config?: { params?: unknown }) => ({
      data: await (url.endsWith('/ai-models/registry') ? api.getRegistry(config?.params) : api.getAllModels()),
    }),
    put: async () => ({ data: await api.setDefault() }),
  },
}));
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { useAiModelsManager } from '../hooks/use-ai-models-manager';
import { clearSessionCache } from '@/lib/api/session-cache';

const REGISTRY = { providers: [{ providerId: 'openAI', name: 'OpenAI' }] };
const MODELS = { models: { llm: [{ modelKey: 'k1', provider: 'openAI' }] } };

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function mountManager(options: Partial<Parameters<typeof useAiModelsManager>[0]> = {}) {
  const view = renderHook(() => {
    const manager = useAiModelsManager({ providerErrorKey: 'providers.error', modelsErrorKey: 'models.error', ...options });
    // Subscribe to what the pages read.
    return {
      providers: manager.providers,
      configuredModels: manager.configuredModels,
      modelsLoaded: manager.modelsLoaded,
      isLoading: manager.isLoading,
      handleSetDefault: manager.handleSetDefault,
      loadProviders: manager.loadProviders,
      loadModels: manager.loadModels,
    };
  });
  await settle();
  return view;
}

describe('useAiModelsManager cache', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    clearSessionCache();
    api.getRegistry.mockResolvedValue(REGISTRY);
    api.getAllModels.mockResolvedValue(MODELS);
    api.setDefault.mockResolvedValue({});
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('a revisit of the page makes no registry or models request', async () => {
    (await mountManager()).unmount();
    await settle(3_600_000);
    const view = await mountManager();

    expect(view.result.current.providers).toEqual(REGISTRY.providers);
    expect(view.result.current.isLoading).toBe(false);
    expect(api.getRegistry).toHaveBeenCalledTimes(1);
    expect(api.getAllModels).toHaveBeenCalledTimes(1);
  });

  it('keeps the onboarding and settings registries apart', async () => {
    await mountManager();
    await mountManager({ registryCapability: 'embedding' });

    expect(api.getRegistry).toHaveBeenCalledTimes(2);
    expect(api.getRegistry).toHaveBeenLastCalledWith({ capability: 'embedding' });
  });

  it('setting a default refetches the configured models once and never the registry', async () => {
    const view = await mountManager();

    await act(async () => {
      await view.result.current.handleSetDefault('llm', 'k1');
    });
    await settle();

    expect(api.getAllModels).toHaveBeenCalledTimes(2);
    expect(api.getRegistry).toHaveBeenCalledTimes(1);
  });

  it('a failed default change refetches nothing', async () => {
    api.setDefault.mockRejectedValue(new Error('500'));
    const view = await mountManager();

    await act(async () => {
      await view.result.current.handleSetDefault('llm', 'k1');
    });
    await settle();

    expect(toast.error).toHaveBeenCalledWith('workspace.aiModels.toastDefaultError');
    expect(api.getAllModels).toHaveBeenCalledTimes(1);
  });

  it('Refresh rereads the registry and the models', async () => {
    const view = await mountManager();

    await act(async () => {
      await Promise.all([view.result.current.loadProviders(), view.result.current.loadModels()]);
    });

    expect(api.getRegistry).toHaveBeenCalledTimes(2);
    expect(api.getAllModels).toHaveBeenCalledTimes(2);
  });

  it('reports a registry failure once, with an empty provider list', async () => {
    api.getRegistry.mockRejectedValue(new Error('down'));
    const onProvidersLoaded = vi.fn();

    const view = await mountManager({ onProvidersLoaded });
    await settle(100);

    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith('providers.error');
    expect(onProvidersLoaded).toHaveBeenCalledTimes(1);
    expect(onProvidersLoaded).toHaveBeenCalledWith([]);
    expect(view.result.current.providers).toEqual([]);
  });

  it('reports the providers of each visit to onboarding, also from the cache', async () => {
    const onProvidersLoaded = vi.fn();
    (await mountManager({ onProvidersLoaded })).unmount();
    await mountManager({ onProvidersLoaded });

    expect(onProvidersLoaded).toHaveBeenCalledTimes(2);
    expect(api.getRegistry).toHaveBeenCalledTimes(1);
  });

  it('after a models failure: loaded, empty, and stable for effects that depend on it', async () => {
    api.getAllModels.mockRejectedValue(new Error('down'));
    const effectRuns = vi.fn();
    const view = renderHook(() => {
      const { configuredModels, modelsLoaded } = useAiModelsManager({ providerErrorKey: 'p', modelsErrorKey: 'm' });
      useEffect(() => {
        effectRuns();
      }, [configuredModels]);
      return modelsLoaded;
    });
    await settle(100);
    view.rerender();
    view.rerender();

    expect(view.result.current).toBe(true);
    expect(toast.error).toHaveBeenCalledWith('m');
    expect(effectRuns).toHaveBeenCalledTimes(1);
  });
});

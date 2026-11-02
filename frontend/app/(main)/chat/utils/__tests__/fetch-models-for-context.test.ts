import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AvailableLlmModel } from '@/chat/types';

const chatApi = vi.hoisted(() => ({ fetchOrgLlmCatalog: vi.fn() }));
const agentsApi = vi.hoisted(() => ({ getAgent: vi.fn() }));
vi.mock('@/chat/api', () => ({ ChatApi: chatApi }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: agentsApi }));
vi.mock('@/config', async () => ({
  chatModelOptions: (await vi.importActual<typeof import('../chat-model-options')>('../chat-model-options')).chatModelOptions,
}));

import { fetchModelsForContext, OrgModelsFetchError } from '../fetch-models-for-context';
import { ASSISTANT_CTX, useChatStore } from '@/chat/store';
import { clearSessionCache } from '@/lib/api/session-cache';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';

function model(modelKey: string, isDefault = false): AvailableLlmModel {
  return { modelKey, modelName: modelKey, modelFriendlyName: modelKey, provider: 'openAI', isDefault } as AvailableLlmModel;
}

function orgList(...models: AvailableLlmModel[]) {
  return { models, response: { models } };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const settings = () => useChatStore.getState().settings;
const keys = (models: AvailableLlmModel[]) => models.map((m) => m.modelKey);

beforeEach(() => {
  clearSessionCache();
  vi.clearAllMocks();
  vi.useRealTimers();
  chatApi.fetchOrgLlmCatalog.mockResolvedValue(orgList(model('gpt', true), model('claude')));
});

describe('fetchModelsForContext: the org list', () => {
  it('is fetched once per session', async () => {
    await fetchModelsForContext(ASSISTANT_CTX);
    vi.useFakeTimers();
    vi.setSystemTime(Date.now() + 3_600_000);

    const again = await fetchModelsForContext(ASSISTANT_CTX);

    expect(keys(again)).toEqual(['gpt', 'claude']);
    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(1);
    expect(settings().defaultModels[ASSISTANT_CTX]?.modelKey).toBe('gpt');
  });

  it('shares one request between concurrent callers, including an agent without models', async () => {
    agentsApi.getAgent.mockResolvedValue({ agent: { models: [] } });

    await Promise.all([fetchModelsForContext(ASSISTANT_CTX), fetchModelsForContext('agent-1')]);

    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(1);
    expect(keys(settings().availableModels['agent-1'].models)).toEqual(['gpt', 'claude']);
  });

  it('is fetched again when the model picker forces it', async () => {
    await fetchModelsForContext(ASSISTANT_CTX);
    await fetchModelsForContext(ASSISTANT_CTX, { force: true });

    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(2);
  });

  it('a failure is not cached: the next call asks again', async () => {
    chatApi.fetchOrgLlmCatalog.mockRejectedValueOnce(new Error('down'));
    await expect(fetchModelsForContext(ASSISTANT_CTX)).rejects.toThrow('down');

    await fetchModelsForContext(ASSISTANT_CTX);

    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(2);
  });

  it('an agent without models reports a failed org list as an org-models failure', async () => {
    agentsApi.getAgent.mockResolvedValue({ agent: { models: [] } });
    chatApi.fetchOrgLlmCatalog.mockRejectedValueOnce(new Error('down'));

    await expect(fetchModelsForContext('agent-1')).rejects.toBeInstanceOf(OrgModelsFetchError);
  });
});

describe('fetchModelsForContext: agent lists', () => {
  it('still refetch after 60 s', async () => {
    agentsApi.getAgent.mockResolvedValue({ agent: { models: [{ modelKey: 'own', modelName: 'own', provider: 'openAI' }] } });
    vi.useFakeTimers();
    await fetchModelsForContext('agent-1');
    vi.setSystemTime(Date.now() + 59_000);
    await fetchModelsForContext('agent-1');
    expect(agentsApi.getAgent).toHaveBeenCalledTimes(1);

    vi.setSystemTime(Date.now() + 2_000);
    await fetchModelsForContext('agent-1');
    expect(agentsApi.getAgent).toHaveBeenCalledTimes(2);
  });
});

describe('fetchModelsForContext after an AI-model change in Settings', () => {
  it('refetches the org list on the next call and drops a selection that was removed', async () => {
    await fetchModelsForContext(ASSISTANT_CTX);
    useChatStore.getState().setSelectedModelForCtx(ASSISTANT_CTX, {
      modelKey: 'claude', modelName: 'claude', modelFriendlyName: 'claude', modelProvider: 'openAI',
    });
    chatApi.fetchOrgLlmCatalog.mockResolvedValue(orgList(model('gpt', true)));

    revalidateSwrNamespace(SWR_NS.aiModelConfig);
    expect(settings().availableModels[ASSISTANT_CTX]).toBeUndefined();
    await fetchModelsForContext(ASSISTANT_CTX);

    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(2);
    expect(settings().selectedModels[ASSISTANT_CTX]).toBeNull();
  });

  it("keeps an agent's own models and drops an agent list that came from the org", async () => {
    agentsApi.getAgent.mockImplementation(async (id: string) => ({
      agent: { models: id === 'own' ? [{ modelKey: 'x', modelName: 'x', provider: 'openAI' }] : [] },
    }));
    await fetchModelsForContext('own');
    await fetchModelsForContext('fallback');

    revalidateSwrNamespace(SWR_NS.aiModelConfig);

    expect(settings().availableModels.own).toBeDefined();
    expect(settings().availableModels.fallback).toBeUndefined();
  });

  it('a response in flight during the change is replaced by one fresh request', async () => {
    const stale = deferred<ReturnType<typeof orgList>>();
    chatApi.fetchOrgLlmCatalog
      .mockReturnValueOnce(stale.promise)
      .mockResolvedValueOnce(orgList(model('new', true)));

    const call = fetchModelsForContext(ASSISTANT_CTX);
    revalidateSwrNamespace(SWR_NS.aiModelConfig);
    stale.resolve(orgList(model('old', true)));

    expect(keys(await call)).toEqual(['new']);
    expect(keys(settings().availableModels[ASSISTANT_CTX].models)).toEqual(['new']);
    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(2);
  });
});

describe('fetchModelsForContext: an agent with its own models during an AI-model change', () => {
  it('keeps its answer and asks for the agent only once', async () => {
    const pending = deferred<{ agent: unknown }>();
    agentsApi.getAgent.mockReturnValueOnce(pending.promise);

    const call = fetchModelsForContext('own');
    revalidateSwrNamespace(SWR_NS.aiModelConfig);
    pending.resolve({ agent: { models: [{ modelKey: 'x', modelName: 'x', provider: 'openAI' }] } });

    expect(keys(await call)).toEqual(['x']);
    expect(agentsApi.getAgent).toHaveBeenCalledTimes(1);
    expect(keys(settings().availableModels.own.models)).toEqual(['x']);
  });
});

describe('fetchModelsForContext at sign-out', () => {
  it("does not let the next user join the last user's request or see its answer", async () => {
    const lastUser = deferred<ReturnType<typeof orgList>>();
    chatApi.fetchOrgLlmCatalog.mockReturnValueOnce(lastUser.promise);
    const lastCall = fetchModelsForContext(ASSISTANT_CTX);

    clearSessionCache();
    const nextUser = await fetchModelsForContext(ASSISTANT_CTX);
    expect(keys(nextUser)).toEqual(['gpt', 'claude']);
    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(2);

    lastUser.resolve(orgList(model('last-user-only', true)));
    await lastCall;

    expect(keys(settings().availableModels[ASSISTANT_CTX].models)).toEqual(['gpt', 'claude']);
    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(2);
  });

  it('starts the next session with an empty model list', async () => {
    await fetchModelsForContext(ASSISTANT_CTX);

    clearSessionCache();

    expect(settings().availableModels).toEqual({});
    expect(settings().defaultModels).toEqual({});
  });
});

describe('ChatApi.fetchAvailableLlms (kept for edition code)', () => {
  it('reads the same session-cached org list', async () => {
    const { ChatApi } = await vi.importActual<typeof import('@/chat/api')>('@/chat/api');
    await fetchModelsForContext(ASSISTANT_CTX);

    expect(keys(await ChatApi.fetchAvailableLlms())).toEqual(['gpt', 'claude']);
    expect(chatApi.fetchOrgLlmCatalog).toHaveBeenCalledTimes(1);
  });
});

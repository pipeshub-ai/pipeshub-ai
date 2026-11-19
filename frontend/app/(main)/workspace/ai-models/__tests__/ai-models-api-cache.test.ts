import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { streamSSERequest } from '@/lib/api/streaming';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { AIModelsApi } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/api/streaming', () => ({ streamSSERequest: vi.fn(), streamSSEGet: vi.fn() }));
vi.mock('@/lib/api/swr-cache', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/api/swr-cache')>()),
  revalidateSwrNamespace: vi.fn(),
}));

const api = vi.mocked(apiClient);
const stream = vi.mocked(streamSSERequest);
const revalidate = vi.mocked(revalidateSwrNamespace);
const SSE = { onEvent: vi.fn(), onError: vi.fn() };
const BATCH = { modelType: 'llm', provider: 'openAI', configuration: {}, models: [] };

const writes: Array<[string, () => Promise<unknown>]> = [
  ['add', () => AIModelsApi.addProvider({ modelType: 'llm', provider: 'openAI', configuration: {} })],
  ['edit', () => AIModelsApi.updateProvider('llm', 'k1', { provider: 'openAI', configuration: {} })],
  ['delete', () => AIModelsApi.deleteProvider('llm', 'k1')],
  ['set default', () => AIModelsApi.setDefault('llm', 'k1')],
  ['rotate credentials', () => AIModelsApi.rotateConnectionCredentials('c1', { apiKey: 'x' })],
  ['identity forwarding', () => AIModelsApi.setConnectionIdentityForwarding('c1', { enabled: true } as never)],
];

describe('AI-model writes refresh the cached model config', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    for (const method of [api.post, api.put, api.delete]) method.mockResolvedValue({ data: {} });
  });

  it.each(writes)('after a successful %s', async (_name, write) => {
    await write();

    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(revalidate).toHaveBeenCalledWith(SWR_NS.aiModelConfig);
  });

  it.each(writes)('not after a failed %s', async (_name, write) => {
    for (const method of [api.post, api.put, api.delete]) method.mockRejectedValue(new Error('500'));

    await expect(write()).rejects.toThrow('500');

    expect(revalidate).not.toHaveBeenCalled();
  });

  it('a batch add refreshes when the stream ends', async () => {
    stream.mockResolvedValue(undefined);

    await AIModelsApi.batchAddModels(BATCH, SSE);

    expect(revalidate).toHaveBeenCalledWith(SWR_NS.aiModelConfig);
  });

  it('a batch add refreshes even when the stream fails, since earlier rows may be saved', async () => {
    stream.mockRejectedValue(new Error('dropped'));

    await expect(AIModelsApi.batchAddModels(BATCH, SSE)).rejects.toThrow('dropped');

    expect(revalidate).toHaveBeenCalledWith(SWR_NS.aiModelConfig);
  });

  it('saving the indexing role invalidates nothing: the roles section writes its own cache', async () => {
    await AIModelsApi.updateRoles({ indexing: { modelType: 'llm', modelKey: 'k1' } });

    expect(revalidate).not.toHaveBeenCalled();
  });

  it('never invalidates the provider registry', async () => {
    for (const [, write] of writes) await write();

    expect(revalidate.mock.calls.every(([ns]) => ns !== SWR_NS.aiModelRegistry)).toBe(true);
  });
});

import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SSEStreamingOptions } from '@/lib/api/streaming';
import type { PickedModel } from '../types';

const api = vi.hoisted(() => ({ batchAddModels: vi.fn() }));
vi.mock('../api', () => ({ AIModelsApi: api }));

import { failUnresolvedRows, useBatchAddModels } from '../hooks/use-batch-add-models';

const picked = (id: string): PickedModel => ({
  id,
  isReasoning: false,
  isMultimodal: false,
  contextLength: null,
});

afterEach(() => {
  api.batchAddModels.mockReset();
});

describe('failUnresolvedRows', () => {
  it('fails rows the stream never finished and leaves finished rows alone', () => {
    const next = failUnresolvedRows([
      { model: 'saved', status: 'healthy' },
      { model: 'checking', status: 'checking' },
      { model: 'waiting', status: 'pending' },
      { model: 'rejected', status: 'failed', message: 'nope' },
    ]);
    expect(next.map((row) => [row.model, row.status, row.message])).toEqual([
      ['saved', 'healthy', undefined],
      ['checking', 'failed', 'interrupted'],
      ['waiting', 'failed', 'interrupted'],
      ['rejected', 'failed', 'nope'],
    ]);
  });
});

describe('useBatchAddModels', () => {
  it('marks rows failed when the stream ends without a terminal event', async () => {
    api.batchAddModels.mockImplementation(async (_body: unknown, options: SSEStreamingOptions) => {
      options.onEvent({ event: 'progress', data: { model: 'gpt-5.6-luna', status: 'checking' } });
    });
    const { result } = renderHook(() => useBatchAddModels());
    let outcome: { rows: { model: string; status: string; message?: string }[] } | undefined;
    await act(async () => {
      outcome = await result.current.run({
        modelType: 'llm',
        provider: 'openAI',
        configuration: { apiKey: 'sk' },
        models: [picked('gpt-5.6-luna'), picked('gpt-5.6-terra')],
      });
    });
    expect(outcome?.rows.map((row) => [row.model, row.status, row.message])).toEqual([
      ['gpt-5.6-luna', 'failed', 'interrupted'],
      ['gpt-5.6-terra', 'failed', 'interrupted'],
    ]);
  });

  it('sends size and thinking only for a model whose detected value was overridden', async () => {
    api.batchAddModels.mockResolvedValue(undefined);
    const { result } = renderHook(() => useBatchAddModels());
    await act(async () => {
      await result.current.run({
        modelType: 'llm',
        provider: 'openAI',
        configuration: { apiKey: 'sk' },
        models: [{ ...picked('gpt-5.6-luna'), size: 'large', thinking: 'always' }, picked('gpt-5.6-terra')],
      });
    });
    const body = api.batchAddModels.mock.calls[0][0] as { models: Record<string, unknown>[] };
    expect(body.models[0]).toMatchObject({ size: 'large', thinking: 'always' });
    expect(body.models[1]).not.toHaveProperty('size');
    expect(body.models[1]).not.toHaveProperty('thinking');
  });
});

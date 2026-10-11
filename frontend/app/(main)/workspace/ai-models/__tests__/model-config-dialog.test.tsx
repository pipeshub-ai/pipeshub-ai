import '@/lib/__tests__/test-i18n';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { SSEStreamingOptions } from '@/lib/api/streaming';
import type { AIModelProvider } from '../types';

const api = vi.hoisted(() => ({
  addProvider: vi.fn(),
  batchAddModels: vi.fn(),
  discoverModels: vi.fn(),
  prepareModel: vi.fn(),
  updateProvider: vi.fn(),
}));
vi.mock('../api', () => ({ AIModelsApi: api }));

import { ModelConfigDialog } from '../components/model-config-dialog';

const field = (name: string, extra: Record<string, unknown>) => ({
  name,
  displayName: name,
  fieldType: 'TEXT',
  required: false,
  ...extra,
});

const MANUAL_PROVIDER = {
  providerId: 'azureAI',
  name: 'Azure AI Foundry',
  description: '',
  capabilities: ['text_generation'],
  iconPath: '',
  color: '#000',
  discovery: { mode: 'manual', requiredFields: [] },
  fields: {
    text_generation: [
      field('modelFriendlyName', { placeholder: 'Friendly name' }),
      field('apiKey', { fieldType: 'PASSWORD', required: true, placeholder: 'API key' }),
      field('model', { required: true, placeholder: 'Model id' }),
      field('contextLength', { fieldType: 'NUMBER', displayName: 'Context Length', placeholder: 'e.g. 128000' }),
      field('isReasoning', { fieldType: 'BOOLEAN', displayName: 'Reasoning', defaultValue: true }),
      field('isMultimodal', { fieldType: 'BOOLEAN', displayName: 'Multimodal', defaultValue: true }),
    ],
  },
} as unknown as AIModelProvider;

const LOCAL_EMBEDDING_PROVIDER = {
  providerId: 'sentenceTransformers',
  name: 'Sentence Transformers',
  description: '',
  capabilities: ['embedding'],
  iconPath: '',
  color: '#000',
  discovery: { mode: 'manual', requiredFields: [] },
  fields: {
    embedding: [field('model', { required: true, placeholder: 'Model id' })],
  },
} as unknown as AIModelProvider;

function renderDialog(
  onClose = vi.fn(),
  onSaved = vi.fn(),
  props: Partial<React.ComponentProps<typeof ModelConfigDialog>> = {},
) {
  render(
    <Theme>
      <ModelConfigDialog
        open
        mode="add"
        provider={MANUAL_PROVIDER}
        capability="text_generation"
        editModel={null}
        onClose={onClose}
        onSaved={onSaved}
        {...props}
      />
    </Theme>,
  );
  return { onClose, onSaved };
}

function addCustomIds(ids: string[]) {
  for (const id of ids) {
    fireEvent.change(screen.getByLabelText('Custom model id'), { target: { value: id } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  }
}

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset();
});

afterEach(cleanup);

describe('ModelConfigDialog without a model list', () => {
  it('saves one typed model through the single-model path with the form flags', async () => {
    api.addProvider.mockResolvedValue({});
    const { onClose } = renderDialog();

    expect(screen.getByTestId('ai-manual-models-hint')).toBeTruthy();
    expect(screen.queryByTestId('ai-fetch-models')).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    fireEvent.change(screen.getByPlaceholderText('Model id'), { target: { value: 'gpt-4o-2024' } });
    fireEvent.click(screen.getAllByRole('switch')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await waitFor(() => expect(api.addProvider).toHaveBeenCalledTimes(1));
    expect(api.addProvider.mock.calls[0][0]).toMatchObject({
      provider: 'azureAI',
      configuration: { apiKey: 'sk-1', model: 'gpt-4o-2024' },
      isReasoning: false,
      isMultimodal: true,
    });
    expect(api.batchAddModels).not.toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('keeps the typed model when more ids are added and sends per-model flags', async () => {
    api.batchAddModels.mockImplementation(
      async (body: { models: { model: string }[] }, options: SSEStreamingOptions) => {
        for (const item of body.models) {
          options.onEvent({ event: 'progress', data: { model: item.model, status: 'healthy' } });
        }
        options.onEvent({
          event: 'done',
          data: { connectionId: '7b7c4f62-2f5c-4c55-9a55-1f3f7c1d2f10', saved: body.models.length, aborted: false },
        });
      },
    );
    const { onClose, onSaved } = renderDialog();

    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    fireEvent.change(screen.getByPlaceholderText('Friendly name'), { target: { value: 'Model A' } });
    fireEvent.change(screen.getByPlaceholderText('Model id'), { target: { value: 'model-a' } });
    fireEvent.click(screen.getAllByRole('switch')[0]);

    fireEvent.change(screen.getByLabelText('Custom model id'), { target: { value: 'model-b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(screen.queryByPlaceholderText('Model id')).toBeNull();
    expect(screen.queryByPlaceholderText('Friendly name')).toBeNull();
    expect(screen.getByRole('switch', { name: 'Reasoning for model-a' }).getAttribute('aria-checked')).toBe('false');
    expect(screen.getByRole('switch', { name: 'Multimodal for model-a' }).getAttribute('aria-checked')).toBe('true');

    fireEvent.click(screen.getByRole('switch', { name: 'Reasoning for model-b' }));
    fireEvent.change(screen.getByLabelText('Context Length for model-b'), { target: { value: '64000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await waitFor(() => expect(api.batchAddModels).toHaveBeenCalledTimes(1));
    const body = api.batchAddModels.mock.calls[0][0];
    expect(body.configuration).toEqual({ apiKey: 'sk-1' });
    expect(body.models).toEqual([
      expect.objectContaining({ model: 'model-a', modelFriendlyName: 'Model A', isReasoning: false, isMultimodal: true }),
      expect.objectContaining({ model: 'model-b', isReasoning: true, isMultimodal: true, contextLength: 64000 }),
    ]);
    expect(body.defaultModel).toBe('model-a');
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it('keeps the failed model selected and retries it alone in the same connection', async () => {
    const connectionId = '7b7c4f62-2f5c-4c55-9a55-1f3f7c1d2f10';
    api.batchAddModels
      .mockImplementationOnce(async (_body: unknown, options: SSEStreamingOptions) => {
        options.onEvent({ event: 'progress', data: { model: 'model-a', status: 'healthy' } });
        options.onEvent({ event: 'progress', data: { model: 'model-b', status: 'failed', message: 'not deployed' } });
        options.onEvent({ event: 'done', data: { connectionId, saved: 1, aborted: false } });
      })
      .mockImplementationOnce(async (_body: unknown, options: SSEStreamingOptions) => {
        options.onEvent({ event: 'progress', data: { model: 'model-b', status: 'healthy' } });
        options.onEvent({ event: 'done', data: { connectionId, saved: 1, aborted: false } });
      });
    const { onClose } = renderDialog();

    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    for (const id of ['model-a', 'model-b']) {
      fireEvent.change(screen.getByLabelText('Custom model id'), { target: { value: id } });
      fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    }
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await screen.findByText(/not deployed/);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByRole('switch', { name: 'Reasoning for model-a' })).toBeNull();
    expect(screen.getByRole('switch', { name: 'Reasoning for model-b' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(api.batchAddModels).toHaveBeenCalledTimes(2));
    const retry = api.batchAddModels.mock.calls[1][0];
    expect(retry.models.map((item: { model: string }) => item.model)).toEqual(['model-b']);
    expect(retry.connectionId).toBe(connectionId);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('does not ask a retry to become the default when the type already has models', async () => {
    const connectionId = '7b7c4f62-2f5c-4c55-9a55-1f3f7c1d2f10';
    api.batchAddModels
      .mockImplementationOnce(async (_body: unknown, options: SSEStreamingOptions) => {
        options.onEvent({ event: 'progress', data: { model: 'model-a', status: 'failed', message: 'not deployed' } });
        options.onEvent({ event: 'progress', data: { model: 'model-b', status: 'healthy' } });
        options.onEvent({ event: 'done', data: { connectionId, saved: 1, aborted: false } });
      })
      .mockImplementationOnce(async (_body: unknown, options: SSEStreamingOptions) => {
        options.onEvent({ event: 'progress', data: { model: 'model-a', status: 'healthy' } });
        options.onEvent({ event: 'done', data: { connectionId, saved: 1, aborted: false } });
      });
    renderDialog(vi.fn(), vi.fn(), { existingModelsCount: 2 });

    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    addCustomIds(['model-a', 'model-b']);
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await screen.findByText(/not deployed/);
    expect(api.batchAddModels.mock.calls[0][0].defaultModel).toBeUndefined();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(api.batchAddModels).toHaveBeenCalledTimes(2));
    const retry = api.batchAddModels.mock.calls[1][0];
    expect(retry.models.map((item: { model: string }) => item.model)).toEqual(['model-a']);
    expect(retry.defaultModel).toBeUndefined();
  });

  it('prepares every picked local embedding model before the batch health checks', async () => {
    const order: string[] = [];
    api.prepareModel.mockImplementation(async (modelName: string) => {
      order.push(`prepare:${modelName}`);
      return { status: 'ready' };
    });
    api.batchAddModels.mockImplementation(async (body: { models: { model: string }[] }) => {
      order.push(`batch:${body.models.map((item) => item.model).join(',')}`);
    });
    renderDialog(vi.fn(), vi.fn(), { provider: LOCAL_EMBEDDING_PROVIDER, capability: 'embedding' });

    addCustomIds(['BAAI/bge-small-en-v1.5', 'BAAI/bge-base-en-v1.5']);
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await waitFor(() => expect(api.batchAddModels).toHaveBeenCalledTimes(1));
    expect(order).toEqual([
      'prepare:BAAI/bge-small-en-v1.5',
      'prepare:BAAI/bge-base-en-v1.5',
      'batch:BAAI/bge-small-en-v1.5,BAAI/bge-base-en-v1.5',
    ]);
  });

  it('saves an Azure OpenAI model id without a comma', async () => {
    api.batchAddModels.mockResolvedValue(undefined);
    render(
      <Theme>
        <ModelConfigDialog
          open
          mode="add"
          provider={{ ...MANUAL_PROVIDER, providerId: 'azureOpenAI' }}
          capability="text_generation"
          editModel={null}
          onClose={vi.fn()}
          onSaved={vi.fn()}
        />
      </Theme>,
    );
    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    fireEvent.change(screen.getByLabelText('Custom model id'), { target: { value: 'gpt-5.6-luna, extra' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(screen.getByRole('switch', { name: 'Reasoning for gpt-5.6-luna' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));
    await waitFor(() => expect(api.batchAddModels).toHaveBeenCalledTimes(1));
    const body = api.batchAddModels.mock.calls[0][0];
    expect(body.models.map((item: { model: string }) => item.model)).toEqual(['gpt-5.6-luna']);
    expect(body.defaultModel).toBe('gpt-5.6-luna');
  });
});

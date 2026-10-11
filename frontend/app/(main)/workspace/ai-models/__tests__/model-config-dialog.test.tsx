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

const toastMock = vi.hoisted(() => ({
  loading: vi.fn(() => 'toast-1'),
  update: vi.fn(),
  dismiss: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));
vi.mock('@/lib/store/toast-store', () => ({ toast: toastMock }));

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

function addModelIds(ids: string[]) {
  const input = screen.getByRole('combobox', { name: 'model' });
  for (const id of ids) {
    fireEvent.change(input, { target: { value: id } });
    fireEvent.keyDown(input, { key: 'Enter' });
  }
}

// jsdom has no layout, so it does not implement scrollIntoView.
const scrollIntoView = vi.fn();

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset();
  for (const mock of Object.values(toastMock)) mock.mockClear();
  scrollIntoView.mockClear();
  Element.prototype.scrollIntoView = scrollIntoView;
});

afterEach(cleanup);

describe('ModelConfigDialog without a model list', () => {
  it('saves one typed model through the single-model path with the form flags', async () => {
    api.addProvider.mockResolvedValue({});
    const { onClose } = renderDialog();

    expect(
      screen.getByText('Type a model name and press Enter. Add several to save them together.'),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Fetch models' })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    addModelIds(['gpt-4o-2024']);
    fireEvent.click(screen.getAllByRole('switch')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await waitFor(() => expect(api.addProvider).toHaveBeenCalledTimes(1));
    expect(toastMock.loading).toHaveBeenCalledWith('Checking gpt-4o-2024 with Azure AI Foundry…', {
      description: 'Each model gets a short test call. This usually takes under a minute.',
    });
    await waitFor(() => expect(toastMock.dismiss).toHaveBeenCalledWith('toast-1'));
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
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
    addModelIds(['model-a']);
    fireEvent.click(screen.getAllByRole('switch')[0]);

    addModelIds(['model-b']);

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
    addModelIds(['model-a', 'model-b']);
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await screen.findByText(/not deployed/);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Remove model-a' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Remove model-b' })).toBeTruthy();
    // One model left, so it is edited in the regular form again.
    expect(screen.getByPlaceholderText('Friendly name')).toBeTruthy();

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
    addModelIds(['model-a', 'model-b']);
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await screen.findByText(/not deployed/);
    expect(api.batchAddModels.mock.calls[0][0].defaultModel).toBeUndefined();
    expect(toastMock.loading).toHaveBeenCalledWith('Checking 2 models with Azure AI Foundry…', expect.anything());
    expect(toastMock.update).toHaveBeenLastCalledWith('toast-1', expect.objectContaining({
      variant: 'error',
      title: "1 of 2 models didn't pass the check",
    }));

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(api.batchAddModels).toHaveBeenCalledTimes(2));
    const retry = api.batchAddModels.mock.calls[1][0];
    expect(retry.models.map((item: { model: string }) => item.model)).toEqual(['model-a']);
    expect(retry.defaultModel).toBeUndefined();
  });

  it('shows the save error when a retry request fails', async () => {
    const connectionId = '7b7c4f62-2f5c-4c55-9a55-1f3f7c1d2f10';
    api.batchAddModels
      .mockImplementationOnce(async (_body: unknown, options: SSEStreamingOptions) => {
        options.onEvent({ event: 'progress', data: { model: 'model-a', status: 'failed', message: 'not deployed' } });
        options.onEvent({ event: 'progress', data: { model: 'model-b', status: 'healthy' } });
        options.onEvent({ event: 'done', data: { connectionId, saved: 1, aborted: false } });
      })
      .mockRejectedValueOnce(new Error('network down'));
    renderDialog();

    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    addModelIds(['model-a', 'model-b']);
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));
    await screen.findByText(/not deployed/);

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Failed to save model configuration')).toBeTruthy();
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

    addModelIds(['BAAI/bge-small-en-v1.5', 'BAAI/bge-base-en-v1.5']);
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));

    await waitFor(() => expect(api.batchAddModels).toHaveBeenCalledTimes(1));
    expect(order).toEqual([
      'prepare:BAAI/bge-small-en-v1.5',
      'prepare:BAAI/bge-base-en-v1.5',
      'batch:BAAI/bge-small-en-v1.5,BAAI/bge-base-en-v1.5',
    ]);
  });

  it('keeps Azure OpenAI to one model per deployment and drops a pasted comma list', async () => {
    api.addProvider.mockResolvedValue({});
    const azure = {
      ...MANUAL_PROVIDER,
      providerId: 'azureOpenAI',
      fields: {
        text_generation: [
          ...MANUAL_PROVIDER.fields.text_generation,
          field('deploymentName', { required: true, placeholder: 'Deployment' }),
        ],
      },
    } as unknown as AIModelProvider;
    renderDialog(vi.fn(), vi.fn(), { provider: azure });

    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    fireEvent.change(screen.getByPlaceholderText('Deployment'), { target: { value: 'prod-gpt' } });
    addModelIds(['gpt-5.6-luna, extra', 'gpt-5.6-nova']);
    expect(screen.queryByRole('button', { name: 'Remove gpt-5.6-luna' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Remove gpt-5.6-nova' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));
    await waitFor(() => expect(api.addProvider).toHaveBeenCalledTimes(1));
    expect(api.addProvider.mock.calls[0][0].configuration).toMatchObject({
      model: 'gpt-5.6-nova',
      deploymentName: 'prod-gpt',
    });
    expect(api.batchAddModels).not.toHaveBeenCalled();
  });

  it('fills the Azure deployment name from the model until the user types their own', async () => {
    api.addProvider.mockResolvedValue({});
    const azure = {
      ...MANUAL_PROVIDER,
      providerId: 'azureOpenAI',
      fields: {
        text_generation: [
          ...MANUAL_PROVIDER.fields.text_generation,
          field('deploymentName', { required: true, placeholder: 'Deployment' }),
        ],
      },
    } as unknown as AIModelProvider;
    renderDialog(vi.fn(), vi.fn(), { provider: azure });
    const deployment = screen.getByPlaceholderText('Deployment') as HTMLInputElement;

    addModelIds(['gpt-6-luna']);
    expect(deployment.value).toBe('gpt-6-luna');
    addModelIds(['gpt-5.4-mini']);
    expect(deployment.value).toBe('gpt-5.4-mini');

    fireEvent.change(deployment, { target: { value: 'prod-mini' } });
    addModelIds(['gpt-6-luna']);
    expect(deployment.value).toBe('prod-mini');

    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));
    await waitFor(() => expect(api.addProvider).toHaveBeenCalledTimes(1));
    expect(api.addProvider.mock.calls[0][0].configuration).toMatchObject({
      model: 'gpt-6-luna',
      deploymentName: 'prod-mini',
    });
  });
});

const LISTING_PROVIDER = {
  ...MANUAL_PROVIDER,
  providerId: 'openAI',
  name: 'OpenAI',
  discovery: { mode: 'list', requiredFields: ['apiKey'] },
} as unknown as AIModelProvider;

describe('ModelConfigDialog with a model list', () => {
  it('loads models once the API key is filled and fills the form from the picked model', async () => {
    api.discoverModels.mockResolvedValue({
      success: true,
      supported: true,
      models: [
        {
          id: 'gpt-5-mini',
          displayName: 'gpt-5-mini',
          capabilities: ['llm'],
          contextLength: 272000,
          isReasoning: true,
          isMultimodal: false,
        },
      ],
    });
    api.addProvider.mockResolvedValue({});
    renderDialog(vi.fn(), vi.fn(), { provider: LISTING_PROVIDER });

    expect(
      screen.getByText('Fill in the connection details above to load the model list, or type a model name.'),
    ).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });

    await waitFor(() => expect(api.discoverModels).toHaveBeenCalledTimes(1));
    expect(api.discoverModels.mock.calls[0][0]).toMatchObject({
      provider: 'openAI',
      capability: 'text_generation',
      configuration: { apiKey: 'sk-1' },
    });
    expect(await screen.findByText('1 model available. Search, or type any model name.')).toBeTruthy();

    fireEvent.focus(screen.getByRole('combobox', { name: 'model' }));
    fireEvent.click(screen.getByRole('option', { name: /gpt-5-mini/ }));
    expect((screen.getByPlaceholderText('e.g. 128000') as HTMLInputElement).value).toBe('272000');

    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));
    await waitFor(() => expect(api.addProvider).toHaveBeenCalledTimes(1));
    expect(api.addProvider.mock.calls[0][0]).toMatchObject({
      configuration: { apiKey: 'sk-1', model: 'gpt-5-mini' },
      isReasoning: true,
      isMultimodal: false,
      contextLength: 272000,
    });
  });
});

describe('ModelConfigDialog while the model list is loading', () => {
  function deferredDiscovery() {
    let resolve: (value: unknown) => void = () => undefined;
    api.discoverModels.mockReturnValue(new Promise((done) => { resolve = done; }));
    return (models: unknown[]) => resolve({ success: true, supported: true, models });
  }

  it('does not turn a half-typed search into a model when the field loses focus', async () => {
    deferredDiscovery();
    renderDialog(vi.fn(), vi.fn(), { provider: LISTING_PROVIDER });
    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });

    const input = screen.getByRole('combobox', { name: 'model' });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'gpt' } });
    fireEvent.blur(input);
    expect(screen.queryByRole('button', { name: 'Remove gpt' })).toBeNull();

    await waitFor(() => expect(api.discoverModels).toHaveBeenCalledTimes(1));
    fireEvent.blur(input);
    expect(screen.queryByRole('button', { name: 'Remove gpt' })).toBeNull();
  });

  it('fills a model typed before the list arrives from what the provider reports', async () => {
    const resolve = deferredDiscovery();
    renderDialog(vi.fn(), vi.fn(), { provider: LISTING_PROVIDER });
    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    addModelIds(['gpt-5-mini']);
    await waitFor(() => expect(api.discoverModels).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('switch', { name: 'Multimodal' }).getAttribute('aria-checked')).toBe('true');

    resolve([
      {
        id: 'gpt-5-mini',
        displayName: 'gpt-5-mini',
        capabilities: ['llm'],
        contextLength: 272000,
        isReasoning: true,
        isMultimodal: false,
      },
    ]);

    await waitFor(() =>
      expect((screen.getByPlaceholderText('e.g. 128000') as HTMLInputElement).value).toBe('272000'),
    );
    expect(screen.getByRole('switch', { name: 'Multimodal' }).getAttribute('aria-checked')).toBe('false');
  });

  it('keeps settings changed in the form before the id was added', async () => {
    const resolve = deferredDiscovery();
    renderDialog(vi.fn(), vi.fn(), { provider: LISTING_PROVIDER });
    fireEvent.change(screen.getByPlaceholderText('API key'), { target: { value: 'sk-1' } });
    fireEvent.click(screen.getByRole('switch', { name: 'Multimodal' }));
    fireEvent.change(screen.getByPlaceholderText('e.g. 128000'), { target: { value: '16000' } });
    addModelIds(['gpt-5-mini']);
    await waitFor(() => expect(api.discoverModels).toHaveBeenCalledTimes(1));

    resolve([
      { id: 'gpt-5-mini', displayName: 'gpt-5-mini', capabilities: ['llm'], contextLength: 272000, isMultimodal: true },
    ]);
    expect(await screen.findByText('1 model available. Search, or type any model name.')).toBeTruthy();

    expect(screen.getByRole('switch', { name: 'Multimodal' }).getAttribute('aria-checked')).toBe('false');
    expect((screen.getByPlaceholderText('e.g. 128000') as HTMLInputElement).value).toBe('16000');
  });

  it('does not keep a search as a model for a search provider between searches', async () => {
    api.discoverModels.mockResolvedValue({ success: true, supported: true, models: [] });
    const searching = {
      ...MANUAL_PROVIDER,
      providerId: 'huggingFace',
      discovery: { mode: 'search', requiredFields: [] },
    } as unknown as AIModelProvider;
    renderDialog(vi.fn(), vi.fn(), { provider: searching });
    await waitFor(() => expect(api.discoverModels).toHaveBeenCalledTimes(1));

    const input = screen.getByRole('combobox', { name: 'model' });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'bge' } });
    fireEvent.blur(input);

    expect(screen.queryByRole('button', { name: 'Remove bge' })).toBeNull();
  });
});

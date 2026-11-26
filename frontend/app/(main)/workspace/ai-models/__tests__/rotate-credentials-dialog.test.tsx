import '@/lib/__tests__/test-i18n';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { processError } from '@/lib/api/api-error';
import type { ConfiguredModel } from '../types';

const api = vi.hoisted(() => ({ rotateConnectionCredentials: vi.fn() }));
vi.mock('../api', () => ({ AIModelsApi: api }));

import { RotateCredentialsDialog } from '../components/rotate-credentials-dialog';

const model: ConfiguredModel = {
  modelKey: 'm1',
  provider: 'openAI',
  modelType: 'llm',
  configuration: {},
  isDefault: false,
  connectionId: 'conn-1',
  configuredFields: [],
};

function http400(data: unknown) {
  return processError(
    new AxiosError(
      'Request failed with status code 400',
      'ERR_BAD_REQUEST',
      { headers: {} } as InternalAxiosRequestConfig,
      {},
      { status: 400, data, statusText: 'Bad Request', headers: {}, config: {} } as AxiosResponse,
    ),
  );
}

async function rotate() {
  render(
    <Theme>
      <RotateCredentialsDialog model={model} onClose={vi.fn()} onSaved={vi.fn()} />
    </Theme>,
  );
  fireEvent.change(screen.getByPlaceholderText('New API key'), { target: { value: 'sk-new' } });
  fireEvent.click(screen.getByRole('button', { name: 'Update key' }));
}

describe('rotate credentials dialog', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it('names the models on another endpoint when the server refuses the key', async () => {
    api.rotateConnectionCredentials.mockRejectedValue(
      http400({
        status: 'error',
        error_code: 'credential_reentry_required',
        message: 'server text',
        models: [
          { modelKey: 'a', modelType: 'llm', name: 'gpt-4o' },
          { modelKey: 'b', modelType: 'llm', name: 'gpt-4o-mini' },
        ],
        reference: { modelKey: 'c', modelType: 'llm', name: 'gpt-5' },
      }),
    );
    await rotate();
    expect(
      await screen.findByText(
        "These models in the connection use a different endpoint from gpt-5: gpt-4o, gpt-4o-mini. Put every model in the connection on one endpoint, then try again.",
      ),
    ).toBeTruthy();
  });

  it('shows the server message for any other refusal', async () => {
    api.rotateConnectionCredentials.mockRejectedValue(
      http400({ status: 'error', message: 'Incorrect API key provided' }),
    );
    await rotate();
    expect(await screen.findByText('Incorrect API key provided')).toBeTruthy();
  });
});

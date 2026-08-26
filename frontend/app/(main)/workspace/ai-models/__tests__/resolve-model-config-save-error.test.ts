import { describe, it, expect } from 'vitest';
import type { TFunction } from 'i18next';
import { ErrorType, type ProcessedError } from '@/lib/api/api-error';
import {
  extractErrorCode,
  resolveModelConfigSaveError,
} from '../resolve-model-config-save-error';

const t = ((key: string) => key) as TFunction;

function processed(partial: Partial<ProcessedError> & Pick<ProcessedError, 'type' | 'message'>): ProcessedError {
  return partial;
}

describe('extractErrorCode', () => {
  it('reads a top-level error_code', () => {
    expect(extractErrorCode({ error_code: 'outbound_connectivity' })).toBe(
      'outbound_connectivity'
    );
  });

  it('walks the Node proxy wrap { error: { details: pythonBody } }', () => {
    const nodeBody = {
      error: {
        message: 'Cannot reach cloud LLM providers from PipesHub.',
        details: {
          status: 'error',
          message: 'Cannot reach cloud LLM providers from PipesHub.',
          details: { error_code: 'outbound_connectivity', provider: 'gemini' },
        },
      },
    };
    expect(extractErrorCode(nodeBody)).toBe('outbound_connectivity');
  });
});

describe('resolveModelConfigSaveError', () => {
  it('uses the outbound i18n key when outbound_connectivity is nested on the axios body', () => {
    const pythonMessage = 'Cannot reach cloud LLM providers from PipesHub.';
    const err = processed({
      type: ErrorType.SERVER_ERROR,
      message: pythonMessage,
      originalError: {
        name: 'AxiosError',
        message: 'Request failed',
        response: {
          data: {
            error: {
              message: pythonMessage,
              details: {
                details: { error_code: 'outbound_connectivity' },
              },
            },
          },
        },
      } as ProcessedError['originalError'],
    });
    expect(resolveModelConfigSaveError(err, t)).toBe(
      'workspace.aiModels.configSaveOutboundError'
    );
  });

  it('uses the outbound i18n key when error_code is on processed details', () => {
    const err = processed({
      type: ErrorType.SERVER_ERROR,
      message: '   ',
      details: { error_code: 'outbound_connectivity' },
    });
    expect(resolveModelConfigSaveError(err, t)).toBe(
      'workspace.aiModels.configSaveOutboundError'
    );
  });

  it('maps timeout errors to the timeout i18n key', () => {
    const err = processed({
      type: ErrorType.TIMEOUT_ERROR,
      message: 'Request timed out. Please try again.',
    });
    expect(resolveModelConfigSaveError(err, t)).toBe(
      'workspace.aiModels.configSaveTimeoutError'
    );
  });

  it('maps health_check_timeout to the timeout i18n key', () => {
    const err = processed({
      type: ErrorType.SERVER_ERROR,
      message: 'LLM health check timed out. For cloud providers, verify your API key',
      details: { error_code: 'health_check_timeout' },
    });
    expect(resolveModelConfigSaveError(err, t)).toBe(
      'workspace.aiModels.configSaveTimeoutError'
    );
  });

  it('uses the processed message for other API errors', () => {
    const err = processed({
      type: ErrorType.SERVER_ERROR,
      message: 'Incorrect API key provided',
    });
    expect(resolveModelConfigSaveError(err, t)).toBe('Incorrect API key provided');
  });

  it('uses the fallback key for unknown errors', () => {
    expect(resolveModelConfigSaveError(new Error('boom'), t)).toBe(
      'workspace.aiModels.configSaveErrorFallback'
    );
  });
});

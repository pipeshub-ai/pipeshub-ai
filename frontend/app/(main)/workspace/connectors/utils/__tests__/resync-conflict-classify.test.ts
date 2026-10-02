import { describe, it, expect } from 'vitest';
import { AxiosError } from 'axios';
import { ErrorType, type ProcessedError } from '@/lib/api/api-error';
import { classifyResyncConflict } from '../connector-sync-actions';

function conflictError(
  code: string,
  message = 'A sync is already in progress for this connector.'
): ProcessedError {
  const axiosErr = {
    isAxiosError: true,
    response: {
      status: 409,
      data: { error: { code, message } },
    },
  } as AxiosError<{ error?: { code?: string; message?: string } }>;

  return {
    type: ErrorType.CONFLICT,
    message,
    statusCode: 409,
    originalError: axiosErr,
  };
}

describe('classifyResyncConflict', () => {
  it('maps CONNECTOR_SYNC_IN_PROGRESS to a restartable conflict', () => {
    expect(
      classifyResyncConflict(conflictError('HTTP_CONNECTOR_SYNC_IN_PROGRESS'))
    ).toBe('restartable');
  });

  it('maps CONNECTOR_SYNC_LOCKED to a wait/locked conflict', () => {
    expect(
      classifyResyncConflict(
        conflictError(
          'HTTP_CONNECTOR_SYNC_LOCKED',
          'A full sync is in progress. Please wait and try again.'
        )
      )
    ).toBe('locked');
  });

  it('treats legacy plain CONFLICT as locked (old Node gate)', () => {
    expect(
      classifyResyncConflict(
        conflictError(
          'HTTP_CONFLICT',
          'A full sync is in progress. Please wait and try again.'
        )
      )
    ).toBe('locked');
  });

  it('infers restartable from message when the code is missing', () => {
    const err: ProcessedError = {
      type: ErrorType.CONFLICT,
      message: 'A full sync is already in progress for this connector.',
      statusCode: 409,
      originalError: {
        isAxiosError: true,
        response: { status: 409, data: {} },
      } as AxiosError,
    };
    expect(classifyResyncConflict(err)).toBe('restartable');
  });

  it('ignores non-409 errors', () => {
    expect(
      classifyResyncConflict({
        type: ErrorType.SERVER_ERROR,
        message: 'boom',
        statusCode: 500,
      })
    ).toBeNull();
  });
});
